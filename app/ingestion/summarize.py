from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from sqlalchemy.orm import Session

from app import repository

logger = logging.getLogger(__name__)

# Haiku is deliberately the only model used here: this is a high-volume,
# latency-insensitive summarization task (hundreds of short articles/day
# across sources), not a reasoning task, so the fastest/cheapest tier is the
# right fit. Submitted via the Batches API (50% off standard pricing) rather
# than inline during ingestion, since inline calls would add per-item latency
# to every scrape run.
MODEL = "claude-haiku-4-5"
MAX_SUMMARY_TOKENS = 200

PROMPT_TEMPLATE = (
    "Summarize the following tax-related article in 2-3 concise sentences for "
    "a tax professional audience. Focus on what changed and who it affects. "
    "The title and content below are all the information available -- there is "
    "no fuller article text to consult. Write the best concise summary you can "
    "from what's given, restating and lightly expanding on it in your own "
    "words. Never say you lack access to the full article, apologize for "
    "insufficient information, or ask for more content -- just summarize what "
    "is provided. Respond with only the summary text, no preamble or "
    "headers.\n\n"
    "Title: {title}\n\n"
    "Content: {content}"
)


@dataclass
class SummaryCandidate:
    id: int
    title: str
    content: str | None


def build_batch_requests(candidates: list[SummaryCandidate]) -> list[Request]:
    return [
        Request(
            custom_id=str(candidate.id),
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=MAX_SUMMARY_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": PROMPT_TEMPLATE.format(
                            title=candidate.title,
                            content=candidate.content or "(no summary/description available)",
                        ),
                    }
                ],
            ),
        )
        for candidate in candidates
    ]


def submit_batch(client: anthropic.Anthropic, candidates: list[SummaryCandidate]) -> str:
    batch = client.messages.batches.create(requests=build_batch_requests(candidates))
    return batch.id


def wait_for_batch(
    client: anthropic.Anthropic,
    batch_id: str,
    *,
    poll_interval_seconds: float = 15,
    max_wait_seconds: float = 600,
) -> bool:
    """Blocks until the batch ends or `max_wait_seconds` elapses.

    Returns True if the batch ended within the window, False on timeout --
    the batch itself keeps running server-side either way; a timeout just
    means this run won't see the results (a later run will, once the
    staleness window in `repository.list_publications_needing_summary` lets
    those items be reconsidered).
    """
    deadline = time.monotonic() + max_wait_seconds
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval_seconds)


def collect_batch_summaries(client: anthropic.Anthropic, batch_id: str) -> dict[int, str]:
    """Returns {publication_id: summary_text} for every successfully summarized item."""
    summaries: dict[int, str] = {}
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            logger.warning(
                "Publication %s summary batch entry did not succeed: %s",
                result.custom_id,
                result.result.type,
            )
            continue
        message = result.result.message
        text = next((block.text for block in message.content if block.type == "text"), None)
        if text:
            summaries[int(result.custom_id)] = text.strip()
    return summaries


def run_pending_summaries(
    db: Session,
    client: anthropic.Anthropic,
    *,
    batch_size: int = 500,
    stale_after: dt.timedelta = dt.timedelta(hours=2),
    poll_interval_seconds: float = 15,
    max_wait_seconds: float = 600,
) -> int:
    """Submits one batch covering every publication still missing an AI summary.

    Returns the number of publications updated. Blocks the calling thread for
    up to `max_wait_seconds` -- fine for a background scheduler job on its
    own interval, not appropriate to call from a request handler.
    """
    stale_before = dt.datetime.now(dt.UTC) - stale_after
    publications = repository.list_publications_needing_summary(
        db, limit=batch_size, stale_before=stale_before
    )
    if not publications:
        return 0

    candidates = [
        SummaryCandidate(id=pub.id, title=pub.title, content=pub.summary) for pub in publications
    ]

    now = dt.datetime.now(dt.UTC)
    for publication in publications:
        publication.ai_summary_requested_at = now
    db.commit()

    batch_id = submit_batch(client, candidates)
    logger.info("Submitted publication summary batch %s (%d items)", batch_id, len(candidates))

    if not wait_for_batch(
        client,
        batch_id,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    ):
        logger.warning(
            "Publication summary batch %s did not finish within %ss; "
            "unresolved items will be retried after the staleness window",
            batch_id,
            max_wait_seconds,
        )
        return 0

    summaries = collect_batch_summaries(client, batch_id)
    publications_by_id = {pub.id: pub for pub in publications}
    for publication_id, summary in summaries.items():
        publication = publications_by_id.get(publication_id)
        if publication is not None:
            publication.ai_summary = summary
    db.commit()

    logger.info(
        "Publication summary batch %s: %d/%d summarized", batch_id, len(summaries), len(candidates)
    )
    return len(summaries)

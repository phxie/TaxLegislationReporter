from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from sqlalchemy.orm import Session

from app import repository
from app.models import Bill, Publication

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

BILL_PROMPT_TEMPLATE = (
    "Summarize the following piece of tax legislation in 2-3 concise sentences "
    "for a tax professional audience. Focus on what the bill would change and "
    "who it affects. The title and content below are all the information "
    "available -- there is no fuller bill text to consult. Write the best "
    "concise summary you can from what's given, restating and lightly "
    "expanding on it in your own words. Never say you lack access to the full "
    "bill text, apologize for insufficient information, or ask for more "
    "content -- just summarize what is provided. Respond with only the "
    "summary text, no preamble or headers.\n\n"
    "Title: {title}\n\n"
    "Content: {content}"
)


@dataclass
class SummaryCandidate:
    id: int
    title: str
    content: str | None


def build_batch_requests(
    candidates: list[SummaryCandidate], *, prompt_template: str = PROMPT_TEMPLATE
) -> list[Request]:
    return [
        Request(
            custom_id=str(candidate.id),
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=MAX_SUMMARY_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": prompt_template.format(
                            title=candidate.title,
                            content=candidate.content or "(no summary/description available)",
                        ),
                    }
                ],
            ),
        )
        for candidate in candidates
    ]


def submit_batch(
    client: anthropic.Anthropic,
    candidates: list[SummaryCandidate],
    *,
    prompt_template: str = PROMPT_TEMPLATE,
) -> str:
    requests = build_batch_requests(candidates, prompt_template=prompt_template)
    batch = client.messages.batches.create(requests=requests)
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
                "Summary batch entry %s did not succeed: %s",
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
    return _run_summary_batch(
        db,
        client,
        publications,
        entity_label="publication",
        prompt_template=PROMPT_TEMPLATE,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    )


def run_pending_bill_summaries(
    db: Session,
    client: anthropic.Anthropic,
    *,
    batch_size: int = 500,
    stale_after: dt.timedelta = dt.timedelta(hours=2),
    poll_interval_seconds: float = 15,
    max_wait_seconds: float = 600,
) -> int:
    """Submits one batch covering every bill still missing an AI summary.

    Same mechanism as `run_pending_summaries`, just for `Bill` rows and with
    bill-flavored prompt wording -- see `_run_summary_batch`.
    """
    stale_before = dt.datetime.now(dt.UTC) - stale_after
    bills = repository.list_bills_needing_summary(db, limit=batch_size, stale_before=stale_before)
    return _run_summary_batch(
        db,
        client,
        bills,
        entity_label="bill",
        prompt_template=BILL_PROMPT_TEMPLATE,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    )


def _run_summary_batch(
    db: Session,
    client: anthropic.Anthropic,
    items: Sequence[Publication] | Sequence[Bill],
    *,
    entity_label: str,
    prompt_template: str,
    poll_interval_seconds: float,
    max_wait_seconds: float,
) -> int:
    """Shared batch-submit/poll/write-back flow behind `run_pending_summaries`
    and `run_pending_bill_summaries` -- both `Publication` and `Bill` expose
    the same `id`/`title`/`summary`/`ai_summary`/`ai_summary_requested_at`
    shape, so the flow itself doesn't need to know which one it's given.
    """
    if not items:
        return 0

    candidates = [SummaryCandidate(id=item.id, title=item.title, content=item.summary) for item in items]

    now = dt.datetime.now(dt.UTC)
    for item in items:
        item.ai_summary_requested_at = now
    db.commit()

    batch_id = submit_batch(client, candidates, prompt_template=prompt_template)
    logger.info("Submitted %s summary batch %s (%d items)", entity_label, batch_id, len(candidates))

    if not wait_for_batch(
        client,
        batch_id,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    ):
        logger.warning(
            "%s summary batch %s did not finish within %ss; "
            "unresolved items will be retried after the staleness window",
            entity_label.capitalize(),
            batch_id,
            max_wait_seconds,
        )
        return 0

    summaries = collect_batch_summaries(client, batch_id)
    items_by_id = {item.id: item for item in items}
    for item_id, summary in summaries.items():
        item = items_by_id.get(item_id)
        if item is not None:
            item.ai_summary = summary
    db.commit()

    logger.info(
        "%s summary batch %s: %d/%d summarized",
        entity_label.capitalize(),
        batch_id,
        len(summaries),
        len(candidates),
    )
    return len(summaries)

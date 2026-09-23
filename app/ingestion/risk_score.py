from __future__ import annotations

import datetime as dt
import json
import logging

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from sqlalchemy.orm import Session

from app import repository
from app.ingestion.summarize import wait_for_batch
from app.models import Bill

logger = logging.getLogger(__name__)

# Same model choice as summarize.py, for the same reason: a high-volume,
# latency-insensitive classification task, submitted via the Batches API.
MODEL = "claude-haiku-4-5"
MAX_RISK_SCORE_TOKENS = 50

RISK_PROMPT_TEMPLATE = (
    "You are assessing tax legislation for a tax professional audience. Rate how "
    "much impact the following bill would have if enacted, as a single integer "
    '"risk score" from 0 (negligible impact -- e.g. a technical correction or a '
    "narrow administrative change) to 100 (sweeping impact -- e.g. a major change "
    "to tax rates, brackets, or rules affecting a broad population or large dollar "
    "amounts). Base the score on the breadth of taxpayers or businesses affected, "
    "the magnitude of the financial change, and the complexity of compliance the "
    "bill would introduce. The title and content below are all the information "
    "available -- there is no fuller bill text to consult. Respond with ONLY a "
    'JSON object of the exact form {{"risk_score": <integer 0-100>}} -- no '
    "preamble, no explanation, no markdown fencing.\n\n"
    "Title: {title}\n\n"
    "Content: {content}"
)


def build_batch_requests(bills: list[Bill]) -> list[Request]:
    return [
        Request(
            custom_id=str(bill.id),
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=MAX_RISK_SCORE_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": RISK_PROMPT_TEMPLATE.format(
                            title=bill.title,
                            content=bill.ai_summary
                            or bill.summary
                            or "(no summary/description available)",
                        ),
                    }
                ],
            ),
        )
        for bill in bills
    ]


def submit_batch(client: anthropic.Anthropic, bills: list[Bill]) -> str:
    batch = client.messages.batches.create(requests=build_batch_requests(bills))
    return batch.id


def _parse_risk_score(text: str) -> int | None:
    try:
        score = int(json.loads(text.strip())["risk_score"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return max(0, min(100, score))


def collect_batch_risk_scores(client: anthropic.Anthropic, batch_id: str) -> dict[int, int]:
    """Returns {bill_id: risk_score} for every successfully scored item."""
    scores: dict[int, int] = {}
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            logger.warning(
                "Risk score batch entry %s did not succeed: %s",
                result.custom_id,
                result.result.type,
            )
            continue
        message = result.result.message
        text = next((block.text for block in message.content if block.type == "text"), None)
        score = _parse_risk_score(text) if text else None
        if score is None:
            logger.warning("Could not parse risk score for bill %s: %r", result.custom_id, text)
            continue
        scores[int(result.custom_id)] = score
    return scores


def run_pending_bill_risk_scores(
    db: Session,
    client: anthropic.Anthropic,
    *,
    batch_size: int = 500,
    stale_after: dt.timedelta = dt.timedelta(hours=2),
    poll_interval_seconds: float = 15,
    max_wait_seconds: float = 600,
) -> int:
    """Submits one batch covering every bill still missing a risk score.

    Same mechanism as `summarize.run_pending_bill_summaries`: blocks the
    calling thread for up to `max_wait_seconds`, fine for a background
    scheduler job on its own interval.
    """
    stale_before = dt.datetime.now(dt.UTC) - stale_after
    bills = repository.list_bills_needing_risk_score(db, limit=batch_size, stale_before=stale_before)
    if not bills:
        return 0

    now = dt.datetime.now(dt.UTC)
    for bill in bills:
        bill.risk_score_requested_at = now
    db.commit()

    batch_id = submit_batch(client, bills)
    logger.info("Submitted bill risk-score batch %s (%d items)", batch_id, len(bills))

    if not wait_for_batch(
        client,
        batch_id,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    ):
        logger.warning(
            "Bill risk-score batch %s did not finish within %ss; "
            "unresolved items will be retried after the staleness window",
            batch_id,
            max_wait_seconds,
        )
        return 0

    scores = collect_batch_risk_scores(client, batch_id)
    bills_by_id = {bill.id: bill for bill in bills}
    for bill_id, score in scores.items():
        bill = bills_by_id.get(bill_id)
        if bill is not None:
            bill.risk_score = score
    db.commit()

    logger.info("Bill risk-score batch %s: %d/%d scored", batch_id, len(scores), len(bills))
    return len(scores)

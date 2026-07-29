from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_ny

logger = logging.getLogger(__name__)

PAGE_LIMIT = 1000
DEFAULT_BACKFILL = dt.timedelta(days=1)


class NewYorkSenateAdapter:
    source_name = "NY"

    def __init__(self, api_key: str, base_url: str = "https://legislation.nysenate.gov/api/3"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _get(self, path: str, **params: object) -> dict:
        params.setdefault("key", self.api_key)
        resp = self._client.get(f"{self.base_url}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        since = since or (dt.datetime.now(dt.UTC) - DEFAULT_BACKFILL)
        from_dt = since.strftime("%Y-%m-%dT%H:%M:%S")

        offset = 1
        seen: set[tuple[str, int]] = set()
        while True:
            data = self._get(
                f"/bills/updates/{from_dt}",
                type="processed",
                limit=PAGE_LIMIT,
                offset=offset,
            )
            items = (data.get("result") or {}).get("items", [])
            if not items:
                break

            for item in items:
                bill_id = item.get("id", {})
                print_no = bill_id.get("basePrintNo")
                session = bill_id.get("session")
                if not print_no or session is None:
                    continue
                key = (print_no, session)
                if key in seen:
                    continue
                seen.add(key)

                try:
                    normalized = self._normalize_bill(print_no, session)
                except httpx.HTTPError:
                    logger.warning("Skipping NY bill %s-%s due to a request failure", print_no, session)
                    continue
                if normalized is not None:
                    yield normalized

            total = data.get("total", 0)
            offset_end = data.get("offsetEnd", 0)
            if not offset_end or offset_end >= total:
                break
            offset = offset_end + 1

    def _normalize_bill(self, print_no: str, session: int) -> NormalizedBill | None:
        detail = (self._get(f"/bills/{session}/{print_no}") or {}).get("result")
        if not detail:
            return None

        title = detail.get("title", "")
        summary = detail.get("summary")
        status = detail.get("status") or {}
        committee = status.get("committeeName")

        is_relevant, matched = is_tax_relevant_ny(title, summary, committee)
        if not is_relevant:
            return None

        sponsor_member = (detail.get("sponsor") or {}).get("member") or {}
        sponsors = [sponsor_member["fullName"]] if sponsor_member.get("fullName") else []

        last_action_date = _parse_date(status.get("actionDate"))
        introduced_date = _parse_date(detail.get("publishedDateTime"))

        status_events = []
        for action in (detail.get("actions") or {}).get("items", []):
            action_date = _parse_date(action.get("date"))
            if action_date is None:
                continue
            status_events.append(
                NormalizedStatusEvent(
                    event_date=action_date,
                    action_text=action.get("text", ""),
                    action_code=None,
                    sequence_num=action.get("sequenceNo"),
                )
            )

        source_url = f"https://www.nysenate.gov/legislation/bills/{session}/{print_no}"

        return NormalizedBill(
            jurisdiction="NY",
            source_bill_id=print_no,
            session=str(session),
            bill_number=print_no,
            title=title,
            source_label="NY Senate Open Legislation API",
            summary=summary,
            sponsors=sponsors,
            status_text=status.get("statusDesc"),
            status_code=status.get("statusType"),
            last_action_date=last_action_date,
            introduced_date=introduced_date,
            full_text_url=None,
            source_url=source_url,
            tax_keywords_matched=matched,
            raw_source_payload={"detail": detail},
            status_events=status_events,
        )


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None

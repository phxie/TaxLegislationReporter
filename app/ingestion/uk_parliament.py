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
from app.ingestion.tax_filter import is_tax_relevant_uk

logger = logging.getLogger(__name__)

# The UK Parliament Bills API (bills-api.parliament.uk) is an official,
# documented, unauthenticated REST API -- no scraping needed. Confirmed via
# its own OpenAPI spec (GET /swagger/v1/swagger.json).
PAGE_SIZE = 50


class UkParliamentAdapter:
    source_name = "UK"
    source_label = "UK Parliament Bills"

    def __init__(self, base_url: str = "https://bills-api.parliament.uk"):
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
        resp = self._client.get(f"{self.base_url}/api/v1{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _fetch_current_session_id(self) -> int:
        # The API has no "current session" endpoint of its own -- the
        # session of the most-recently-updated bill is a reliable proxy,
        # since a session stays open (and keeps getting bill updates) until
        # prorogation. This keeps the adapter correct across session
        # transitions without a hardcoded session ID to maintain (unlike
        # Spain's `legislature` setting, which has no equivalent signal).
        data = self._get("/Bills", SortOrder="DateUpdatedDescending", Take=1)
        items = data.get("items") or []
        if not items:
            raise RuntimeError("UK Parliament Bills API returned no bills at all")
        return items[0]["introducedSessionId"]

    def _fetch_bills_page(self, session_id: int, skip: int) -> list[dict]:
        data = self._get("/Bills", Session=session_id, Skip=skip, Take=PAGE_SIZE)
        return data.get("items") or []

    def _fetch_bill_detail(self, bill_id: int) -> dict:
        return self._get(f"/Bills/{bill_id}")

    def _fetch_bill_stages(self, bill_id: int) -> list[dict]:
        data = self._get(f"/Bills/{bill_id}/Stages")
        return data.get("items") or []

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # No incremental "changed since" filter is exposed, and a single
        # session is small (a couple hundred bills) -- like Spain/PwC, every
        # run re-pulls the current session in full and the diff pipeline
        # detects what's actually new via (jurisdiction, source_bill_id, session).
        #
        # The Stages endpoint is only fetched for bills that already pass the
        # relevance check on `detail` -- this API is unusually slow per
        # request (observed ~10-15s), and only a small fraction of a
        # session's bills are tax-relevant, so skipping the second request
        # for the rest roughly halves total run time.
        session_id = self._fetch_current_session_id()
        skip = 0
        while True:
            items = self._fetch_bills_page(session_id, skip)
            if not items:
                break

            for item in items:
                bill_id = item.get("billId")
                if bill_id is None:
                    continue
                try:
                    detail = self._fetch_bill_detail(bill_id)
                except httpx.HTTPError:
                    logger.warning("Skipping UK bill %s due to a request failure", bill_id)
                    continue

                title = detail.get("shortTitle")
                if not title:
                    logger.warning("Skipping UK bill missing shortTitle: %s", detail)
                    continue
                summary = detail.get("summary") or detail.get("longTitle")
                is_relevant, matched = is_tax_relevant_uk(title, summary)
                if not is_relevant:
                    continue

                try:
                    stages = self._fetch_bill_stages(bill_id)
                except httpx.HTTPError:
                    logger.warning("Skipping UK bill %s due to a request failure", bill_id)
                    continue

                normalized = self._normalize(detail, stages, matched)
                if normalized is not None:
                    yield normalized

            if len(items) < PAGE_SIZE:
                break
            skip += PAGE_SIZE

    def _normalize(
        self, detail: dict, stages: list[dict], matched: list[str]
    ) -> NormalizedBill | None:
        bill_id = detail.get("billId")
        title = detail.get("shortTitle")
        if bill_id is None or not title:
            logger.warning("Skipping UK bill missing billId/shortTitle: %s", detail)
            return None

        summary = detail.get("summary") or detail.get("longTitle")

        sponsors = [
            sponsor["member"]["name"]
            for sponsor in detail.get("sponsors") or []
            if sponsor.get("member") and sponsor["member"].get("name")
        ]

        status_events = _extract_status_events(stages)
        introduced_date = min((event.event_date for event in status_events), default=None)
        last_action_date = max((event.event_date for event in status_events), default=None)
        if last_action_date is None:
            last_action_date = _parse_date(detail.get("lastUpdate"))

        current_stage = detail.get("currentStage") or {}
        source_bill_id = str(bill_id)

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=source_bill_id,
            session=str(detail.get("introducedSessionId")),
            bill_number=source_bill_id,
            title=title,
            source_label=self.source_label,
            summary=summary,
            sponsors=sponsors,
            status_text=current_stage.get("description"),
            status_code=current_stage.get("abbreviation"),
            last_action_date=last_action_date,
            introduced_date=introduced_date,
            source_url=f"https://bills.parliament.uk/bills/{bill_id}",
            tax_keywords_matched=matched,
            raw_source_payload={"detail": detail, "stages": stages},
            status_events=status_events,
        )


def _extract_status_events(stages: list[dict]) -> list[NormalizedStatusEvent]:
    events: list[NormalizedStatusEvent] = []
    for stage in stages:
        description = stage.get("description")
        house = stage.get("house")
        action_text = f"{description} ({house})" if description and house else description
        if not action_text:
            continue
        for sitting in stage.get("stageSittings") or []:
            event_date = _parse_date(sitting.get("date"))
            if event_date is None:
                continue
            events.append(
                NormalizedStatusEvent(
                    event_date=event_date,
                    action_text=action_text,
                    action_code=stage.get("abbreviation"),
                    sequence_num=stage.get("sortOrder"),
                )
            )
    return events


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        return None

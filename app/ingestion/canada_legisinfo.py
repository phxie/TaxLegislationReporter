from __future__ import annotations

import datetime as dt
import html
import logging
import re
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_canada

logger = logging.getLogger(__name__)

# LEGISinfo (Parliament of Canada's legislative information portal) publishes
# an official, undocumented-but-stable JSON export -- no API key, no
# scraping. The bills list only ever contains the current parliamentary
# session (confirmed by inspection: every entry shares one ParlSessionCode),
# so a full run re-pulls the whole list every time (small: ~200 bills), then
# only re-fetches per-bill detail for bills whose LatestActivityDateTime is
# newer than `since` -- avoiding ~200 detail requests on every run once the
# initial backfill is done.
BILLS_LIST_PATH = "/legisinfo/en/bills/json"
BILL_DETAIL_PATH = "/legisinfo/en/bill/{session}/{number_code}/json"
DOCUMENT_VIEWER_PATH = "/DocumentViewer/en/{session}/bill/{number_code}/first-reading"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")


class CanadaLegisinfoAdapter:
    source_name = "CANADA"
    source_label = "Parliament of Canada (LEGISinfo)"

    def __init__(self, base_url: str = "https://www.parl.ca"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0, headers={"User-Agent": _USER_AGENT})

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _fetch_bills_list(self) -> list[dict]:
        resp = self._client.get(f"{self.base_url}{BILLS_LIST_PATH}")
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _fetch_bill_detail(self, number_code: str, session_code: str) -> dict:
        path = BILL_DETAIL_PATH.format(session=session_code, number_code=number_code.lower())
        resp = self._client.get(f"{self.base_url}{path}")
        resp.raise_for_status()
        data = resp.json()
        return data[0]

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        for entry in self._fetch_bills_list():
            number_code = entry.get("BillNumberFormatted")
            session_code = entry.get("ParlSessionCode")
            if not number_code or not session_code:
                continue

            latest_activity = _parse_datetime(entry.get("LatestActivityDateTime"))
            if latest_activity is not None and latest_activity.tzinfo is None:
                # Comparable only if offset-aware, like `since` (an
                # IngestionRun.started_at) -- treat as "unknown" and always
                # re-fetch rather than risk a naive/aware comparison error.
                latest_activity = None
            if since is not None and latest_activity is not None and latest_activity <= since:
                continue

            try:
                detail = self._fetch_bill_detail(number_code, session_code)
            except httpx.HTTPError:
                logger.warning(
                    "Skipping Canada bill %s (%s) due to a request failure", number_code, session_code
                )
                continue

            normalized = self._normalize_bill(detail, session_code)
            if normalized is not None:
                yield normalized

    def _normalize_bill(self, detail: dict, session_code: str) -> NormalizedBill | None:
        number_code = detail.get("NumberCode")
        title = detail.get("ShortTitleEn") or detail.get("LongTitleEn")
        if not number_code or not title:
            logger.warning("Skipping Canada bill missing NumberCode/title: %s", detail)
            return None

        summary_html = detail.get("ShortLegislativeSummaryEn")
        summary = _strip_html(summary_html) if summary_html else None

        is_relevant, matched = is_tax_relevant_canada(title, summary)
        if not is_relevant:
            return None

        sponsor_name = detail.get("SponsorPersonName")
        sponsors = [sponsor_name] if sponsor_name else []

        status_id = detail.get("StatusId")
        status_events = _extract_status_events(detail.get("BillStages") or {})
        introduced_date = min((event.event_date for event in status_events), default=None)
        last_action_date = _parse_date(
            detail.get("LatestBillEventDateTime") or detail.get("LatestCompletedBillStageDateTime")
        )

        number_code_lower = number_code.lower()
        source_url = f"{self.base_url}/legisinfo/en/bill/{session_code}/{number_code_lower}"
        doc_viewer_path = DOCUMENT_VIEWER_PATH.format(session=session_code, number_code=number_code)
        full_text_url = f"{self.base_url}{doc_viewer_path}"

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=number_code,
            session=session_code,
            bill_number=number_code,
            title=title,
            source_label=self.source_label,
            summary=summary,
            sponsors=sponsors,
            status_text=detail.get("StatusNameEn"),
            status_code=str(status_id) if status_id is not None else None,
            last_action_date=last_action_date,
            introduced_date=introduced_date,
            full_text_url=full_text_url,
            source_url=source_url,
            tax_keywords_matched=matched,
            raw_source_payload=detail,
            status_events=status_events,
        )


def _extract_status_events(bill_stages: dict) -> list[NormalizedStatusEvent]:
    events: list[NormalizedStatusEvent] = []
    for group_key in ("HouseBillStages", "SenateBillStages", "RoyalAssent"):
        for stage in bill_stages.get(group_key) or []:
            for significant_event in stage.get("SignificantEvents") or []:
                event_date = _parse_date(significant_event.get("EventDateTime"))
                action_text = significant_event.get("EventNameEn")
                if event_date is None or not action_text:
                    continue
                event_type_id = significant_event.get("EventTypeId")
                events.append(
                    NormalizedStatusEvent(
                        event_date=event_date,
                        action_text=action_text,
                        action_code=str(event_type_id) if event_type_id is not None else None,
                    )
                )
    return events


def _strip_html(value: str) -> str:
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: str | None) -> dt.date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None

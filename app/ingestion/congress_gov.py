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
from app.ingestion.tax_filter import is_tax_relevant_federal

logger = logging.getLogger(__name__)

PAGE_LIMIT = 250
DEFAULT_BACKFILL = dt.timedelta(days=1)


class CongressGovAdapter:
    source_name = "FEDERAL"

    def __init__(self, api_key: str, base_url: str = "https://api.congress.gov/v3"):
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
        params["api_key"] = self.api_key
        params.setdefault("format", "json")
        resp = self._client.get(f"{self.base_url}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        since = since or (dt.datetime.now(dt.UTC) - DEFAULT_BACKFILL)
        from_dt = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        offset = 0
        while True:
            data = self._get(
                "/bill",
                fromDateTime=from_dt,
                sort="updateDate+asc",
                limit=PAGE_LIMIT,
                offset=offset,
            )
            items = data.get("bills", [])
            if not items:
                break

            for item in items:
                try:
                    normalized = self._normalize_candidate(item)
                except httpx.HTTPError:
                    logger.warning("Skipping bill %s due to a request failure", item.get("number"))
                    continue
                if normalized is not None:
                    yield normalized

            if len(items) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT

    def _normalize_candidate(self, list_item: dict) -> NormalizedBill | None:
        congress = list_item["congress"]
        bill_type = list_item["type"].lower()
        number = list_item["number"]

        subjects_data = self._get(f"/bill/{congress}/{bill_type}/{number}/subjects")
        subject_container = subjects_data.get("subjects", {})
        policy_area = (subject_container.get("policyArea") or {}).get("name")
        legislative_subjects = [
            s.get("name", "") for s in subject_container.get("legislativeSubjects", [])
        ]

        title = list_item.get("title", "")
        is_relevant, matched = is_tax_relevant_federal(policy_area, legislative_subjects, title, None)
        if not is_relevant:
            return None

        detail = self._get(f"/bill/{congress}/{bill_type}/{number}").get("bill", {})
        actions_data = self._get(f"/bill/{congress}/{bill_type}/{number}/actions")

        sponsors = [s.get("fullName", "") for s in detail.get("sponsors", [])]

        latest_action = list_item.get("latestAction", {})
        last_action_date = _parse_date(latest_action.get("actionDate"))
        introduced_date = _parse_date(detail.get("introducedDate"))

        status_events = []
        for idx, action in enumerate(actions_data.get("actions", [])):
            action_date = _parse_date(action.get("actionDate"))
            if action_date is None:
                continue
            status_events.append(
                NormalizedStatusEvent(
                    event_date=action_date,
                    action_text=action.get("text", ""),
                    action_code=action.get("actionCode"),
                    sequence_num=idx,
                )
            )

        text_versions = detail.get("textVersions")
        full_text_url = text_versions.get("url") if isinstance(text_versions, dict) else None

        return NormalizedBill(
            jurisdiction="FEDERAL",
            source_bill_id=f"{bill_type}{number}",
            session=str(congress),
            bill_number=f"{bill_type.upper()} {number}",
            title=title,
            source_label="Congress.gov",
            summary=None,
            sponsors=sponsors,
            status_text=latest_action.get("text"),
            status_code=None,
            last_action_date=last_action_date,
            introduced_date=introduced_date,
            full_text_url=full_text_url,
            source_url=list_item.get("url"),
            tax_keywords_matched=matched,
            raw_source_payload={"list_item": list_item, "detail": detail},
            status_events=status_events,
        )


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None

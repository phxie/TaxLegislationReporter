from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Iterator
from urllib.parse import quote

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_singapore

logger = logging.getLogger(__name__)

# The Bills Introduced page (parliament.gov.sg) is a Next.js App Router site
# with no documented API. Its first 10 results are server-rendered, but the
# pagination/page-size controls are wired to a React "Server Action"
# (`searchBillAction`) rather than a REST endpoint or URL query params --
# invoked as a POST back to the page URL with a `Next-Action: <id>` header
# and a multipart body, returning React Server Components' own wire format
# rather than plain JSON. Discovered with a throwaway Playwright spike to
# capture the browser's real request (this page's data isn't visible in any
# static HTML or plain network capture); confirmed afterwards that the whole
# flow -- including the ID itself -- is reproducible with plain httpx, so no
# headless browser is a runtime dependency here, same as the PwC adapter's
# validation-spike precedent.
#
# The `<id>` in `Next-Action` is a content hash tied to the current
# JS bundle, so it's rediscovered on every run rather than hardcoded: fetch
# the page, find its referenced JS chunk filenames, and scan them for the
# `createServerReference(<id>, ..., "searchBillAction")` call that names it.
# This is more fragile than every other undocumented-endpoint source in this
# project (PwC/EY/KPMG/Spain) since it's coupled to Next.js's minified build
# output shape, not just a stable URL -- if the site's framework or action
# name changes, discovery will raise loudly (see `_discover_action_id`)
# rather than silently returning nothing.
BILLS_PAGE_PATH = "/parliamentary-business/bills-introduced"
PAGE_SIZE = 100

_CHUNK_RE = re.compile(r'[0-9a-zA-Z]+-[a-f0-9]{16,20}\.js')
_ACTION_ID_RE = re.compile(
    r'createServerReference\)\(["\']([0-9a-f]{20,})["\'][^)]*["\']searchBillAction["\']\)'
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class SingaporeParliamentAdapter:
    source_name = "SINGAPORE"
    source_label = "Parliament of Singapore"

    def __init__(self, base_url: str = "https://www.parliament.gov.sg"):
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
    def _get(self, path: str) -> str:
        resp = self._client.get(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp.text

    def _discover_action_id(self) -> str:
        html = self._get(BILLS_PAGE_PATH)
        chunk_names = sorted(set(_CHUNK_RE.findall(html)))
        for name in chunk_names:
            js = self._get(f"/_next/static/chunks/{name}")
            match = _ACTION_ID_RE.search(js)
            if match:
                return match.group(1)
        raise RuntimeError(
            "Could not find Singapore Parliament's searchBillAction ID in any "
            "referenced JS chunk -- the site's build likely changed."
        )

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _fetch_page(self, action_id: str, offset: int, limit: int) -> dict:
        resp = self._client.post(
            f"{self.base_url}{BILLS_PAGE_PATH}",
            files={
                "1_name": (None, ""),
                "1_title": (None, ""),
                "1_yearOption": (None, ""),
                "1_offset": (None, str(offset)),
                "1_limit": (None, str(limit)),
                # Static RSC-protocol placeholder for this action's unused
                # first argument -- observed constant across every real
                # request captured, regardless of search params.
                "0": (None, '[null,"$K1"]'),
            },
            headers={"Accept": "text/x-component", "Next-Action": action_id},
        )
        resp.raise_for_status()
        body = resp.text
        marker = body.find("1:")
        if marker == -1:
            raise RuntimeError(
                "Singapore Parliament's search response didn't contain the expected "
                "RSC data line -- the site's response format likely changed."
            )
        return json.loads(body[marker + 2:])

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # No incremental "changed since" filter is exposed, and the total
        # dataset is small (a few hundred bills across ~20 years) -- like
        # Spain/UK/Germany, every run re-pulls the full set and the diff
        # pipeline detects what's actually new.
        action_id = self._discover_action_id()
        offset = 0
        while True:
            page = self._fetch_page(action_id, offset, PAGE_SIZE)
            items = page.get("data") or []
            if not items:
                break

            for item in items:
                normalized = self._normalize(item)
                if normalized is not None:
                    yield normalized

            offset += PAGE_SIZE
            total = (page.get("meta") or {}).get("filter_count", 0)
            if offset >= total:
                break

    def _normalize(self, item: dict) -> NormalizedBill | None:
        bill_no = item.get("title")
        title = item.get("description")
        source_bill_id = item.get("id")
        if not bill_no or not title or not source_bill_id:
            logger.warning("Skipping Singapore bill missing title/description/id: %s", item)
            return None

        is_relevant, matched = is_tax_relevant_singapore(title)
        if not is_relevant:
            return None

        session = bill_no.split("/")[-1].strip() if "/" in bill_no else None

        status_events = _extract_status_events(item)
        introduced_date = _parse_date(item.get("date_introduced"))
        last_action_date = max((e.event_date for e in status_events), default=introduced_date)
        status_text = status_events[-1].action_text if status_events else None

        full_text_url = None
        file_info = item.get("file") or {}
        file_id = file_info.get("id")
        filename = file_info.get("filename_download")
        if file_id and filename:
            full_text_url = f"{self.base_url}/api/media/{file_id}/{quote(filename)}"

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=source_bill_id,
            session=session or "unknown",
            bill_number=bill_no,
            title=title,
            source_label=self.source_label,
            sponsors=[],
            status_text=status_text,
            introduced_date=introduced_date,
            last_action_date=last_action_date,
            full_text_url=full_text_url,
            source_url=f"{self.base_url}{BILLS_PAGE_PATH}",
            tax_keywords_matched=matched,
            raw_source_payload=item,
            status_events=status_events,
        )


def _extract_status_events(item: dict) -> list[NormalizedStatusEvent]:
    events: list[NormalizedStatusEvent] = []

    introduced = _parse_date(item.get("date_introduced"))
    if introduced is not None:
        events.append(NormalizedStatusEvent(event_date=introduced, action_text="Introduced (1st Reading)"))

    # `date_of_second_reading` is sometimes a placeholder like "Next
    # Available Sitting" rather than an actual date -- `_parse_date` returns
    # None for that, so it's simply skipped rather than yielding a bogus event.
    second_reading = _parse_date(item.get("date_of_second_reading"))
    if second_reading is not None:
        events.append(NormalizedStatusEvent(event_date=second_reading, action_text="2nd Reading"))

    passed = _parse_date(item.get("date_passed"))
    if passed is not None:
        events.append(NormalizedStatusEvent(event_date=passed, action_text="Passed"))

    return sorted(events, key=lambda e: e.event_date)


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return dt.datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        return None

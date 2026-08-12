from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterator

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_india

logger = logging.getLogger(__name__)

# India has no official structured bill-tracking API of its own -- the
# unified Parliament portal (sansad.in) is a client-rendered SPA with no
# discoverable data endpoint. PRS Legislative Research is a well-established
# independent legislative research organization whose "Bills Track" page is
# a plain server-rendered Drupal "Views" listing (no JS needed) covering
# every bill before Lok Sabha/Rajya Sabha, with a real per-bill dated status
# timeline and PRS's own plain-English bill summary -- confirmed to work
# standalone via plain HTTP with no cookies/session required.
LIST_PATH = "/billtrack/category/all"
DETAIL_PATH = "/billtrack/{slug}"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


class IndiaPrsAdapter:
    source_name = "INDIA"
    source_label = "PRS Legislative Research"

    def __init__(self, base_url: str = "https://prsindia.org"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=30.0, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
        )

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _fetch_bill_list_html(self) -> str:
        resp = self._client.get(f"{self.base_url}{LIST_PATH}")
        resp.raise_for_status()
        return resp.text

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _fetch_bill_detail_html(self, slug: str) -> str:
        resp = self._client.get(f"{self.base_url}{DETAIL_PATH.format(slug=slug)}")
        resp.raise_for_status()
        return resp.text

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # No incremental "changed since" filter is exposed. The full listing
        # (~1,000 bills spanning many years) is re-pulled every run like
        # Spain/UK, but the (slower) per-bill detail page -- where the
        # status timeline and summary live -- is only fetched for bills that
        # already pass the relevance check on title alone, the same
        # optimization applied to the UK adapter after its first live run
        # showed fetching detail for every bill was unnecessarily slow.
        entries = _parse_bill_list(self._fetch_bill_list_html())
        if not entries:
            raise RuntimeError(
                "PRS Bills Track page returned zero bills -- the page's markup likely changed."
            )

        for entry in entries:
            title = entry.get("title")
            slug = entry.get("slug")
            if not title or not slug:
                continue

            is_relevant, _ = is_tax_relevant_india(title, None)
            if not is_relevant:
                continue

            try:
                detail_html = self._fetch_bill_detail_html(slug)
            except httpx.HTTPError:
                logger.warning("Skipping India bill %s due to a request failure", slug)
                continue

            normalized = self._normalize(entry, detail_html)
            if normalized is not None:
                yield normalized

    def _normalize(self, entry: dict, detail_html: str) -> NormalizedBill | None:
        title = entry["title"]
        slug = entry["slug"]
        detail = _parse_bill_detail(detail_html)

        # The title-only check in fetch_updates is just a cheap pre-filter to
        # decide whether detail is worth fetching at all -- this is the real
        # check, now with PRS's fuller summary text available too.
        is_relevant, matched = is_tax_relevant_india(title, detail.get("summary"))
        if not is_relevant:
            return None

        status_events = detail["status_events"]
        introduced_date = min((event.event_date for event in status_events), default=None)
        last_action_date = max((event.event_date for event in status_events), default=None)

        # No session/term identifier is exposed by this source -- titles
        # consistently end in the bill's year (e.g. "..., 2026"), so that
        # stands in as the session.
        years = _YEAR_RE.findall(title)
        session = years[-1] if years else "unknown"

        ministry = detail.get("ministry")
        sponsors = [f"Ministry of {ministry}"] if ministry else []

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=slug,
            session=session,
            bill_number=slug,
            title=title,
            source_label=self.source_label,
            summary=detail.get("summary"),
            sponsors=sponsors,
            status_text=entry.get("status"),
            last_action_date=last_action_date,
            introduced_date=introduced_date,
            source_url=f"{self.base_url}{DETAIL_PATH.format(slug=slug)}",
            tax_keywords_matched=matched,
            raw_source_payload={"list_entry": entry, "ministry": ministry},
            status_events=status_events,
        )


def _parse_bill_list(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    entries = []
    for row in soup.select(".views-row"):
        link = row.select_one(".views-field-title-field a")
        status_el = row.select_one(".views-field-field-bill-status")
        if link is None or not link.get("href"):
            continue
        href = link["href"]
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        entries.append(
            {
                "slug": slug,
                "title": link.get_text(strip=True),
                "status": status_el.get_text(strip=True) if status_el else None,
            }
        )
    return entries


def _parse_bill_detail(html_text: str) -> dict:
    soup = BeautifulSoup(html_text, "html.parser")

    ministry_el = soup.select_one(".field-name-field-ministry .field-item")
    ministry = ministry_el.get_text(strip=True) if ministry_el else None

    status_events: list[NormalizedStatusEvent] = []
    for block in soup.select(".field-collection-item-field-own-status-details"):
        status_el = block.select_one(".field-name-field-own-status .field-item")
        chamber_el = block.select_one(".field-name-field-own-status-title .field-item")
        date_el = block.select_one(".field-name-field-own-status-date .date-display-single")

        status_text = status_el.get_text(strip=True) if status_el else None
        chamber = chamber_el.get_text(strip=True) if chamber_el else None
        event_date = _parse_date(date_el.get_text(strip=True) if date_el else None)
        if not status_text or event_date is None:
            continue

        action_text = f"{status_text} ({chamber})" if chamber else status_text
        status_events.append(NormalizedStatusEvent(event_date=event_date, action_text=action_text))

    summary_el = soup.select_one(".field-name-body")
    summary = summary_el.get_text(" ", strip=True) if summary_el else None

    return {"ministry": ministry, "status_events": status_events, "summary": summary}


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%b %d, %Y").date()
    except ValueError:
        return None

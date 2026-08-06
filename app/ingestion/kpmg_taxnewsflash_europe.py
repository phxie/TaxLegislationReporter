from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterator

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.ingestion.publications_base import NormalizedPublication, PublicationScrapeError
from app.ingestion.tax_filter import matching_keywords

logger = logging.getLogger(__name__)

# The Europe TaxNewsFlash landing page renders one AEM "gridlist" component
# per month of history (~16 months back), each lazily fetching its own item
# JSON via a `data-fetch="..."` attribute already present in the page's
# static HTML -- no JS rendering needed to discover these, unlike PwC/EY.
# Confirmed to work standalone via plain HTTP with no cookies/session.
LANDING_PATH = "/us/en/taxnewsflash/europe.html"
_DATA_FETCH_RE = re.compile(r'data-fetch="([^"]+)"')
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Each month's gridlist paginates independently at 10 items/page; the
# busiest month observed so far needed ~11 pages. Capped generously above
# that as a safety net against a runaway/broken response, not a tight bound.
MAX_PAGES_PER_MONTH = 30

# KPMG titles its articles "Country: ..." (e.g. "Finland: New guidance...")
# with near-total consistency on this page, so -- unlike PwC, which has no
# such convention and needs a text-scanning heuristic -- jurisdiction here is
# extracted directly from the title prefix. Still best-effort: a handful of
# articles ("EU-Mercosur free trade agreement: ...") don't follow the
# convention and fall back to no jurisdiction.
_EUROPEAN_JURISDICTIONS = (
    "Albania", "Andorra", "Austria", "Belarus", "Belgium",
    "Bosnia and Herzegovina", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
    "Czechia", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece",
    "Hungary", "Iceland", "Ireland", "Italy", "Kosovo", "Latvia",
    "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Moldova", "Monaco",
    "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland",
    "Portugal", "Romania", "Russia", "San Marino", "Serbia", "Slovakia",
    "Slovenia", "Spain", "Sweden", "Switzerland", "Ukraine",
    "United Kingdom", "Vatican City",
)  # fmt: skip
_JURISDICTION_LABELS = {name.lower(): name for name in _EUROPEAN_JURISDICTIONS}
_JURISDICTION_LABELS["uk"] = "United Kingdom"
_JURISDICTION_LABELS["eu"] = "European Union"


class KpmgTaxNewsFlashEuropeAdapter:
    source_name = "KPMG_TAXNEWSFLASH_EUROPE"
    source_label = "KPMG TaxNewsFlash Europe"

    def __init__(self, base_url: str = "https://kpmg.com"):
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
    def _fetch_landing_page(self) -> str:
        resp = self._client.get(f"{self.base_url}{LANDING_PATH}")
        resp.raise_for_status()
        return resp.text

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _fetch_page(self, fetch_path: str, page: int) -> tuple[list[dict], int]:
        resp = self._client.get(f"{self.base_url}{fetch_path}", params={"page": page})
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", []), int(data.get("totalPages", 1))

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedPublication]:
        # No server-side "changed since" filter on this endpoint (like PwC),
        # so every run re-pulls each month bucket in full and the diff
        # pipeline detects what's actually new via (source, url).
        html = self._fetch_landing_page()
        fetch_paths = sorted(set(_DATA_FETCH_RE.findall(html)))
        if not fetch_paths:
            raise PublicationScrapeError(
                "KPMG TaxNewsFlash Europe page has no gridlist data-fetch "
                "endpoints -- the page's markup likely changed."
            )

        total_seen = 0
        for fetch_path in fetch_paths:
            for page in range(1, MAX_PAGES_PER_MONTH + 1):
                items, total_pages = self._fetch_page(fetch_path, page)
                if not items:
                    break

                for item in items:
                    normalized = self._normalize(item)
                    if normalized is not None:
                        total_seen += 1
                        yield normalized

                if page >= total_pages:
                    break

        if total_seen == 0:
            raise PublicationScrapeError(
                "KPMG TaxNewsFlash Europe returned zero items across all "
                "gridlist endpoints -- the response format likely changed."
            )

    def _normalize(self, item: dict) -> NormalizedPublication | None:
        url = item.get("ctaLink")
        title = item.get("title")
        if not url or not title:
            logger.warning("Skipping KPMG TaxNewsFlash item missing ctaLink/title: %s", item)
            return None

        summary = item.get("description") or None

        return NormalizedPublication(
            source=self.source_name,
            url=url,
            title=title,
            source_label=self.source_label,
            summary=summary,
            published_date=_parse_date(item.get("sortTime"), item.get("dateTime")),
            topic_tags=_split_tags(item.get("allTags")),
            content_type=item.get("category") or None,
            relevant_jurisdiction=_extract_jurisdiction(title),
            tax_keywords_matched=matching_keywords(title, summary),
            raw_source_payload=item,
        )


def _split_tags(value: str | None) -> list[str]:
    if not value:
        return []
    seen: list[str] = []
    for tag in value.split(","):
        tag = tag.strip()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def _extract_jurisdiction(title: str) -> str | None:
    prefix = title.split(":", 1)[0].strip().lower()
    return _JURISDICTION_LABELS.get(prefix)


def _parse_date(sort_time: str | None, date_time: str | None) -> dt.date | None:
    if sort_time:
        try:
            return dt.datetime.fromisoformat(sort_time).date()
        except ValueError:
            pass
    if date_time:
        try:
            return dt.datetime.strptime(date_time, "%d %B %Y").date()
        except ValueError:
            pass
    return None

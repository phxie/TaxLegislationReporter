from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.ingestion.publications_base import NormalizedPublication, PublicationScrapeError
from app.ingestion.tax_filter import matching_keywords

logger = logging.getLogger(__name__)

# EY's tax-alerts page (www.ey.com) renders its list client-side from a
# separate search-as-a-service host (api-search.ey.com). Discovered by
# watching network traffic while driving the live page with Playwright;
# confirmed to work standalone via plain HTTP -- no cookies/session needed,
# but the API rejects requests without a same-site Referer header.
SEARCH_PATH = "/usepinthi2azf04_Prod/orchestrators/search/query"
INDEX_NAME = "asengine-en-gl"
CONTENT_PATH = "/content/ey-unified-site/ey-com/global/main/en_gl/home/technical/tax-alerts/"
CONTENT_TYPE_FILTER = "technical-content-hub,technical-content-full,technical-content-stub"
REFERER = "https://www.ey.com/"

# This endpoint is EY's *entire* global "technical content" search index
# (results_count ~3,000), not a small dedicated tax-alerts feed, so -- unlike
# PwC's adapter, which re-pulls its whole (~100-item) listing every run --
# this only pulls a bounded window of the most recent items per run and
# relies on the diff/upsert to find what's new.
PAGE_SIZE = 50
MAX_PAGES = 3

# The majority of "latest" items under this content path are actually
# Immigration alerts, not tax (confirmed by sampling: ~64% Immigration vs.
# Corporate Tax/Transfer Pricing/VAT/BEPS 2.0/etc.), so unlike PwC (whose
# whole page is tax already), we filter using EY's own category taxonomy
# rather than trusting the page scope alone.
EXCLUDED_CATEGORIES = {"immigration"}


class EyTaxAlertsAdapter:
    source_name = "EY_TAX_ALERTS"
    source_label = "EY Tax Alerts"

    def __init__(self, base_url: str = "https://api-search.ey.com"):
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
    def _fetch_page(self, page: int) -> tuple[list[dict], int]:
        resp = self._client.get(
            f"{self.base_url}{SEARCH_PATH}",
            params={
                "i": INDEX_NAME,
                "p": page,
                "ps": PAGE_SIZE,
                "q": "",
                "s": "latest",
                "contentpath": CONTENT_PATH,
                "f": CONTENT_TYPE_FILTER,
            },
            headers={
                "Referer": REFERER,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("search_results", []), int(data.get("results_count", 0))

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedPublication]:
        total_raw_items = 0

        for page in range(1, MAX_PAGES + 1):
            items, _ = self._fetch_page(page)
            if not items:
                break
            total_raw_items += len(items)

            for item in items:
                normalized = self._normalize(item)
                if normalized is not None:
                    yield normalized

        if total_raw_items == 0:
            raise PublicationScrapeError(
                "EY Tax Alerts search API returned zero items across all pages "
                "-- the response format likely changed."
            )

    def _normalize(self, item: dict) -> NormalizedPublication | None:
        url = _raw(item.get("url"))
        title = _raw(item.get("pagetitle")) or _raw(item.get("title"))
        if not url or not title:
            logger.warning("Skipping EY Tax Alerts item missing url/title: %s", item)
            return None

        categories = _as_list(_raw(item.get("category_label")))
        if any(cat.strip().lower() in EXCLUDED_CATEGORIES for cat in categories):
            return None

        summary = _raw(item.get("pagedescription")) or _raw(item.get("meta_description"))
        published_date = _parse_date(_raw(item.get("dateuser")))
        jurisdictions = _as_list(_raw(item.get("jurisdiction_label")))
        relevant_jurisdiction = _normalize_jurisdiction(jurisdictions[0]) if jurisdictions else None

        return NormalizedPublication(
            source=self.source_name,
            url=url,
            title=title,
            source_label=self.source_label,
            summary=summary,
            published_date=published_date,
            topic_tags=categories,
            content_type=", ".join(categories) if categories else None,
            relevant_jurisdiction=relevant_jurisdiction,
            tax_keywords_matched=matching_keywords(title, summary),
            raw_source_payload=item,
        )


def _raw(field: object) -> str | list | None:
    if isinstance(field, dict):
        return field.get("raw")
    return None


def _as_list(value: str | list | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [value]


def _normalize_jurisdiction(label: str) -> str:
    # Consistent with PwC's heuristic, which maps generic US content to "Federal".
    if label == "United States":
        return "Federal"
    return label


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        return None

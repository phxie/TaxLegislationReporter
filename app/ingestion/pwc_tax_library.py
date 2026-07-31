from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterator

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.ingestion.publications_base import NormalizedPublication
from app.ingestion.tax_filter import matching_keywords

logger = logging.getLogger(__name__)

# Undocumented AEM Sling servlet backing the "Load More" button on the Tax
# Library page. Discovered by watching network traffic while driving the
# live page with Playwright; confirmed to work standalone via plain HTTP
# with no cookies/session required. `page` is a skip/offset (not a 1-indexed
# page number) -- paginate in increments of the batch size actually returned.
COLLECTION_PATH = (
    "/content/pwc/us/en/services/tax/library/jcr:content/root/container/"
    "content-free-container-1/section_65997272/collection_v2.rebrand-filter-dynamic.html"
)
CURRENT_PAGE_PATH = "/content/pwc/us/en/services/tax/library"


class PublicationScrapeError(RuntimeError):
    """Raised when the source reports items exist but none could be extracted.

    This is the dangerous failure mode for an undocumented, scraped endpoint:
    a response-shape change would still return HTTP 200, so it must not be
    reported as "success, 0 new" -- that's indistinguishable from a genuinely
    quiet week.
    """


class PwcTaxLibraryAdapter:
    source_name = "PWC_TAX_LIBRARY"
    source_label = "PwC Tax Library"

    def __init__(self, base_url: str = "https://www.pwc.com"):
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
    def _fetch_page(self, offset: int) -> tuple[list[dict], int]:
        resp = self._client.get(
            f"{self.base_url}{COLLECTION_PATH}",
            params={
                "currentPagePath": CURRENT_PAGE_PATH,
                "list": "{}",
                "searchText": "",
                "defaultImagePath": "/content/dam/pwc/network/collection-fallback-images",
                "page": offset,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        elements = json.loads(data.get("elements", "[]"))
        number_hits = int(data.get("numberHits", 0))
        return elements, number_hits

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedPublication]:
        # The page only ever exposes the current ~100-item listing (no
        # server-side "changed since" filter), so -- like the CA adapter --
        # every run pulls the full listing and the diff pipeline detects
        # what's actually new via the (source, url) unique constraint.
        offset = 0
        total_seen = 0
        number_hits = None

        while True:
            elements, number_hits = self._fetch_page(offset)
            if not elements:
                break

            for element in elements:
                normalized = self._normalize(element)
                if normalized is not None:
                    total_seen += 1
                    yield normalized

            offset += len(elements)
            if offset >= number_hits:
                break

        if number_hits and total_seen == 0:
            raise PublicationScrapeError(
                f"PwC Tax Library reported {number_hits} items but none could be extracted "
                "-- the page's response format likely changed."
            )

    def _normalize(self, element: dict) -> NormalizedPublication | None:
        href = element.get("href")
        title = element.get("title")
        if not href or not title:
            logger.warning("Skipping PwC Tax Library item missing href/title: %s", element)
            return None

        summary = element.get("text") or None
        tags = element.get("tags") or []
        published_date = _parse_publish_date(element.get("publishDate"))
        content_type = _infer_content_type(tags, bool(element.get("isVideo")))

        return NormalizedPublication(
            source=self.source_name,
            url=href,
            title=title,
            source_label=self.source_label,
            summary=summary,
            published_date=published_date,
            topic_tags=tags,
            content_type=content_type,
            tax_keywords_matched=matching_keywords(title, summary),
            raw_source_payload=element,
        )


def _parse_publish_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%B %d, %Y").date()
    except ValueError:
        return None


def _infer_content_type(tags: list[str], is_video: bool) -> str | None:
    if is_video:
        return "Video"
    for tag in tags:
        if "content-type/podcast" in tag or "content-type:podcast" in tag:
            return "Podcast"
        if "content-type/publication" in tag:
            return "Publication"
    return None

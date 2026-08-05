from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class NormalizedPublication:
    source: str
    url: str
    title: str
    # Human-readable name of the originating system (e.g. "PwC Tax Library"),
    # as opposed to `source`, which is the short internal code.
    source_label: str
    summary: str | None = None
    published_date: dt.date | None = None
    topic_tags: list[str] = field(default_factory=list)
    content_type: str | None = None
    relevant_jurisdiction: str | None = None
    tax_keywords_matched: list[str] = field(default_factory=list)
    raw_source_payload: dict | None = None


class PublicationSourceAdapter(Protocol):
    source_name: str

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedPublication]:
        """Yield publications that are new or changed since `since`.

        `since=None` means "full backfill" (e.g. first run for this source).
        """
        ...


class PublicationScrapeError(RuntimeError):
    """Raised when a source reports/implies items exist but none could be extracted.

    This is the dangerous failure mode for a scraped/undocumented endpoint: a
    response-shape change would still return HTTP 200, so it must not be
    reported as "success, 0 new" -- that's indistinguishable from a genuinely
    quiet period.
    """

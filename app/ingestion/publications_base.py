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
    tax_keywords_matched: list[str] = field(default_factory=list)
    raw_source_payload: dict | None = None


class PublicationSourceAdapter(Protocol):
    source_name: str

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedPublication]:
        """Yield publications that are new or changed since `since`.

        `since=None` means "full backfill" (e.g. first run for this source).
        """
        ...

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class NormalizedStatusEvent:
    event_date: dt.date
    action_text: str
    action_code: str | None = None
    sequence_num: int | None = None


@dataclass
class NormalizedBill:
    jurisdiction: str
    source_bill_id: str
    session: str
    bill_number: str
    title: str
    summary: str | None = None
    sponsors: list[str] = field(default_factory=list)
    status_text: str | None = None
    status_code: str | None = None
    last_action_date: dt.date | None = None
    introduced_date: dt.date | None = None
    full_text_url: str | None = None
    source_url: str | None = None
    tax_keywords_matched: list[str] = field(default_factory=list)
    raw_source_payload: dict | None = None
    status_events: list[NormalizedStatusEvent] = field(default_factory=list)


class SourceAdapter(Protocol):
    source_name: str

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        """Yield tax-relevant bills that are new or changed since `since`.

        `since=None` means "full backfill" (e.g. first run for this source).
        """
        ...

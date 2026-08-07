from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JURISDICTIONS = ("FEDERAL", "CA", "NY")
PUBLICATION_SOURCES = ("PWC_TAX_LIBRARY", "EY_TAX_ALERTS", "KPMG_TAXNEWSFLASH_EUROPE")


class Bill(Base):
    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("jurisdiction", "source_bill_id", "session", name="uq_bill_natural_key"),
        CheckConstraint(
            f"jurisdiction IN ({', '.join(repr(j) for j in JURISDICTIONS)})",
            name="ck_bill_jurisdiction",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(index=True)
    source_bill_id: Mapped[str]
    session: Mapped[str]
    bill_number: Mapped[str]

    title: Mapped[str]
    # Human-readable name of the originating system (e.g. "Congress.gov"),
    # as opposed to `jurisdiction`, which is the short internal code (e.g. "FEDERAL").
    source_label: Mapped[str]
    summary: Mapped[str | None] = mapped_column(default=None)
    sponsors_json: Mapped[list | None] = mapped_column(JSONB, default=None)

    status_text: Mapped[str | None] = mapped_column(default=None)
    status_code: Mapped[str | None] = mapped_column(default=None)
    last_action_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    introduced_date: Mapped[dt.date | None] = mapped_column(Date, default=None)

    full_text_url: Mapped[str | None] = mapped_column(default=None)
    source_url: Mapped[str | None] = mapped_column(default=None)

    is_tax_relevant: Mapped[bool] = mapped_column(default=True)
    tax_keywords_matched: Mapped[list | None] = mapped_column(JSONB, default=None)
    raw_source_payload: Mapped[dict | None] = mapped_column(JSONB, default=None)

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    status_events: Mapped[list[BillStatusEvent]] = relationship(
        back_populates="bill", cascade="all, delete-orphan", order_by="BillStatusEvent.event_date"
    )
    changes: Mapped[list[BillChange]] = relationship(
        back_populates="bill", cascade="all, delete-orphan"
    )


class BillStatusEvent(Base):
    __tablename__ = "bill_status_events"
    __table_args__ = (
        UniqueConstraint("bill_id", "event_date", "action_text", name="uq_status_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id", ondelete="CASCADE"), index=True)
    event_date: Mapped[dt.date] = mapped_column(Date)
    action_text: Mapped[str]
    action_code: Mapped[str | None] = mapped_column(default=None)
    sequence_num: Mapped[int | None] = mapped_column(default=None)
    source: Mapped[str]
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    bill: Mapped[Bill] = relationship(back_populates="status_events")


class BillChange(Base):
    __tablename__ = "bill_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id", ondelete="CASCADE"), index=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"), default=None
    )
    change_type: Mapped[str]  # NEW_BILL | STATUS_CHANGE | METADATA_UPDATED
    field_name: Mapped[str | None] = mapped_column(default=None)
    old_value: Mapped[str | None] = mapped_column(default=None)
    new_value: Mapped[str | None] = mapped_column(default=None)
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    bill: Mapped[Bill] = relationship(back_populates="changes")


class Publication(Base):
    """A non-legislative content item (e.g. a PwC Tax Library article).

    Deliberately separate from `Bill`: publications have no jurisdiction,
    session, bill number, sponsors, or status timeline, and are effectively
    immutable once published, so there's no publication-equivalent of
    `BillChange`/`BillStatusEvent` -- "what's new" just means
    `first_seen_at >= since`.
    """

    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("source", "url", name="uq_publication_natural_key"),
        CheckConstraint(
            f"source IN ({', '.join(repr(s) for s in PUBLICATION_SOURCES)})",
            name="ck_publication_source",
        ),
        # No CHECK constraint on relevant_jurisdiction: PwC (no native jurisdiction
        # data) is limited to the US-state/Federal/International/Multistate
        # heuristic in jurisdiction_detect.py, but EY provides its own authoritative,
        # global jurisdiction taxonomy (e.g. "Guinea", "European Union") that a
        # fixed enum can't practically cover.
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(index=True)
    # Human-readable name of the originating system (e.g. "PwC Tax Library"),
    # as opposed to `source`, which is the short internal code.
    source_label: Mapped[str]
    url: Mapped[str]

    title: Mapped[str]
    summary: Mapped[str | None] = mapped_column(default=None)
    published_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    topic_tags_json: Mapped[list | None] = mapped_column(JSONB, default=None)
    content_type: Mapped[str | None] = mapped_column(default=None)
    # "Which jurisdiction is this article about" -- authoritative when the
    # source provides its own (e.g. EY's jurisdiction taxonomy), otherwise a
    # best-effort heuristic inferred from title/summary text (PwC; see
    # app/ingestion/jurisdiction_detect.py). Informational either way.
    relevant_jurisdiction: Mapped[str | None] = mapped_column(index=True, default=None)

    is_tax_relevant: Mapped[bool] = mapped_column(default=True)
    tax_keywords_matched: Mapped[list | None] = mapped_column(JSONB, default=None)
    raw_source_payload: Mapped[dict | None] = mapped_column(JSONB, default=None)

    # LLM-generated summary (Claude Haiku via the Batches API, see
    # app/ingestion/summarize.py). `ai_summary_requested_at` is set when a
    # batch entry is submitted for this row and left in place if the batch
    # times out or the item fails, so a later run only retries it once
    # `ai_summary_requested_at` falls outside the staleness window rather
    # than resubmitting (and re-billing) it every run.
    ai_summary: Mapped[str | None] = mapped_column(default=None)
    ai_summary_requested_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str]
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    status: Mapped[str] = mapped_column(default="running")  # running | success | failed | partial
    bills_seen: Mapped[int] = mapped_column(default=0)
    bills_new: Mapped[int] = mapped_column(default=0)
    bills_updated: Mapped[int] = mapped_column(default=0)
    errors_json: Mapped[list | None] = mapped_column(JSONB, default=None)

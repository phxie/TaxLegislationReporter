from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ingestion.base import NormalizedBill
from app.models import Bill, BillChange, BillStatusEvent

MUTABLE_FIELDS = (
    "title",
    "summary",
    "status_text",
    "status_code",
    "last_action_date",
    "introduced_date",
    "full_text_url",
    "source_url",
)

STATUS_FIELDS = {"status_text", "status_code"}


def apply_bill(db: Session, normalized: NormalizedBill, *, ingestion_run_id: int) -> tuple[Bill, bool]:
    """Insert or update a bill, logging changes. Returns (bill, is_new)."""
    existing = db.scalars(
        select(Bill).where(
            Bill.jurisdiction == normalized.jurisdiction,
            Bill.source_bill_id == normalized.source_bill_id,
            Bill.session == normalized.session,
        )
    ).first()

    now = dt.datetime.now(dt.UTC)

    if existing is None:
        bill = Bill(
            jurisdiction=normalized.jurisdiction,
            source_bill_id=normalized.source_bill_id,
            session=normalized.session,
            bill_number=normalized.bill_number,
            title=normalized.title,
            source_label=normalized.source_label,
            summary=normalized.summary,
            sponsors_json=normalized.sponsors,
            status_text=normalized.status_text,
            status_code=normalized.status_code,
            last_action_date=normalized.last_action_date,
            introduced_date=normalized.introduced_date,
            full_text_url=normalized.full_text_url,
            source_url=normalized.source_url,
            is_tax_relevant=True,
            tax_keywords_matched=normalized.tax_keywords_matched,
            raw_source_payload=normalized.raw_source_payload,
            last_seen_at=now,
        )
        db.add(bill)
        db.flush()

        db.add(
            BillChange(
                bill_id=bill.id,
                ingestion_run_id=ingestion_run_id,
                change_type="NEW_BILL",
                new_value=normalized.title,
            )
        )
        _upsert_status_events(db, bill.id, normalized, ingestion_run_id)
        return bill, True

    changed_fields: list[tuple[str, object, object]] = []
    for field_name in MUTABLE_FIELDS:
        old_value = getattr(existing, field_name)
        new_value = getattr(normalized, field_name)
        if old_value != new_value:
            changed_fields.append((field_name, old_value, new_value))
            setattr(existing, field_name, new_value)

    existing.sponsors_json = normalized.sponsors
    existing.source_label = normalized.source_label
    existing.tax_keywords_matched = normalized.tax_keywords_matched
    existing.raw_source_payload = normalized.raw_source_payload
    existing.last_seen_at = now

    for field_name, old_value, new_value in changed_fields:
        change_type = "STATUS_CHANGE" if field_name in STATUS_FIELDS else "METADATA_UPDATED"
        db.add(
            BillChange(
                bill_id=existing.id,
                ingestion_run_id=ingestion_run_id,
                change_type=change_type,
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
            )
        )

    new_event_count = _upsert_status_events(db, existing.id, normalized, ingestion_run_id)
    changed_status_field = any(f in STATUS_FIELDS for f, _, _ in changed_fields)
    if new_event_count and not changed_status_field:
        db.add(
            BillChange(
                bill_id=existing.id,
                ingestion_run_id=ingestion_run_id,
                change_type="STATUS_CHANGE",
                field_name="status_events",
                new_value=f"{new_event_count} new action(s)",
            )
        )

    return existing, False


def _upsert_status_events(
    db: Session, bill_id: int, normalized: NormalizedBill, ingestion_run_id: int
) -> int:
    if not normalized.status_events:
        return 0

    rows = [
        {
            "bill_id": bill_id,
            "event_date": event.event_date,
            "action_text": event.action_text,
            "action_code": event.action_code,
            "sequence_num": event.sequence_num,
            "source": normalized.jurisdiction,
            "ingestion_run_id": ingestion_run_id,
        }
        for event in normalized.status_events
    ]

    stmt = pg_insert(BillStatusEvent).values(rows)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["bill_id", "event_date", "action_text"]
    ).returning(BillStatusEvent.id)
    result = db.execute(stmt)
    return len(result.fetchall())

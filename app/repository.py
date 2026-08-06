from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Bill, BillChange, Publication


def list_bills(
    db: Session,
    *,
    jurisdiction: str | None = None,
    status_text: str | None = None,
    keyword: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    limit: int = 200,
) -> list[Bill]:
    stmt = select(Bill).order_by(Bill.last_action_date.desc().nulls_last())

    if jurisdiction:
        stmt = stmt.where(Bill.jurisdiction == jurisdiction)
    if status_text:
        stmt = stmt.where(Bill.status_text == status_text)
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where((Bill.title.ilike(pattern)) | (Bill.summary.ilike(pattern)))
    if date_from:
        stmt = stmt.where(Bill.last_action_date >= date_from)
    if date_to:
        stmt = stmt.where(Bill.last_action_date <= date_to)

    stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def get_bill(db: Session, jurisdiction: str, source_bill_id: str, session: str) -> Bill | None:
    stmt = (
        select(Bill)
        .options(selectinload(Bill.status_events))
        .where(
            Bill.jurisdiction == jurisdiction,
            Bill.source_bill_id == source_bill_id,
            Bill.session == session,
        )
    )
    return db.scalars(stmt).first()


def recent_changes(db: Session, *, since: dt.datetime | None = None, limit: int = 50) -> list[BillChange]:
    stmt = (
        select(BillChange)
        .options(selectinload(BillChange.bill))
        .order_by(BillChange.detected_at.desc())
    )
    if since:
        stmt = stmt.where(BillChange.detected_at >= since)
    stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def bill_ids_with_recent_changes(db: Session, *, since: dt.datetime) -> set[int]:
    stmt = select(BillChange.bill_id).where(BillChange.detected_at >= since).distinct()
    return set(db.scalars(stmt).all())


def list_publications(
    db: Session,
    *,
    source: str | None = None,
    relevant_jurisdiction: str | None = None,
    keyword: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    limit: int = 200,
) -> list[Publication]:
    stmt = select(Publication).order_by(Publication.published_date.desc().nulls_last())

    if source:
        stmt = stmt.where(Publication.source == source)
    if relevant_jurisdiction:
        stmt = stmt.where(Publication.relevant_jurisdiction == relevant_jurisdiction)
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where((Publication.title.ilike(pattern)) | (Publication.summary.ilike(pattern)))
    if date_from:
        stmt = stmt.where(Publication.published_date >= date_from)
    if date_to:
        stmt = stmt.where(Publication.published_date <= date_to)

    stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def get_publication(db: Session, publication_id: int) -> Publication | None:
    return db.get(Publication, publication_id)


def distinct_publication_jurisdictions(db: Session) -> list[str]:
    stmt = (
        select(Publication.relevant_jurisdiction)
        .where(Publication.relevant_jurisdiction.is_not(None))
        .distinct()
        .order_by(Publication.relevant_jurisdiction)
    )
    return list(db.scalars(stmt).all())


def distinct_publication_sources(db: Session) -> list[tuple[str, str]]:
    """Returns (source, source_label) pairs actually present, for a filter dropdown."""
    stmt = (
        select(Publication.source, Publication.source_label)
        .distinct()
        .order_by(Publication.source_label)
    )
    return [(row.source, row.source_label) for row in db.execute(stmt)]

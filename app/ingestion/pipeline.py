from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.base import SourceAdapter
from app.ingestion.diff import apply_bill
from app.models import IngestionRun

logger = logging.getLogger(__name__)


def _last_successful_run(db: Session, source_name: str) -> IngestionRun | None:
    stmt = (
        select(IngestionRun)
        .where(IngestionRun.source == source_name, IngestionRun.status.in_(("success", "partial")))
        .order_by(IngestionRun.started_at.desc())
    )
    return db.scalars(stmt).first()


def run_source(db: Session, adapter: SourceAdapter) -> IngestionRun:
    run = IngestionRun(source=adapter.source_name, status="running")
    db.add(run)
    db.commit()

    last_run = _last_successful_run(db, adapter.source_name)
    since = last_run.started_at if last_run else None

    bills_seen = 0
    bills_new = 0
    bills_updated = 0
    errors: list[dict] = []

    try:
        for normalized in adapter.fetch_updates(since):
            bills_seen += 1
            try:
                _, is_new = apply_bill(db, normalized, ingestion_run_id=run.id)
                db.commit()
                if is_new:
                    bills_new += 1
                else:
                    bills_updated += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.exception("Failed to apply bill %s", normalized.source_bill_id)
                errors.append({"bill": normalized.source_bill_id, "error": str(exc)})

        run.status = "partial" if errors else "success"
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("Ingestion run failed for source %s", adapter.source_name)
        run.status = "failed"
        errors.append({"error": str(exc)})

    run.finished_at = dt.datetime.now(dt.UTC)
    run.bills_seen = bills_seen
    run.bills_new = bills_new
    run.bills_updated = bills_updated
    run.errors_json = errors or None
    db.add(run)
    db.commit()
    return run


def run_all(db: Session, adapters: list[SourceAdapter]) -> list[IngestionRun]:
    return [run_source(db, adapter) for adapter in adapters]

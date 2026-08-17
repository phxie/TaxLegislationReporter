from __future__ import annotations

import datetime as dt
import logging

import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal
from app.ingestion.pipeline import run_all, run_all_publications
from app.ingestion.registry import (
    build_heavy_adapters,
    build_light_adapters,
    build_publication_adapters,
)
from app.ingestion.summarize import run_pending_bill_summaries, run_pending_summaries

logger = logging.getLogger(__name__)


def _run_light_sources(settings: Settings) -> None:
    adapters = build_light_adapters(settings)
    if not adapters:
        return
    db = SessionLocal()
    try:
        run_all(db, adapters)
        _run_pending_bill_summaries(settings, db)
    finally:
        db.close()


def _run_heavy_sources(settings: Settings) -> None:
    adapters = build_heavy_adapters(settings)
    if not adapters:
        return
    db = SessionLocal()
    try:
        run_all(db, adapters)
        _run_pending_bill_summaries(settings, db)
    finally:
        db.close()


def _run_publication_sources(settings: Settings) -> None:
    adapters = build_publication_adapters(settings)
    if not adapters:
        return
    db = SessionLocal()
    try:
        run_all_publications(db, adapters)
        _run_pending_summaries(settings, db)
    finally:
        db.close()


def _run_pending_summaries(settings: Settings, db: Session) -> None:
    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY not set; skipping publication AI summaries")
        return
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    run_pending_summaries(
        db,
        client,
        batch_size=settings.ai_summary_batch_size,
        stale_after=dt.timedelta(hours=settings.ai_summary_stale_after_hours),
        max_wait_seconds=settings.ai_summary_max_wait_seconds,
    )


def _run_pending_bill_summaries(settings: Settings, db: Session) -> None:
    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY not set; skipping bill AI summaries")
        return
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    run_pending_bill_summaries(
        db,
        client,
        batch_size=settings.ai_summary_batch_size,
        stale_after=dt.timedelta(hours=settings.ai_summary_stale_after_hours),
        max_wait_seconds=settings.ai_summary_max_wait_seconds,
    )


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    # BackgroundScheduler (not AsyncIOScheduler): our ingestion pipeline uses
    # sync SQLAlchemy + sync httpx, so jobs must run in a worker thread rather
    # than directly on the event loop, where they'd block request handling.
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_light_sources,
        "interval",
        hours=settings.scrape_interval_hours,
        args=[settings],
        id="ingest_light_sources",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _run_heavy_sources,
        "interval",
        hours=settings.ca_scrape_interval_hours,
        args=[settings],
        id="ingest_heavy_sources",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _run_publication_sources,
        "interval",
        hours=settings.publications_scrape_interval_hours,
        args=[settings],
        id="ingest_publication_sources",
        coalesce=True,
        max_instances=1,
    )
    return scheduler

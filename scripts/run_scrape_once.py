"""Run one ingestion pass across all (or selected) configured sources.

Usage: uv run scripts/run_scrape_once.py [source ...]
       e.g. uv run scripts/run_scrape_once.py FEDERAL
            uv run scripts/run_scrape_once.py CA NY
            uv run scripts/run_scrape_once.py PWC_TAX_LIBRARY EY_TAX_ALERTS KPMG_TAXNEWSFLASH_EUROPE
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Running this file directly (rather than as `python -m`) puts its own
# directory on sys.path instead of the project root, so `app` wouldn't
# otherwise be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic

from app.config import get_settings
from app.db import SessionLocal
from app.ingestion.pipeline import run_all, run_all_publications
from app.ingestion.registry import build_all_adapters, build_publication_adapters
from app.ingestion.summarize import run_pending_summaries

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _log_run(run) -> None:
    logger.info(
        "source=%s status=%s seen=%d new=%d updated=%d errors=%s",
        run.source,
        run.status,
        run.bills_seen,
        run.bills_new,
        run.bills_updated,
        run.errors_json,
    )


def main() -> None:
    requested = {s.upper() for s in sys.argv[1:]}
    settings = get_settings()

    bill_adapters = build_all_adapters(settings)
    publication_adapters = build_publication_adapters(settings)
    if requested:
        bill_adapters = [a for a in bill_adapters if a.source_name in requested]
        publication_adapters = [a for a in publication_adapters if a.source_name in requested]

    if not bill_adapters and not publication_adapters:
        logger.warning("No adapters configured/selected; nothing to do")
        return

    db = SessionLocal()
    try:
        for run in run_all(db, bill_adapters):
            _log_run(run)
        for run in run_all_publications(db, publication_adapters):
            _log_run(run)

        if publication_adapters and settings.anthropic_api_key:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            summarized = run_pending_summaries(db, client)
            logger.info("AI summaries: %d publications summarized", summarized)
    finally:
        db.close()


if __name__ == "__main__":
    main()

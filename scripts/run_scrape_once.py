"""Run one ingestion pass across all (or selected) configured sources.

Usage: uv run scripts/run_scrape_once.py [source ...]
       e.g. uv run scripts/run_scrape_once.py FEDERAL
            uv run scripts/run_scrape_once.py CA NY
"""

from __future__ import annotations

import logging
import sys

from app.config import get_settings
from app.db import SessionLocal
from app.ingestion.pipeline import run_all
from app.ingestion.registry import build_all_adapters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    requested = {s.upper() for s in sys.argv[1:]}
    adapters = build_all_adapters(get_settings())
    if requested:
        adapters = [a for a in adapters if a.source_name in requested]

    if not adapters:
        logger.warning("No adapters configured/selected; nothing to do")
        return

    db = SessionLocal()
    try:
        runs = run_all(db, adapters)
    finally:
        db.close()

    for run in runs:
        logger.info(
            "source=%s status=%s seen=%d new=%d updated=%d errors=%s",
            run.source,
            run.status,
            run.bills_seen,
            run.bills_new,
            run.bills_updated,
            run.errors_json,
        )


if __name__ == "__main__":
    main()

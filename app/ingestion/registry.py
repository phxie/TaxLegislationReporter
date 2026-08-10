from __future__ import annotations

import logging

from app.config import Settings
from app.ingestion.base import SourceAdapter
from app.ingestion.california import CaliforniaAdapter
from app.ingestion.canada_legisinfo import CanadaLegisinfoAdapter
from app.ingestion.congress_gov import CongressGovAdapter
from app.ingestion.ey_tax_alerts import EyTaxAlertsAdapter
from app.ingestion.kpmg_taxnewsflash_europe import KpmgTaxNewsFlashEuropeAdapter
from app.ingestion.new_york import NewYorkSenateAdapter
from app.ingestion.publications_base import PublicationSourceAdapter
from app.ingestion.pwc_tax_library import PwcTaxLibraryAdapter
from app.ingestion.spain_congreso import SpainCongresoAdapter

logger = logging.getLogger(__name__)


def build_light_adapters(settings: Settings) -> list[SourceAdapter]:
    """Adapters cheap/fast enough to run on the short `scrape_interval_hours` cadence."""
    adapters: list[SourceAdapter] = []

    if settings.congress_api_key:
        adapters.append(
            CongressGovAdapter(api_key=settings.congress_api_key, base_url=settings.congress_api_base_url)
        )
    else:
        logger.warning("CONGRESS_API_KEY not set; skipping federal adapter")

    if settings.ny_senate_api_key:
        adapters.append(
            NewYorkSenateAdapter(api_key=settings.ny_senate_api_key, base_url=settings.ny_senate_api_base_url)
        )
    else:
        logger.warning("NY_SENATE_API_KEY not set; skipping New York adapter")

    # No auth required, and (after the initial backfill) cheap: only bills
    # whose activity changed since the last run get a detail fetch.
    adapters.append(CanadaLegisinfoAdapter(base_url=settings.canada_legisinfo_base_url))

    # No auth required; small, bounded dataset (a few hundred items per
    # legislature), re-pulled in full every run like PwC/California.
    adapters.append(
        SpainCongresoAdapter(
            base_url=settings.spain_congreso_base_url,
            legislature=settings.spain_congreso_legislature,
        )
    )

    return adapters


def build_heavy_adapters(settings: Settings) -> list[SourceAdapter]:
    """Adapters that are expensive (large downloads) and run on their own, longer cadence."""
    return [CaliforniaAdapter(base_url=settings.ca_pubinfo_base_url)]


def build_all_adapters(settings: Settings) -> list[SourceAdapter]:
    return build_light_adapters(settings) + build_heavy_adapters(settings)


def build_publication_adapters(settings: Settings) -> list[PublicationSourceAdapter]:
    return [
        PwcTaxLibraryAdapter(base_url=settings.pwc_tax_library_base_url),
        EyTaxAlertsAdapter(base_url=settings.ey_tax_alerts_base_url),
        KpmgTaxNewsFlashEuropeAdapter(base_url=settings.kpmg_taxnewsflash_europe_base_url),
    ]

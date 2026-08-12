from __future__ import annotations

import logging

from app.config import Settings
from app.ingestion.base import SourceAdapter
from app.ingestion.california import CaliforniaAdapter
from app.ingestion.canada_legisinfo import CanadaLegisinfoAdapter
from app.ingestion.congress_gov import CongressGovAdapter
from app.ingestion.ey_tax_alerts import EyTaxAlertsAdapter
from app.ingestion.france_assemblee import FranceAssembleeAdapter
from app.ingestion.india_prs import IndiaPrsAdapter
from app.ingestion.kpmg_taxnewsflash_europe import KpmgTaxNewsFlashEuropeAdapter
from app.ingestion.new_york import NewYorkSenateAdapter
from app.ingestion.publications_base import PublicationSourceAdapter
from app.ingestion.pwc_tax_library import PwcTaxLibraryAdapter
from app.ingestion.spain_congreso import SpainCongresoAdapter
from app.ingestion.uk_parliament import UkParliamentAdapter

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

    # No auth required; official structured API, small current-session
    # dataset re-pulled in full every run like Spain.
    adapters.append(UkParliamentAdapter(base_url=settings.uk_parliament_base_url))

    # No auth required; India has no official structured bill API, so this
    # scrapes PRS Legislative Research's public bill tracker instead (see
    # app/ingestion/india_prs.py). Full listing re-pulled every run, detail
    # fetched only for likely-relevant bills.
    adapters.append(IndiaPrsAdapter(base_url=settings.india_prs_base_url))

    return adapters


def build_heavy_adapters(settings: Settings) -> list[SourceAdapter]:
    """Adapters that are expensive (large downloads) and run on their own, longer cadence."""
    return [
        CaliforniaAdapter(base_url=settings.ca_pubinfo_base_url),
        # No auth required; a ~10MB bulk zip of every dossier for the
        # current legislature (no per-bill requests needed at all, unlike
        # every other bill source here), refreshed daily like California.
        FranceAssembleeAdapter(
            base_url=settings.france_an_base_url,
            legislature=settings.france_an_legislature,
        ),
    ]


def build_all_adapters(settings: Settings) -> list[SourceAdapter]:
    return build_light_adapters(settings) + build_heavy_adapters(settings)


def build_publication_adapters(settings: Settings) -> list[PublicationSourceAdapter]:
    return [
        PwcTaxLibraryAdapter(base_url=settings.pwc_tax_library_base_url),
        EyTaxAlertsAdapter(base_url=settings.ey_tax_alerts_base_url),
        KpmgTaxNewsFlashEuropeAdapter(base_url=settings.kpmg_taxnewsflash_europe_base_url),
    ]

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/tax_legislation"

    congress_api_key: str = ""
    ny_senate_api_key: str = ""
    anthropic_api_key: str = ""

    scrape_interval_hours: int = 6
    # California only publishes a full ~1GB session snapshot once a day, so it
    # gets its own (longer) interval rather than sharing scrape_interval_hours.
    ca_scrape_interval_hours: int = 24
    # Publication sources (PwC, EY, KPMG) publish at most a few items/day, so
    # they share this longer interval rather than the bill sources' cadence.
    publications_scrape_interval_hours: int = 24

    # AI summary generation (app/ingestion/summarize.py) runs as part of the
    # publication scrape, via the Anthropic Batches API.
    ai_summary_batch_size: int = 500
    # How long a submitted-but-unresolved batch entry is left alone before a
    # later run is allowed to resubmit it -- avoids re-billing the same item
    # every run just because one batch happened to time out.
    ai_summary_stale_after_hours: int = 2
    # How long a single scheduled run will block waiting for its batch to
    # finish before giving up for this run (the batch itself keeps running
    # server-side either way; unresolved items are retried after the
    # staleness window above).
    ai_summary_max_wait_seconds: int = 600

    congress_api_base_url: str = "https://api.congress.gov/v3"
    ny_senate_api_base_url: str = "https://legislation.nysenate.gov/api/3"
    ca_pubinfo_base_url: str = "https://downloads.leginfo.legislature.ca.gov"
    canada_legisinfo_base_url: str = "https://www.parl.ca"
    spain_congreso_base_url: str = "https://www.congreso.es"
    # "15" = XV Legislatura (2023-present). Spain elects a new Congress every
    # few years, so this is expected to need updating periodically.
    spain_congreso_legislature: str = "15"
    pwc_tax_library_base_url: str = "https://www.pwc.com"
    ey_tax_alerts_base_url: str = "https://api-search.ey.com"
    kpmg_taxnewsflash_europe_base_url: str = "https://kpmg.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()

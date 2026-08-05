from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/tax_legislation"

    congress_api_key: str = ""
    ny_senate_api_key: str = ""

    scrape_interval_hours: int = 6
    # California only publishes a full ~1GB session snapshot once a day, so it
    # gets its own (longer) interval rather than sharing scrape_interval_hours.
    ca_scrape_interval_hours: int = 24
    # Publication sources (PwC, EY) publish at most a few items/day, so they
    # share this longer interval rather than the bill sources' cadence.
    publications_scrape_interval_hours: int = 24

    congress_api_base_url: str = "https://api.congress.gov/v3"
    ny_senate_api_base_url: str = "https://legislation.nysenate.gov/api/3"
    ca_pubinfo_base_url: str = "https://downloads.leginfo.legislature.ca.gov"
    pwc_tax_library_base_url: str = "https://www.pwc.com"
    ey_tax_alerts_base_url: str = "https://api-search.ey.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()

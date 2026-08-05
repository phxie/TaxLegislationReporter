# TaxLegislationReporter

Aggregates newly introduced and changed tax-related legislation, plus tax news/publications,
from federal, state, and secondary sources into a single searchable web dashboard.

## Sources

**Legislation** (bills — jurisdiction-specific, tracked with a status timeline):

| Jurisdiction | Source | Access |
| --- | --- | --- |
| Federal | [Congress.gov API](https://api.congress.gov/) | Free API key required |
| California | [PUBINFO bulk data](https://downloads.leginfo.legislature.ca.gov/) | No auth required |
| New York | [Open Legislation API](https://legislation.nysenate.gov/) | Free API key required |

Bills are filtered to tax-relevant ones using each source's own tax signal where available
(Congress.gov policy area "Taxation", California's `taxlevy` flag) with a shared keyword
fallback (see `app/ingestion/tax_filter.py`).

California only publishes a full session snapshot once a day (its smaller daily delta file
omits bill titles/subjects), so it's ingested on its own, longer schedule rather than the
shared interval used for the other sources.

**Publications** (articles/insights — not legislation, no status timeline; kept as a
separate concept from bills, see `app/models.py`'s `Publication`):

| Source | Access | Notes |
| --- | --- | --- |
| [PwC Tax Library](https://www.pwc.com/us/en/services/tax/library.html) | No auth required | Undocumented AEM endpoint (see `app/ingestion/pwc_tax_library.py`) — no official API/RSS exists, so this is more fragile to upstream site changes than the structured legislation sources above |
| [EY Tax Alerts](https://www.ey.com/en_gl/technical/tax-alerts) | No auth required | Undocumented search-API endpoint (see `app/ingestion/ey_tax_alerts.py`); the endpoint spans EY's *entire* global content index (~3,000 items, mostly non-tax "Immigration" alerts), so this pulls a bounded recent window and filters using EY's own `category_label` rather than trusting the page scope alone |

Each publication gets a `relevant_jurisdiction`: authoritative when the source provides its
own (EY tags every item with a real jurisdiction, e.g. "Guinea", "European Union"), otherwise
a best-effort heuristic inferred from title/summary text via keyword matching (PwC; see
`app/ingestion/jurisdiction_detect.py`, limited to US states + "Federal"/"International"/
"Multistate"). Informational either way, not guaranteed accurate.

## Requirements

- [uv](https://docs.astral.sh/uv/) for Python/dependency management
- Docker (for a local Postgres instance) — or your own PostgreSQL 16+ instance

## Setup

1. Install dependencies:

   ```
   uv sync
   ```

2. Start Postgres locally:

   ```
   docker compose up -d
   ```

3. Copy the environment file and fill in your API keys:

   ```
   cp .env.example .env
   ```

   - `CONGRESS_API_KEY` — get one at https://api.congress.gov/sign-up/
   - `NY_SENATE_API_KEY` — get one at https://legislation.nysenate.gov/static/docs/html/index.html

   Sources without a configured key are skipped automatically (a warning is logged).

4. Run database migrations:

   ```
   uv run alembic upgrade head
   ```

## Usage

Run one ingestion pass across all configured sources:

```
uv run scripts/run_scrape_once.py
```

Or scope it to specific sources:

```
uv run scripts/run_scrape_once.py FEDERAL
uv run scripts/run_scrape_once.py CA NY
uv run scripts/run_scrape_once.py PWC_TAX_LIBRARY EY_TAX_ALERTS
```

Start the dashboard:

```
uv run uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 (bills) or http://127.0.0.1:8000/publications. The app
also runs ingestion automatically in the background on a schedule
(`SCRAPE_INTERVAL_HOURS` / `CA_SCRAPE_INTERVAL_HOURS` / `PUBLICATIONS_SCRAPE_INTERVAL_HOURS`
in `.env`) while it's running.

## Development

```
uv run pytest       # tests
uv run ruff check .  # lint
```

New database schema changes go through Alembic:

```
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

## Project layout

```
app/
├── main.py           # FastAPI app + scheduler lifespan
├── config.py          # Settings (.env)
├── db.py              # SQLAlchemy engine/session
├── models.py           # Bill/BillStatusEvent/BillChange (legislation), Publication (articles), IngestionRun
├── repository.py       # Query helpers for the dashboard
├── scheduler.py         # APScheduler wiring
├── ingestion/
│   ├── base.py                # NormalizedBill / SourceAdapter protocol (legislation)
│   ├── congress_gov.py
│   ├── california.py
│   ├── new_york.py
│   ├── tax_filter.py           # Shared tax-relevance rules
│   ├── diff.py                  # Bill insert/update + change detection
│   ├── publications_base.py      # NormalizedPublication / PublicationSourceAdapter protocol
│   ├── publications_diff.py       # Publication insert/update (no change-log — see Sources above)
│   ├── jurisdiction_detect.py      # Best-effort jurisdiction heuristic (used by PwC)
│   ├── pwc_tax_library.py
│   ├── ey_tax_alerts.py
│   ├── pipeline.py                 # run_all()/run_all_publications() — ingestion entry points
│   └── registry.py                 # Builds adapters from Settings
├── routes/               # dashboard, bills, feed, publications
└── templates/             # Jinja2 + HTMX, no JS build step
scripts/run_scrape_once.py  # Manual/CLI ingestion run
```

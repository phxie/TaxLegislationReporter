# TaxLegislationReporter

Aggregates newly introduced and changed tax-related legislation, plus tax news/publications,
from federal, state, and secondary sources into a single searchable web dashboard.

## Sources

**Legislation** (bills — jurisdiction-specific, tracked with a status timeline). See
[docs/SOURCES.md](docs/SOURCES.md) for how each adapter works, what was tried and rejected along
the way, and known limitations — the summary below is deliberately terse.

| Jurisdiction | Source | Access | Notes |
| --- | --- | --- | --- |
| Federal | [Congress.gov API](https://api.congress.gov/) | Free API key required | Official structured API; own "Taxation" policy-area flag |
| California | [PUBINFO bulk data](https://downloads.leginfo.legislature.ca.gov/) | No auth required | Daily bulk snapshot; own `taxlevy` flag |
| New York | [Open Legislation API](https://legislation.nysenate.gov/) | Free API key required | Official structured API |
| Canada (federal) | [LEGISinfo](https://www.parl.ca/legisinfo/) | No auth required | Only re-fetches per-bill detail for bills changed since the last run |
| Spain (national) | [Congreso de los Diputados](https://www.congreso.es/es/busqueda-de-iniciativas) | No auth required | Undocumented open-data export; thin status data, no per-bill deep link |
| United Kingdom (national) | [UK Parliament Bills API](https://bills-api.parliament.uk/) | No auth required | Cleanest non-US source; official documented REST API |
| India (national) | [PRS Legislative Research](https://prsindia.org/billtrack) | No auth required | Independent research org, not government — the official portal is an unscrapable SPA |
| France (national) | [Assemblée Nationale — Open Data](https://data.assemblee-nationale.fr/) | No auth required | Bulk daily zip; no per-bill requests at all |
| Germany (national) | [Bundestag DIP API](https://dip.bundestag.de/) | Free API key required (a public demo key ships as the default — see below) | Official documented REST API |
| Singapore (national) | [Parliament of Singapore](https://www.parliament.gov.sg/parliamentary-business/bills-introduced) | No auth required | Next.js Server Action pagination — most fragile source by mechanism |
| Mexico (national) | [Cámara de Diputados — Gaceta Parlamentaria](https://gaceta.diputados.gob.mx/gp_iniciativas.html) | No auth required | Bulk HTML archive; ~7,000 bills/legislature |
| Portugal (national) | [Assembleia da República](https://www.parlamento.pt/ActividadeParlamentar/Paginas/IniciativasLegislativas.aspx) | No auth required | Classic ASP.NET WebForms postback pagination |
| Other 48 US states | [Open States](https://openstates.org/) | Free API key required | Third-party aggregator; bulk CSV export per state (not the rate-limited live API) — CA/NY excluded, already covered above |

Bills are filtered to tax-relevant ones using each source's own tax signal where available
(e.g. Congress.gov's policy area, California's `taxlevy` flag), falling back to keyword matching
against the title (and summary, where available) otherwise — see `app/ingestion/tax_filter.py`.
Non-English sources get their own keyword lists, and several needed care to avoid substring
false positives (Singapore's "Taxi", Mexico's/Portugal's "fiscal" vs. "fiscalização") or a
genuine homonym (Portuguese "imposto" = both "tax" and "imposed"); see
[docs/SOURCES.md](docs/SOURCES.md) for how each was found and resolved.

**Publications** (articles/insights — not legislation, no status timeline; kept as a
separate concept from bills, see `app/models.py`'s `Publication`):

| Source | Access | Notes |
| --- | --- | --- |
| [PwC Tax Library](https://www.pwc.com/us/en/services/tax/library.html) | No auth required | Undocumented AEM endpoint (see `app/ingestion/pwc_tax_library.py`) — no official API/RSS exists, so this is more fragile to upstream site changes than the structured legislation sources above |
| [EY Tax Alerts](https://www.ey.com/en_gl/technical/tax-alerts) | No auth required | Undocumented search-API endpoint (see `app/ingestion/ey_tax_alerts.py`); the endpoint spans EY's *entire* global content index (~3,000 items, mostly non-tax "Immigration" alerts), so this pulls a bounded recent window and filters using EY's own `category_label` rather than trusting the page scope alone |
| [KPMG TaxNewsFlash Europe](https://kpmg.com/us/en/taxnewsflash/europe.html) | No auth required | Undocumented per-month AEM "gridlist" JSON endpoints embedded in the page's static HTML (see `app/ingestion/kpmg_taxnewsflash_europe.py`) — the whole page's history (~16 months, ~1,000 items) is re-pulled every run, relying on upsert idempotency like PwC |

Each publication gets a `relevant_jurisdiction`: authoritative when the source provides its
own (EY tags every item with a real jurisdiction, e.g. "Guinea", "European Union"), otherwise
a best-effort heuristic — PwC infers it from title/summary text via keyword matching (see
`app/ingestion/jurisdiction_detect.py`, limited to US states + "Federal"/"International"/
"Multistate"); KPMG extracts it from its own consistent "Country: ..." title convention (see
`_extract_jurisdiction` in `kpmg_taxnewsflash_europe.py`), covering European countries plus
"United Kingdom"/"European Union". Informational either way, not guaranteed accurate.

Each publication also gets an `ai_summary`: a short summary generated by Claude Haiku from the
title and source-provided summary/description, submitted via the Anthropic
[Batches API](https://platform.claude.com/docs/en/build-with-claude/batch-processing) (50%
cheaper than a plain request, and a better fit than an inline call per item since a single run
can touch hundreds of publications) — see `app/ingestion/summarize.py`. It runs as the last step
of the publication ingestion job, submitting one batch covering everything still missing a
summary, waiting up to `AI_SUMMARY_MAX_WAIT_SECONDS` for it to finish. Items a batch times out on
are left alone (not resubmitted) until `AI_SUMMARY_STALE_AFTER_HOURS` passes, so a slow batch
doesn't get re-billed by every subsequent run.

Bills get the same `ai_summary` treatment, generated from the bill's title and its own
(source-provided) legislative summary where one exists — useful even for the non-English sources
above, since Claude summarizes a Spanish/French/German title in English fine on its own. It uses
a bill-specific prompt (`BILL_PROMPT_TEMPLATE` in `summarize.py`) but otherwise shares the exact
same batch/poll/staleness machinery as publications (`Bill` and `Publication` expose the same
`id`/`title`/`summary`/`ai_summary`/`ai_summary_requested_at` shape, so the submit/poll/write-back
flow itself doesn't need to know which one it's given — see `_run_summary_batch`). It runs as the
last step of both the light- and heavy-source ingestion jobs, since bills come from both tiers;
each just submits a batch for whatever's still outstanding, so running it twice on the same cycle
is harmless.

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
   - `ANTHROPIC_API_KEY` — get one at https://console.anthropic.com/ (only needed for the
     `ai_summary` field on bills and publications; see Sources above)
   - `GERMANY_BUNDESTAG_API_KEY` — optional; `GermanyBundestagAdapter` ships with a working
     default (DIP's own public demo key, see Sources above), but heavy users should apply for
     their own free key at https://dip.bundestag.de/ueber-dip/hilfe/api
   - `OPENSTATES_API_KEY` — covers the other 48 US states; register a free key at
     https://open.pluralpolicy.com/accounts/profile/

   Sources without a configured key are skipped automatically (a warning is logged) — this
   doesn't apply to Germany, since it has a usable built-in default.

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
uv run scripts/run_scrape_once.py CA NY CANADA SPAIN UK INDIA FRANCE GERMANY SINGAPORE MEXICO PORTUGAL OPENSTATES
uv run scripts/run_scrape_once.py PWC_TAX_LIBRARY EY_TAX_ALERTS KPMG_TAXNEWSFLASH_EUROPE
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
│   ├── canada_legisinfo.py
│   ├── spain_congreso.py
│   ├── uk_parliament.py
│   ├── india_prs.py
│   ├── france_assemblee.py
│   ├── germany_bundestag.py
│   ├── singapore_parliament.py
│   ├── mexico_diputados.py
│   ├── portugal_parlamento.py
│   ├── openstates.py                # Other 48 US states (third-party aggregator)
│   ├── tax_filter.py           # Shared tax-relevance rules
│   ├── diff.py                  # Bill insert/update + change detection
│   ├── publications_base.py      # NormalizedPublication / PublicationSourceAdapter protocol
│   ├── publications_diff.py       # Publication insert/update (no change-log — see Sources above)
│   ├── jurisdiction_detect.py      # Best-effort jurisdiction heuristic (used by PwC)
│   ├── pwc_tax_library.py
│   ├── ey_tax_alerts.py
│   ├── kpmg_taxnewsflash_europe.py
│   ├── summarize.py                 # AI summary generation for bills + publications (Claude Haiku, Batches API)
│   ├── pipeline.py                 # run_all()/run_all_publications() — ingestion entry points
│   └── registry.py                 # Builds adapters from Settings
├── routes/               # dashboard, bills, feed, publications
└── templates/             # Jinja2 + HTMX, no JS build step
scripts/run_scrape_once.py  # Manual/CLI ingestion run
docs/SOURCES.md              # Per-source engineering rationale
```

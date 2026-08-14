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
| Canada (federal) | [LEGISinfo](https://www.parl.ca/legisinfo/) | No auth required |
| Spain (national) | [Congreso de los Diputados — búsqueda de iniciativas](https://www.congreso.es/es/busqueda-de-iniciativas) | No auth required |
| United Kingdom (national) | [UK Parliament Bills API](https://bills-api.parliament.uk/) | No auth required |
| India (national) | [PRS Legislative Research — Bills Track](https://prsindia.org/billtrack) | No auth required |
| France (national) | [Assemblée Nationale — Open Data](https://data.assemblee-nationale.fr/) | No auth required |
| Germany (national) | [Bundestag DIP API](https://dip.bundestag.de/) | Free API key required (a public demo key ships as the default — see below) |

Bills are filtered to tax-relevant ones using each source's own tax signal where available
(Congress.gov policy area "Taxation", California's `taxlevy` flag) with a shared keyword
fallback (see `app/ingestion/tax_filter.py`) — Canada, Spain, the UK, India, France, and Germany
have no such flag, so they fall back to keyword matching. Canada's is matched against the bill's
full legislative summary as well as its title (Canadian tax bills are often titled generically,
e.g. "Budget Implementation Act, 2026, No. 1"). Spain's, France's, and Germany's titles are in
Spanish/French/German, so separate `SPANISH_TAX_KEYWORDS`/`FRENCH_TAX_KEYWORDS`/
`GERMAN_TAX_KEYWORDS` lists are matched against the title (Spain: title only, no
legislative-summary text is available from that source; France: title only too — see below;
Germany: title and abstract, like Canada — see below). The UK's, India's, and France's main
annual tax bill are all literally titled some variant of "Finance Bill" ("Projet de loi de
finances" in French) with no "tax" wording at all, so that's special-cased the same way
Congress.gov's policy-area flag is, ahead of the keyword fallback, for all three. Germany needs
no such special case: German compounds tax terms directly into the word itself, so even its own
annual omnibus tax act ("Jahressteuergesetz") already contains "steuer" as a literal substring —
confirmed by checking every title in a real Wahlperiode-21 pull for false positives before
relying on it (see `app/ingestion/germany_bundestag.py`).

Canada re-pulls the current Parliament session's full bill list every run (a single request,
~200 bills), but only re-fetches per-bill detail — where the status timeline and legislative
summary live — for bills whose activity changed since the last run, so a steady-state run costs
one request instead of ~200 (see `app/ingestion/canada_legisinfo.py`).

Spain has no bill-specific API, but its legislative search tool has an undocumented "open data"
export (a plain POST returning XML/CSV, discovered by reading the search page's own JS —
`exportOpendata`/`downloadFile` — rather than a network capture) covering every parliamentary
"iniciativa", filterable by type. This adapter scopes to the bill-like types — government bills
("Proyecto de ley"), the four private-member's-bill variants, and royal decree-laws (Spain often
amends tax law by decree-law) — and re-pulls all of them (a few hundred items total) every run
like PwC/California, since no incremental filter is exposed. Status data here is thin (no dated
stage-by-stage timeline like Canada's): just presented/qualified dates and a final result once
resolved, so `full_text_url` and a real per-bill deep link aren't available (the site's own
detail-page links go through a legacy session-gated system) — `source_url` points at the search
tool itself (see `app/ingestion/spain_congreso.py`).

The UK is the cleanest of the non-US sources: an official, documented REST API
(`bills-api.parliament.uk`, confirmed via its own OpenAPI spec) with no auth, giving structured
sponsors, a full dated stage-by-stage timeline (`/Bills/{id}/Stages`), and a real per-bill page
(`bills.parliament.uk/bills/{id}`). It has no "current session" endpoint, so the adapter infers
one from the most-recently-updated bill rather than a hardcoded session number (unlike Spain's
`legislature` setting, which has no equivalent signal to derive it from). Like Spain, a session
is small enough (a couple hundred bills) to re-pull in full every run rather than filtering
incrementally. The API itself is unusually slow per request (observed ~10-15s per call in
practice), so the adapter fetches the (slow) stage timeline only for bills that already passed
the relevance check on the bill detail response, rather than for every bill in the session —
cutting a full run from ~30 minutes to a few minutes (see `app/ingestion/uk_parliament.py`).

India has no official structured bill-tracking API — the unified Parliament portal (sansad.in)
is a client-rendered SPA with no discoverable data endpoint — so this adapter uses
[PRS Legislative Research](https://prsindia.org/)'s public "Bills Track" page instead: an
independent, well-established legislative research organization (not a government body), server-
rendered with no JS required, covering every bill before Lok Sabha/Rajya Sabha with a real dated
status timeline and PRS's own plain-English bill summaries — richer than most of the official
sources above. Applying the same lesson learned from the UK, detail pages (which carry the
timeline and summary) are only fetched for bills that already pass the relevance check on title
alone, out of the ~1,000 bills in the full listing. No session/term identifier is exposed, so the
year embedded in the bill's title (Indian bills are consistently titled "..., 2026") stands in
for it (see `app/ingestion/india_prs.py`).

France is the only non-US source that needs no per-bill HTTP requests at all: its open data
portal publishes a ~10MB daily bulk zip of every "dossier législatif" (bill file) for a given
legislature as plain JSON, containing the full recursive procedural timeline (readings,
committee steps, votes, promulgation, arbitrarily deep) in the same download — closer to
California's bulk-snapshot model than to the per-bill APIs above. The archive covers many
non-bill dossier types too (ceremonial addresses, no-confidence motions, commissions of inquiry),
discriminated by an `@xsi:type` field — only `DossierLegislatif_Type` is kept. No legislative-
summary text is exposed here either, so the procedure label (e.g. "Proposition de loi ordinaire")
stands in for `summary`, and sponsor names aren't resolved (they're actor-ID references into a
separate, un-joined dataset) (see `app/ingestion/france_assemblee.py`).

Germany's Bundestag publishes DIP (Dokumentations- und Informationssystem für
Parlamentsmaterialien), an official, documented REST API with an OpenAPI spec — but unlike every
other non-US source above, it requires an API key on every request. The adapter's default key is
DIP's own publicly-documented demo key, embedded in that same OpenAPI spec's security-scheme
description and auto-preauthorized for every visitor to DIP's own Swagger UI — a shared, openly
published testing credential rather than a secret, though heavy users are expected to apply for
their own free key per DIP's terms. The `/vorgang` (legislative proceeding) list endpoint
supports the same updated-since filter as Canada's LEGISinfo (`f.aktualisiert.start`) and already
includes each item's summary (`abstract`) in the list response itself, so — better than the UK's
situation — the tax-relevance pre-filter needs no extra per-item request at all before deciding
whether the (still separate) `/vorgangsposition` status-timeline fetch is worth making. The
current electoral term (Wahlperiode 21) is a bounded dataset (~400 Gesetzgebung proceedings), so,
like Spain/UK, a full run re-pulls the whole list, filtered incrementally when `since` is
available (see `app/ingestion/germany_bundestag.py`).

California only publishes a full session snapshot once a day (its smaller daily delta file
omits bill titles/subjects), so it's ingested on its own, longer schedule rather than the
shared interval used for the other sources.

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
     `ai_summary` field on publications; see Sources above)
   - `GERMANY_BUNDESTAG_API_KEY` — optional; `GermanyBundestagAdapter` ships with a working
     default (DIP's own public demo key, see Sources above), but heavy users should apply for
     their own free key at https://dip.bundestag.de/ueber-dip/hilfe/api

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
uv run scripts/run_scrape_once.py CA NY CANADA SPAIN UK INDIA FRANCE GERMANY
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
│   ├── tax_filter.py           # Shared tax-relevance rules
│   ├── diff.py                  # Bill insert/update + change detection
│   ├── publications_base.py      # NormalizedPublication / PublicationSourceAdapter protocol
│   ├── publications_diff.py       # Publication insert/update (no change-log — see Sources above)
│   ├── jurisdiction_detect.py      # Best-effort jurisdiction heuristic (used by PwC)
│   ├── pwc_tax_library.py
│   ├── ey_tax_alerts.py
│   ├── kpmg_taxnewsflash_europe.py
│   ├── summarize.py                 # AI summary generation for publications (Claude Haiku, Batches API)
│   ├── pipeline.py                 # run_all()/run_all_publications() — ingestion entry points
│   └── registry.py                 # Builds adapters from Settings
├── routes/               # dashboard, bills, feed, publications
└── templates/             # Jinja2 + HTMX, no JS build step
scripts/run_scrape_once.py  # Manual/CLI ingestion run
```

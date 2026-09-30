# Tax Legislation Reporter

**Spend less time keeping up to date on the on the ever-shifting tax law landscape.**

TaxLegislationReporter pulls bills from official legislative sources (Congress.gov, all 50 US states, and national parliaments from the UK to Singapore), keeps only the tax-relevant ones, records every status change, and adds a short plain-English AI summary to each item, including bills written in Spanish, French, German, or Portuguese. Alongside legislation, it collects tax alerts and insights from PwC, EY, and KPMG.

---

## Coverage

### Legislation

| Jurisdiction | Source | API key |
| --- | --- | --- |
| 🇺🇸 United States (federal) | [Congress.gov API](https://api.congress.gov/) | Required (free) |
| 🇺🇸 California | [PUBINFO bulk data](https://downloads.leginfo.legislature.ca.gov/) | — |
| 🇺🇸 New York | [Open Legislation API](https://legislation.nysenate.gov/) | Required (free) |
| 🇺🇸 Other 48 states | [Open States](https://openstates.org/) bulk CSV | Required (free) |
| 🇨🇦 Canada | [LEGISinfo](https://www.parl.ca/legisinfo/) | — |
| 🇬🇧 United Kingdom | [UK Parliament Bills API](https://bills-api.parliament.uk/) | — |
| 🇫🇷 France | [Assemblée Nationale Open Data](https://data.assemblee-nationale.fr/) | — |
| 🇩🇪 Germany | [Bundestag DIP API](https://dip.bundestag.de/) | Optional (demo key built in) |
| 🇪🇸 Spain | [Congreso de los Diputados](https://www.congreso.es/es/busqueda-de-iniciativas) | — |
| 🇵🇹 Portugal | [Assembleia da República](https://www.parlamento.pt/ActividadeParlamentar/Paginas/IniciativasLegislativas.aspx) | — |
| 🇲🇽 Mexico | [Cámara de Diputados — Gaceta Parlamentaria](https://gaceta.diputados.gob.mx/gp_iniciativas.html) | — |
| 🇮🇳 India | [PRS Legislative Research](https://prsindia.org/billtrack) | — |
| 🇸🇬 Singapore | [Parliament of Singapore](https://www.parliament.gov.sg/parliamentary-business/bills-introduced) | — |


### Publications

| Source | Coverage |
| --- | --- |
| [PwC Tax Library](https://www.pwc.com/us/en/services/tax/library.html) | US federal, state, and international tax insights |
| [EY Tax Alerts](https://www.ey.com/en_gl/technical/tax-alerts) | Global tax alerts, tagged by jurisdiction |
| [KPMG TaxNewsFlash Europe](https://kpmg.com/us/en/taxnewsflash/europe.html) | European country and EU tax news |


**[docs/SOURCES.md](docs/SOURCES.md)** explains how each adapter works, what was tried and rejected, and each source's known limitations.

## Requirements

- [uv](https://docs.astral.sh/uv/) for Python/dependency management
- Docker (for a local Postgres instance) — or your own PostgreSQL 16+ instance

---

## Quickstart

You'll need [uv](https://docs.astral.sh/uv/) and either Docker or your own PostgreSQL 16+ instance.

```bash
git clone https://github.com/phxie/TaxLegislationReporter.git
cd TaxLegislationReporter

uv sync                      # install dependencies
docker compose up -d         # start Postgres
cp .env.example .env         # then add your API keys (see below)
uv run alembic upgrade head  # create the database schema

uv run scripts/run_scrape_once.py      # first ingestion pass
uv run uvicorn app.main:app --reload   # start the dashboard
```

Open **http://127.0.0.1:8000** for bills or **http://127.0.0.1:8000/publications** for publications.

## Configuration

API keys go in `.env`. Every key is free, and **any source without a key is skipped with a logged warning**, so you can start with none and add them as you go.

| Variable | Enables | Get one |
| --- | --- | --- |
| `CONGRESS_API_KEY` | US federal bills | [api.congress.gov/sign-up](https://api.congress.gov/sign-up/) |
| `NY_SENATE_API_KEY` | New York bills | [legislation.nysenate.gov](https://legislation.nysenate.gov/static/docs/html/index.html) |
| `OPENSTATES_API_KEY` | The other 48 US states | [open.pluralpolicy.com](https://open.pluralpolicy.com/accounts/profile/) |
| `ANTHROPIC_API_KEY` | AI summaries on bills and publications | [console.anthropic.com](https://console.anthropic.com/) |
| `GERMANY_BUNDESTAG_API_KEY` | Optional — a public demo key is built in; heavy users should request their own | [dip.bundestag.de](https://dip.bundestag.de/ueber-dip/hilfe/api) |

---

## Usage
Ingestion runs automatically on the schedule above. To run a pass manually:
```bash
uv run scripts/run_scrape_once.py                    # all configured sources
uv run scripts/run_scrape_once.py FEDERAL CA NY PWC_TAX_LIBRARY     # specific sources

```
Available source names: `FEDERAL`, `CA`, `NY`, `OPENSTATES`, `CANADA`, `UK`, `FRANCE`, `GERMANY`, `SPAIN`, `PORTUGAL`, `MEXICO`, `INDIA`, `SINGAPORE`, `PWC_TAX_LIBRARY`, `EY_TAX_ALERTS`, `KPMG_TAXNEWSFLASH_EUROPE`.



<details>
<summary><strong>Project layout</strong></summary>

```
app/
├── main.py            # FastAPI app + scheduler lifespan
├── config.py          # Settings (.env)
├── db.py              # SQLAlchemy engine/session
├── models.py          # Bill, BillStatusEvent, BillChange, Publication, IngestionRun
├── repository.py      # Query helpers for the dashboard
├── scheduler.py       # APScheduler wiring
├── ingestion/
│   ├── base.py                    # SourceAdapter protocol (legislation)
│   ├── publications_base.py       # PublicationSourceAdapter protocol
│   ├── <source>.py                # One adapter per source
│   ├── tax_filter.py              # Shared tax-relevance rules
│   ├── diff.py                    # Bill upsert + change detection
│   ├── publications_diff.py       # Publication upsert
│   ├── jurisdiction_detect.py     # Jurisdiction heuristic (PwC)
│   ├── summarize.py               # AI summaries (Claude Haiku, Batches API)
│   ├── pipeline.py                # run_all() / run_all_publications()
│   └── registry.py                # Builds adapters from Settings
├── routes/            # dashboard, bills, feed, publications
└── templates/         # Jinja2 + HTMX
scripts/run_scrape_once.py   # Manual ingestion run
docs/SOURCES.md              # Per-source engineering notes
```

</details>

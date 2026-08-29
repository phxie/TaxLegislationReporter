# Data source notes

Detailed engineering rationale for each ingestion adapter: why it's built the way it is, what
was tried and rejected, and known limitations. For the source list, access requirements, and
how to configure keys, see the [main README](../README.md#sources).

## Tax-relevance filtering

Bills are filtered to tax-relevant ones using each source's own tax signal where available
(Congress.gov policy area "Taxation", California's `taxlevy` flag) with a shared keyword
fallback (see `app/ingestion/tax_filter.py`) — Canada, Spain, the UK, India, France, Germany,
Singapore, Mexico, Portugal, and Open States have no such flag, so they fall back to keyword
matching. Canada's is matched against the bill's full legislative summary as well as its title
(Canadian tax bills are often titled generically, e.g. "Budget Implementation Act, 2026, No.
1"). Spain's, France's, Germany's, Mexico's, and Portugal's titles are in
Spanish/French/German/Portuguese, so separate
`SPANISH_TAX_KEYWORDS`/`FRENCH_TAX_KEYWORDS`/`GERMAN_TAX_KEYWORDS`/`MEXICO_TAX_KEYWORDS`/`PORTUGAL_TAX_KEYWORDS`
lists are matched against the title (Spain: title only, no legislative-summary text is available
from that source; France: title only too — see below; Germany: title and abstract, like Canada —
see below). The UK's, India's, and France's main annual tax bill are all literally titled some
variant of "Finance Bill" ("Projet de loi de finances" in French) with no "tax" wording at all,
so that's special-cased the same way Congress.gov's policy-area flag is, ahead of the keyword
fallback, for all three. Germany needs no such special case: German compounds tax terms directly
into the word itself, so even its own annual omnibus tax act ("Jahressteuergesetz") already
contains "steuer" as a literal substring — confirmed by checking every title in a real
Wahlperiode-21 pull for false positives before relying on it (see
`app/ingestion/germany_bundestag.py`). Singapore is English-speaking, but its bill titles
surfaced a substring pitfall the shared `matching_keywords()` helper doesn't guard against: a
real bill titled "Third-Party Taxi Booking Service Providers Bill" contains "tax" inside "Taxi".
`SINGAPORE_TAX_KEYWORDS` is matched with a whole-word regex instead of plain substring matching
to avoid that (and equivalents like "duty"/"gst"/"customs"), validated against the full ~770-bill
historical dataset before relying on it (see `app/ingestion/singapore_parliament.py`). Mexico hit
an even sharper version of the same problem: Spanish "fiscal" means both "tax-related" (Código
Fiscal, Coordinación Fiscal) and "prosecutorial/audit" (Fiscalía General, fiscalización) — real
homonyms, not just a shared substring — so `MEXICO_TAX_KEYWORDS` also uses whole-word matching,
which resolves it cleanly since "fiscalía"/"fiscalización" extend past the word boundary;
"hacienda" was deliberately left out of the list after checking its real matches turned out to
all be non-tax bills (see `app/ingestion/mexico_diputados.py`). Portuguese hits the same two
substring problems as Mexican Spanish ("fiscal" vs. "fiscalização"; "IVA" is also a substring of
extremely common words ending in "-iva" like "contributiva" — 25 of 27 substring hits in a real
sample were exactly this), both resolved the same way with whole-word matching — plus a third,
genuine homonym with no substring fix at all: "imposto" is spelled identically whether it means
the noun "tax" or the past participle of "impor" ("imposed"), e.g. "o plano ... imposto pelos
Estados Unidos" ("the plan ... imposed by the United States"). Since every real false positive
followed that exact "imposto por/pelo/pela" passive-voice pattern, it's excluded with a negative
lookahead instead of dropping "imposto" from the list entirely (see
`app/ingestion/portugal_parlamento.py`). Open States (English, but covering all 48 other US
states) hit the same "Taxi"-style substring risk as Singapore, so `is_tax_relevant_openstates`
also uses whole-word matching against the same shared `TAX_KEYWORDS` list rather than the
substring-based `matching_keywords()` (see `app/ingestion/openstates.py`).

## Legislation sources

### Canada

Canada re-pulls the current Parliament session's full bill list every run (a single request,
~200 bills), but only re-fetches per-bill detail — where the status timeline and legislative
summary live — for bills whose activity changed since the last run, so a steady-state run costs
one request instead of ~200 (see `app/ingestion/canada_legisinfo.py`).

### Spain

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

### United Kingdom

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

### India

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

### France

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

### Germany

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

### Singapore

Singapore's Parliament site has no documented API and, unlike every other undocumented-endpoint
source above, isn't a plain REST/JSON backend either: it's a Next.js App Router site where the
bill list's pagination is wired to a React Server Action rather than a URL or query params,
invoked by POSTing back to the page itself with a `Next-Action: <id>` header, where `<id>` is a
content hash of the current JS build. This was found with a throwaway Playwright spike (removed
again afterwards, same as the PwC adapter's precedent — it's not a runtime dependency) to capture
the browser's real request, since the extra pages aren't present in any static HTML. The adapter
re-discovers the current `<id>` on every run — fetching the page, then scanning its referenced JS
chunks for the `createServerReference(...)` call that names it — rather than hardcoding a value
that would silently go stale on the site's next deploy; if the site's framework or that action's
name ever changes, discovery raises loudly instead of returning nothing. This makes Singapore the
most fragile source in this project (coupled to Next.js's build output shape, not just a stable
URL), which is called out here rather than left implicit. No legislative-summary text or sponsor
data is exposed, and there's no per-bill deep link (`source_url` falls back to the bills-introduced
page itself, like Spain), but each bill's actual PDF is directly linkable via a stable
UUID-keyed media endpoint (see `app/ingestion/singapore_parliament.py`).

### Mexico

Mexico's Chamber of Deputies has no official structured bill API either — its own legislative-
tracking portal (`sil.gobernacion.gob.mx`) redirects to a domain (`nsil.gobernacion.gob.mx`) that
no longer resolves — so this adapter uses "Gaceta Parlamentaria" instead: the Chamber's official
legislative record, whose iniciativas (bills) index page links out to one plain server-rendered
HTML page per legislative period, each listing every bill introduced in that period with its
title, sponsor, committee referral, and a dated link into that day's Gaceta issue. Like Spain's
site, this needed no headless browser — just handling the page's original iso-8859-1 encoding,
which isn't declared in the HTTP response's `Content-Type` header (only the page's own `<meta>`
tag, which `httpx` doesn't inspect, so it's set explicitly). The index page goes back to 1997;
the adapter dynamically follows only the numerically highest ("current") legislature's period
links rather than a hardcoded value, since the index page itself exposes it — self-updating
across a legislature change, unlike Spain's/Germany's `legislature` settings, which have no such
signal to derive it from. A legislature's full history to date (~9 period pages, ~7,000 bills as
of the LXVI legislature) is re-pulled every run, since no incremental filter is exposed; given
that volume, this is in the "heavy" adapter tier alongside California/France rather than
alongside Spain/UK/Germany/Singapore (see `app/ingestion/mexico_diputados.py`).

### Portugal

Portugal's Assembleia da República has no REST/JSON API either — its "Dados Abertos" (open data)
download page is an old SharePoint document library whose file links are built client-side from
an encrypted parameter, confirmed unworkable even with a headless browser (renders blank), and
a community-maintained mirror of its direct download URLs is years stale. This adapter instead
scrapes the live "Iniciativas Legislativas" search page directly — a classic ASP.NET WebForms
page whose default view already lists the current legislature's bills. Both it and the per-bill
detail page (addressed by a stable numeric "BID", giving status timeline and sponsors) are plain
server-rendered HTML, but the listing's pagination has no URL/query-string form at all: it's
wired to `__doPostBack`, requiring the page's own `__VIEWSTATE`/`__VIEWSTATEGENERATOR`/
`__EVENTVALIDATION` hidden fields to be replayed on each POST, chained page to page, following
each response's own "next page" link rather than a hardcoded page-number pattern. Confirmed
reproducible with plain `httpx` (validated with a throwaway Playwright spike, removed again
afterwards — not a runtime dependency, same as the PwC/Singapore precedent), making this the
most fragile source in the project by mechanism even though the underlying dataset is small and
bounded (~220 bills for the current legislature, re-pulled in full every run like Spain/UK). The
site is also unusually slow with high latency variance — a live run observed individual requests
taking anywhere from ~10s to ~90s — so this adapter's `httpx` client uses a longer timeout (90s)
than every other adapter's default 30s (see `app/ingestion/portugal_parlamento.py`).

### Open States (other 48 US states)

California and New York already have dedicated adapters pulling directly from those states' own
legislatures, so Open States — a third-party aggregator (run by Plural Policy, not any state
government) covering all 50 states — is scoped to just the other 48 rather than duplicating
those two (see `OPENSTATES_JURISDICTIONS` in `app/models.py`).

The first version of this adapter used Open States' live `/bills` API with a server-side `q=tax`
search. Live testing found two problems that ruled it out for anything beyond spot-checking: (1)
the free tier is rate-limited to 10 requests/minute — confirmed live (a real 429 with
`{"detail":"exceeded limit of 10/min: N"}`) — and under sustained testing this can degrade into a
much longer penalty window than 60 seconds; (2) `q=tax` is a full-text search over the entire
bill, not just the title, so it barely narrows anything: Texas's single most recent special
session alone had 314 pages (~6,280 bills) of "tax" matches out of ~700 total bills in that
session.

This version uses Open States' bulk CSV export instead: each legislative session has a
`downloads` entry (found via `/jurisdictions/{id}?include=legislative_sessions` — still
API-rate-limited, but just one call per configured state) pointing to a zip hosted on a separate,
unauthenticated, non-rate-limited static host (`data.openstates.org`, served via S3/CloudFront).
The zip contains the whole session's bills as plain relational CSVs (bills/actions/
sponsorships/versions, joined by bill id) — the same bulk-snapshot shape as the
California/France adapters, just CSV instead of a proprietary dump/XML. Relevance is checked
with the *same* `is_tax_relevant_openstates` used for the title, just also given the bill's
`subject` categories as the "summary" argument — Open States' own subject taxonomy already
includes a "Taxation--\*" family (e.g. "Taxation--Property-Exemptions"), which the existing
word-boundary "taxation" keyword catches for free. Confirmed on Texas's most recent special
session that this combination (title keywords ∪ subject taxonomy) finds 114 relevant bills, 20
of which a title-only keyword check would have missed entirely.

Because the free-tier rate limit is real and was observed to degrade further under sustained
back-to-back runs, the session-discovery call's retry budget is deliberately generous (up to
~100s across 5 retries, vs. every other adapter's ~15s) rather than proactively pacing requests —
a state that still can't resolve its session after that is skipped and logged rather than
failing the whole 48-state run, and picked up again on the next scheduled run.

Two bugs were found and fixed during live 48-state verification, both now covered by regression
tests: (1) a state's export can omit a CSV entirely when that table has zero rows for the session
(confirmed live: Kentucky's export had no `bill_sponsorships.csv` file at all) — this used to
raise and abort every state processed after it in the same run, now treated as an empty table
for anything but `bills.csv` itself; (2) Open States' bill IDs are `ocd-bill/<uuid>` — a literal
`/` — which broke the dashboard's bill-detail routing (every other source's IDs happen to be
slash-free, so this never surfaced before); fixed by stripping the constant `ocd-bill/` prefix
at normalization time, since it carries no distinguishing information (see
`app/ingestion/openstates.py`).

### California

California only publishes a full session snapshot once a day (its smaller daily delta file
omits bill titles/subjects), so it's ingested on its own, longer schedule rather than the
shared interval used for the other sources.

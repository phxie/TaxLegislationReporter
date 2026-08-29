from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import zipfile
from collections import defaultdict
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_openstates

logger = logging.getLogger(__name__)

# Unlike every other source in this project, Open States is a *third-party
# aggregator* (run by Plural Policy, not any state government), covering
# the other 48 US states through one documented REST API + bulk data
# exports -- California and New York already have dedicated adapters
# pulling directly from those states' own legislatures, so this is
# deliberately scoped to everything else rather than duplicating them (see
# OPENSTATES_JURISDICTIONS in app/models.py).
#
# First version of this adapter used the live /bills API with a server-side
# `q=tax` search. Live testing found two problems that ruled it out for
# anything beyond spot-checking: (1) the free tier is rate-limited to 10
# requests/minute -- confirmed live (a real 429 with `{"detail":"exceeded
# limit of 10/min: 14"}`), and (2) `q=tax` is a full-text search over the
# entire bill, not just the title, so it barely narrows anything: Texas's
# single most recent special session alone had 314 pages (~6,280 bills) of
# "tax" matches out of ~700 total bills in that session -- paging through
# that at 10 req/min would take over half an hour for one state's one
# session alone.
#
# This version uses Open States' bulk CSV export instead: each legislative
# session has a `downloads` entry (found via `/jurisdictions/{id}?
# include=legislative_sessions`, still API-rate-limited but just one call
# per configured state) pointing to a zip hosted on a separate,
# unauthenticated, non-rate-limited static host (data.openstates.org,
# served via S3/CloudFront) -- confirmed live: no API key needed, no rate
# limit hit downloading and re-downloading it repeatedly. The zip contains
# the whole session's bills as plain relational CSVs (bills/actions/
# sponsorships/versions, joined by bill id) -- the same bulk-snapshot shape
# as the California/France adapters, just CSV instead of a proprietary
# dump/XML. Relevance is checked with the *same* `is_tax_relevant_openstates`
# used for the title, just also given the bill's `subject` categories as
# the "summary" argument -- Open States' own subject taxonomy already
# includes a "Taxation--*" family (e.g. "Taxation--Property-Exemptions"),
# which the existing word-boundary "taxation" keyword catches for free.
# Confirmed on Texas's most recent special session that this combination
# (title keywords ∪ subject taxonomy) finds 114 relevant bills, 20 of which
# a title-only keyword check would have missed entirely.
API_BASE_URL = "https://v3.openstates.org"

# Open States' own jurisdiction names (e.g. "California") map to this
# project's short internal jurisdiction codes; use USPS two-letter codes
# for states not already covered by a dedicated adapter.
US_STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC", "Puerto Rico": "PR",
}

# The other 48 US states -- excludes California and New York (dedicated
# adapters already exist for those) and DC/Puerto Rico (not states).
_EXCLUDED_STATE_NAMES = {"California", "New York", "District of Columbia", "Puerto Rico"}
DEFAULT_JURISDICTIONS = tuple(
    sorted(name for name in US_STATE_CODES if name not in _EXCLUDED_STATE_NAMES)
)


class OpenStatesAdapter:
    source_name = "OPENSTATES"
    source_label = "Open States"

    def __init__(self, api_key: str, jurisdictions: list[str], base_url: str = API_BASE_URL):
        self.api_key = api_key
        self.jurisdictions = jurisdictions
        self.base_url = base_url.rstrip("/")
        self._api_client = httpx.Client(timeout=30.0, headers={"X-API-KEY": api_key})
        self._download_client = httpx.Client(timeout=120.0, follow_redirects=True)

    def close(self) -> None:
        self._api_client.close()
        self._download_client.close()

    # The 10/min free-tier rate limit (confirmed live: a real 429 with
    # `{"detail":"exceeded limit of 10/min: N"}`, no Retry-After header) is
    # routinely hit partway through a 48-state run, since each state needs
    # one of these calls. The default retry budget used elsewhere in this
    # project (~15s worst case) isn't long enough to reliably outlast a 60s
    # window -- confirmed live: 8 of 48 states were silently skipped in one
    # run with the shorter budget. This waits long enough (worst case ~100s
    # across 5 retries) to comfortably clear the window even in unlucky
    # timing, at the cost of extra latency only for the specific states
    # that collide with it.
    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=3, min=8, max=70),
        reraise=True,
    )
    def _fetch_legislative_sessions(self, jurisdiction: str) -> list[dict]:
        code = US_STATE_CODES.get(jurisdiction, jurisdiction).lower()
        ocd_id = f"ocd-jurisdiction/country:us/state:{code}/government"
        resp = self._api_client.get(
            f"{self.base_url}/jurisdictions/{ocd_id}", params={"include": "legislative_sessions"}
        )
        resp.raise_for_status()
        return resp.json().get("legislative_sessions") or []

    def _current_session_download_url(self, jurisdiction: str) -> tuple[str, str] | None:
        """Returns (session_identifier, csv_download_url) for the most
        recent session that actually has a bulk CSV export, or None if no
        session has one yet.
        """
        sessions = self._fetch_legislative_sessions(jurisdiction)
        candidates = [
            s
            for s in sessions
            if any(d.get("data_type") == "csv" for d in s.get("downloads") or [])
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda s: s.get("start_date") or "")
        csv_download = next(d for d in latest["downloads"] if d.get("data_type") == "csv")
        return latest["identifier"], csv_download["url"]

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _download_zip(self, url: str) -> bytes:
        # Unauthenticated static host (S3/CloudFront), not subject to the
        # API's 10/min rate limit -- confirmed live.
        with self._download_client.stream("GET", url) as resp:
            resp.raise_for_status()
            buffer = io.BytesIO()
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                buffer.write(chunk)
            return buffer.getvalue()

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # No incremental filter used: each run re-pulls the current
        # session's full bulk snapshot (small -- a few hundred to a couple
        # thousand bills per state per session) and the diff pipeline
        # detects what's actually new, same as California/Spain/UK.
        for jurisdiction in self.jurisdictions:
            try:
                resolved = self._current_session_download_url(jurisdiction)
            except httpx.HTTPError:
                logger.warning(
                    "Skipping Open States jurisdiction %s: could not resolve current session", jurisdiction
                )
                continue
            if resolved is None:
                logger.warning(
                    "Skipping Open States jurisdiction %s: no bulk CSV export available", jurisdiction
                )
                continue

            session_id, download_url = resolved
            try:
                zip_bytes = self._download_zip(download_url)
            except httpx.HTTPError:
                logger.warning(
                    "Skipping Open States jurisdiction %s session %s: export download failed",
                    jurisdiction,
                    session_id,
                )
                continue

            # A parsing failure for one state's export (a corrupt zip, a
            # missing/renamed CSV, an unexpected row shape, ...) shouldn't
            # take down the other 47 -- confirmed necessary live: Kentucky's
            # export was missing its bill_sponsorships.csv entirely (an
            # empty-table export omits the file rather than writing a
            # header-only one, now handled below), which used to raise and
            # abort every jurisdiction after it in the same run. Caught
            # broadly and deliberately (rather than enumerating specific
            # exception types) since the parse step touches a third party's
            # export whose exact failure modes aren't fully known.
            try:
                yield from _parse_session_zip(zip_bytes)
            except Exception as exc:
                logger.warning(
                    "Skipping Open States jurisdiction %s session %s: export parsing failed (%s)",
                    jurisdiction,
                    session_id,
                    exc,
                )
                continue


def _find_csv(zf: zipfile.ZipFile, suffix: str) -> str | None:
    for name in zf.namelist():
        if name.endswith(suffix):
            return name
    return None


def _read_csv(zf: zipfile.ZipFile, name: str | None) -> list[dict]:
    # A missing optional table means the export simply had zero rows for
    # it (confirmed live: Kentucky's export omitted bill_sponsorships.csv
    # entirely rather than including an empty/header-only file), not a
    # broken export.
    if name is None:
        return []
    with zf.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))


def _parse_session_zip(zip_bytes: bytes) -> Iterator[NormalizedBill]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        bills_csv = _find_csv(zf, "_bills.csv")
        if bills_csv is None:
            raise RuntimeError("Open States bulk export has no *_bills.csv file")
        bills = _read_csv(zf, bills_csv)
        actions = _read_csv(zf, _find_csv(zf, "_bill_actions.csv"))
        sponsorships = _read_csv(zf, _find_csv(zf, "_bill_sponsorships.csv"))
        versions = _read_csv(zf, _find_csv(zf, "_bill_versions.csv"))
        version_links = _read_csv(zf, _find_csv(zf, "_bill_version_links.csv"))

    actions_by_bill: dict[str, list[dict]] = defaultdict(list)
    for action in actions:
        actions_by_bill[action["bill_id"]].append(action)

    sponsors_by_bill: dict[str, list[str]] = defaultdict(list)
    for sponsorship in sponsorships:
        if sponsorship.get("name"):
            sponsors_by_bill[sponsorship["bill_id"]].append(sponsorship["name"])

    versions_by_bill: dict[str, list[dict]] = defaultdict(list)
    for version in versions:
        versions_by_bill[version["bill_id"]].append(version)

    links_by_version: dict[str, list[dict]] = defaultdict(list)
    for link in version_links:
        links_by_version[link["version_id"]].append(link)

    for bill in bills:
        normalized = _normalize(
            bill, actions_by_bill, sponsors_by_bill, versions_by_bill, links_by_version
        )
        if normalized is not None:
            yield normalized


def _normalize(
    bill: dict,
    actions_by_bill: dict[str, list[dict]],
    sponsors_by_bill: dict[str, list[str]],
    versions_by_bill: dict[str, list[dict]],
    links_by_version: dict[str, list[dict]],
) -> NormalizedBill | None:
    bill_id = bill.get("id")
    title = bill.get("title")
    identifier = bill.get("identifier")
    if not bill_id or not title or not identifier:
        return None

    is_relevant, matched = is_tax_relevant_openstates(title, bill.get("subject"))
    if not is_relevant:
        return None

    state_name = bill.get("jurisdiction")
    jurisdiction_code = US_STATE_CODES.get(state_name, state_name or "UNKNOWN")
    session = bill.get("session_identifier") or "unknown"

    status_events = _extract_status_events(actions_by_bill.get(bill_id, []))
    introduced_date = min((e.event_date for e in status_events), default=None)
    last_action_date = max((e.event_date for e in status_events), default=None)
    status_text = status_events[-1].action_text if status_events else None

    source_url = (
        f"https://openstates.org/{jurisdiction_code.lower()}/bills/{session}/"
        f"{identifier.replace(' ', '')}/"
    )

    return NormalizedBill(
        jurisdiction=jurisdiction_code,
        # Every other source's source_bill_id is slash-free -- the
        # dashboard's bill-detail route/links build a path from it
        # unescaped, so a literal "/" in the raw "ocd-bill/<uuid>" form
        # breaks routing (confirmed live: a real 404). "ocd-bill/" is a
        # constant prefix carrying no distinguishing information, so this
        # strips it rather than encoding it.
        source_bill_id=bill_id.removeprefix("ocd-bill/"),
        session=session,
        bill_number=identifier,
        title=title,
        source_label="Open States",
        sponsors=sponsors_by_bill.get(bill_id, []),
        status_text=status_text,
        introduced_date=introduced_date,
        last_action_date=last_action_date,
        full_text_url=_full_text_url(bill_id, versions_by_bill, links_by_version),
        source_url=source_url,
        tax_keywords_matched=matched,
        raw_source_payload=bill,
        status_events=status_events,
    )


def _full_text_url(
    bill_id: str,
    versions_by_bill: dict[str, list[dict]],
    links_by_version: dict[str, list[dict]],
) -> str | None:
    bill_versions = sorted(
        versions_by_bill.get(bill_id, []), key=lambda v: v.get("date") or "", reverse=True
    )
    for version in bill_versions:
        links = links_by_version.get(version["id"], [])
        if links:
            return links[0].get("url")
    return None


def _extract_status_events(actions: list[dict]) -> list[NormalizedStatusEvent]:
    def _order(action: dict) -> int:
        try:
            return int(action.get("order") or 0)
        except ValueError:
            return 0

    events: list[NormalizedStatusEvent] = []
    for action in sorted(actions, key=_order):
        event_date = _parse_date(action.get("date"))
        description = action.get("description")
        if event_date is None or not description:
            continue
        events.append(NormalizedStatusEvent(event_date=event_date, action_text=description))
    return events


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None

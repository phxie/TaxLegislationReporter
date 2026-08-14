from __future__ import annotations

import datetime as dt
import html
import logging
import re
from collections.abc import Iterator
from urllib.parse import quote

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_germany

logger = logging.getLogger(__name__)

# DIP (Dokumentations- und Informationssystem fuer Parlamentsmaterialien) is
# the Bundestag's official, documented REST API (OpenAPI spec at
# /api/v1/openapi.yaml) -- but every request needs an API key, unlike the
# other "no auth required" sources. The default below is the public demo key
# published in that same OpenAPI spec's own security-scheme description
# (`R2BZaee...`), which DIP's own Swagger UI auto-preauthorizes every visitor
# with -- i.e. a shared, intentionally-public testing credential, not a
# secret. Heavy users are expected to apply for their own free key per
# https://dip.bundestag.de/ueber-dip/hilfe/api.
#
# `/vorgang` (list of legislative proceedings, "Vorgaenge") supports the same
# incremental-update filter as Canada's LEGISinfo (`f.aktualisiert.start`,
# an updated-since timestamp) and already includes each item's `abstract`
# (summary) in the list response -- so, unlike the UK, the tax-relevance
# pre-filter needs no extra per-item request at all before deciding whether
# a bill is worth the (still-separate) status-timeline fetch. The current
# electoral term (Wahlperiode 21, elected 2025) is a bounded dataset (~400
# Gesetzgebung proceedings), so a full run re-pulls the whole list, same as
# Spain/UK.
VORGANG_PATH = "/vorgang"
VORGANGSPOSITION_PATH = "/vorgangsposition"

DEFAULT_API_KEY = "R2BZaee.DjdCyihKZMf8AOjtScubP2EVydegzjmBIQ"

_TAG_RE = re.compile(r"<[^>]+>")
_SLUG_RE = re.compile(r"[^\w]+", re.UNICODE)


class GermanyBundestagAdapter:
    source_name = "GERMANY"
    source_label = "Deutscher Bundestag (DIP)"

    def __init__(
        self,
        base_url: str = "https://search.dip.bundestag.de/api/v1",
        api_key: str = DEFAULT_API_KEY,
        wahlperiode: str = "21",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.wahlperiode = wahlperiode
        self._client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _get(self, path: str, **params: object) -> dict:
        query = {k: v for k, v in params.items() if v is not None}
        query["apikey"] = self.api_key
        resp = self._client.get(f"{self.base_url}{path}", params=query)
        resp.raise_for_status()
        return resp.json()

    def _fetch_vorgang_page(self, cursor: str | None, since: dt.datetime | None) -> dict:
        params: dict[str, object] = {
            "f.vorgangstyp": "Gesetzgebung",
            "f.wahlperiode": self.wahlperiode,
        }
        if since is not None:
            params["f.aktualisiert.start"] = since.isoformat()
        if cursor is not None:
            params["cursor"] = cursor
        return self._get(VORGANG_PATH, **params)

    def _fetch_vorgangsposition_page(self, vorgang_id: str, cursor: str | None) -> dict:
        params: dict[str, object] = {"f.vorgang": vorgang_id}
        if cursor is not None:
            params["cursor"] = cursor
        return self._get(VORGANGSPOSITION_PATH, **params)

    def _fetch_all_vorgangspositionen(self, vorgang_id: str) -> list[dict]:
        # Same cursor-until-unchanged pagination contract as `/vorgang`; a
        # single Vorgang's steps rarely exceed one page, but this handles it
        # correctly regardless.
        positions: list[dict] = []
        cursor: str | None = None
        while True:
            page = self._fetch_vorgangsposition_page(vorgang_id, cursor)
            positions.extend(page.get("documents") or [])
            new_cursor = page.get("cursor")
            if new_cursor == cursor:
                break
            cursor = new_cursor
        return positions

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        cursor: str | None = None
        while True:
            try:
                page = self._fetch_vorgang_page(cursor, since)
            except httpx.HTTPError:
                logger.warning("Aborting Germany fetch due to a request failure")
                return

            for doc in page.get("documents") or []:
                vorgang_id = doc.get("id")
                title = doc.get("titel")
                if not vorgang_id or not title:
                    continue

                is_relevant, matched = is_tax_relevant_germany(title, doc.get("abstract"))
                if not is_relevant:
                    continue

                try:
                    positions = self._fetch_all_vorgangspositionen(vorgang_id)
                except httpx.HTTPError:
                    logger.warning("Skipping Germany Vorgang %s due to a request failure", vorgang_id)
                    continue

                normalized = self._normalize(doc, positions, matched)
                if normalized is not None:
                    yield normalized

            new_cursor = page.get("cursor")
            if new_cursor == cursor:
                break
            cursor = new_cursor

    def _normalize(self, doc: dict, positions: list[dict], matched: list[str]) -> NormalizedBill | None:
        vorgang_id = doc.get("id")
        title = doc.get("titel")
        if not vorgang_id or not title:
            return None

        summary_raw = doc.get("abstract")
        summary = _strip_html(summary_raw) if summary_raw else None

        status_events = _extract_status_events(positions)
        first_position = min(
            (p for p in positions if p.get("datum")),
            key=lambda p: p["datum"],
            default=None,
        )
        bill_number = None
        pdf_url = None
        if first_position is not None:
            fundstelle = first_position.get("fundstelle") or {}
            bill_number = fundstelle.get("dokumentnummer")
            pdf_url = fundstelle.get("pdf_url")
        if not bill_number:
            bill_number = f"Vorgang {vorgang_id}"

        fallback_date = _parse_date(doc.get("datum"))
        introduced_date = min((e.event_date for e in status_events), default=fallback_date)
        last_action_date = max((e.event_date for e in status_events), default=fallback_date)

        source_url = f"https://dip.bundestag.de/vorgang/{_slugify(title)}/{vorgang_id}"

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=vorgang_id,
            session=str(self.wahlperiode),
            bill_number=bill_number,
            title=title,
            source_label=self.source_label,
            summary=summary,
            sponsors=doc.get("initiative") or [],
            status_text=doc.get("beratungsstand"),
            status_code=None,
            last_action_date=last_action_date,
            introduced_date=introduced_date,
            full_text_url=pdf_url,
            source_url=source_url,
            tax_keywords_matched=matched,
            raw_source_payload=doc,
            status_events=status_events,
        )


def _extract_status_events(positions: list[dict]) -> list[NormalizedStatusEvent]:
    events: list[NormalizedStatusEvent] = []
    for pos in positions:
        event_date = _parse_date(pos.get("datum"))
        action_text = pos.get("vorgangsposition")
        if event_date is None or not action_text:
            continue
        events.append(
            NormalizedStatusEvent(
                event_date=event_date,
                action_text=action_text,
                action_code=pos.get("zuordnung"),
            )
        )
    return sorted(events, key=lambda e: e.event_date)


def _slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return quote(slug, safe="")


def _strip_html(value: str) -> str:
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None

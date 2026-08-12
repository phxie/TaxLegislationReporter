from __future__ import annotations

import datetime as dt
import io
import json
import logging
import zipfile
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_france

logger = logging.getLogger(__name__)

# The French National Assembly's open data portal publishes a daily bulk
# JSON export of every "dossier legislatif" (bill file) for a given
# legislature as a single zip -- no API, no auth, no pagination, and (unlike
# every other bill source in this app) no per-bill HTTP requests at all:
# everything, including the full procedural timeline, is already in the one
# download. Confirmed to work standalone via plain HTTP.
ZIP_PATH = "/static/openData/repository/{legislature}/loi/dossiers_legislatifs/Dossiers_Legislatifs.json.zip"
DOSSIER_PREFIX = "json/dossierParlementaire/"

# The archive's "dossierParlementaire" entries cover many non-bill
# parliamentary procedures (ceremonial addresses, no-confidence motions,
# commissions of inquiry, ...), discriminated by an `@xsi:type` field --
# only this type is an actual bill ("projet de loi" / "proposition de loi").
BILL_TYPE = "DossierLegislatif_Type"


class FranceAssembleeAdapter:
    source_name = "FRANCE"
    source_label = "Assemblée Nationale (data.assemblee-nationale.fr)"

    def __init__(self, base_url: str = "https://data.assemblee-nationale.fr", legislature: str = "17"):
        self.base_url = base_url.rstrip("/")
        self.legislature = legislature

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _download_zip(self) -> bytes:
        url = f"{self.base_url}{ZIP_PATH.format(legislature=self.legislature)}"
        with httpx.stream("GET", url, timeout=300.0, follow_redirects=True) as resp:
            resp.raise_for_status()
            buffer = io.BytesIO()
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                buffer.write(chunk)
            return buffer.getvalue()

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # Published as a full daily snapshot with no incremental filter, like
        # California -- every run re-pulls the whole zip and the diff
        # pipeline detects what's actually new via
        # (jurisdiction, source_bill_id, session).
        zip_bytes = self._download_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = [n for n in zf.namelist() if n.startswith(DOSSIER_PREFIX) and n.endswith(".json")]
            if not names:
                raise RuntimeError(
                    "Assemblée Nationale dossiers zip has no dossierParlementaire "
                    "entries -- the archive layout likely changed."
                )

            for name in names:
                try:
                    payload = json.loads(zf.read(name))
                except (json.JSONDecodeError, KeyError):
                    logger.warning("Skipping unreadable France dossier entry %s", name)
                    continue

                normalized = self._normalize(payload.get("dossierParlementaire") or {})
                if normalized is not None:
                    yield normalized

    def _normalize(self, dossier: dict) -> NormalizedBill | None:
        if dossier.get("@xsi:type") != BILL_TYPE:
            return None
        if str(dossier.get("legislature")) != str(self.legislature):
            return None

        uid = dossier.get("uid")
        titre_info = dossier.get("titreDossier") or {}
        title = titre_info.get("titre")
        if not uid or not title:
            return None

        is_relevant, matched = is_tax_relevant_france(title)
        if not is_relevant:
            return None

        status_events = _extract_status_events(dossier.get("actesLegislatifs"))
        introduced_date = min((event.event_date for event in status_events), default=None)
        last_action_date = max((event.event_date for event in status_events), default=None)
        status_text = (
            max(status_events, key=lambda event: event.event_date).action_text
            if status_events
            else None
        )

        chemin = titre_info.get("titreChemin") or uid
        source_url = f"https://www.assemblee-nationale.fr/dyn/{self.legislature}/dossiers/{chemin}"

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=uid,
            session=str(self.legislature),
            bill_number=uid,
            title=title,
            source_label=self.source_label,
            # No legislative-summary text is exposed by this dataset (unlike
            # e.g. Canada's LEGISinfo) -- the procedure label is the closest
            # available substitute (e.g. "Proposition de loi ordinaire").
            summary=(dossier.get("procedureParlementaire") or {}).get("libelle"),
            sponsors=[],
            status_text=status_text,
            last_action_date=last_action_date,
            introduced_date=introduced_date,
            source_url=source_url,
            tax_keywords_matched=matched,
            raw_source_payload=dossier,
            status_events=status_events,
        )


def _extract_status_events(actes_legislatifs: dict | None) -> list[NormalizedStatusEvent]:
    events: list[NormalizedStatusEvent] = []
    for acte in _walk_actes(actes_legislatifs):
        action_text = (acte.get("libelleActe") or {}).get("libelleCourt")
        event_date = _parse_date(acte.get("dateActe"))
        if not action_text or event_date is None:
            continue
        events.append(
            NormalizedStatusEvent(
                event_date=event_date,
                action_text=action_text,
                action_code=acte.get("codeActe"),
            )
        )
    return events


def _walk_actes(actes_legislatifs: dict | None) -> Iterator[dict]:
    # Each level wraps its child/children (a single dict, or a list) under an
    # "acteLegislatif" key; a node's own nested procedural steps live under
    # its own "actesLegislatifs" key in the same shape -- so this recurses
    # through an arbitrarily deep procedural tree (reading stages, committee
    # steps, votes, promulgation, ...) uniformly at every level.
    if not actes_legislatifs:
        return
    acte_or_list = actes_legislatifs.get("acteLegislatif")
    if acte_or_list is None:
        return
    for acte in acte_or_list if isinstance(acte_or_list, list) else [acte_or_list]:
        yield acte
        yield from _walk_actes(acte.get("actesLegislatifs"))


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        return None

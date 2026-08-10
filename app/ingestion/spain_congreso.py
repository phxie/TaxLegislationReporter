from __future__ import annotations

import datetime as dt
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_spain

logger = logging.getLogger(__name__)

# The public search tool at /es/busqueda-de-iniciativas has a "Datos
# Abiertos" (open data) export feature -- a plain POST to a Liferay portlet
# resource returning XML/CSV of every "iniciativa" (parliamentary
# initiative) matching the current filters, paginated 100 at a time.
# Discovered by reading the search page's own JS (exportOpendata /
# downloadFile) rather than a network-traffic capture, since this page's
# results are server-rendered -- no headless browser was needed. Confirmed
# to work standalone via plain HTTP with no cookies/session required.
EXPORT_PATH = (
    "/es/busqueda-de-iniciativas"
    "?p_p_id=iniciativas&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view"
    "&p_p_resource_id=resourceIDopendataExport&p_p_cacheability=cacheLevelPage"
)
PAGE_SIZE = 100

# The export endpoint has no "type" filter of its own name -- `_iniciativas_tipo`
# takes one of a fixed set of Spanish labels (discovered via the same page's
# "cambiarCompetencia" endpoint, which returns the full list the type-picker
# modal is populated from). Congreso's "iniciativas" cover everything from
# bills to parliamentary questions to no-confidence motions; this adapter is
# scoped to the subset that are actually bills -- government bills
# ("Proyecto de ley"), the four private-member's-bill variants, and
# royal decree-laws (Spain frequently amends tax law by decree-law, which
# takes effect immediately and is later ratified or struck down by Congress).
INITIATIVE_TYPES = (
    "Proyecto de ley",
    "Proposición de ley de Diputados",
    "Proposición de ley de Grupos Parlamentarios del Congreso",
    "Proposición de ley del Senado",
    "Proposición de ley de Comunidades y Ciudades Autónomas",
    "Real Decreto-Ley",
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class SpainCongresoAdapter:
    source_name = "SPAIN"
    source_label = "Congreso de los Diputados (España)"

    def __init__(self, base_url: str = "https://www.congreso.es", legislature: str = "15"):
        self.base_url = base_url.rstrip("/")
        self.legislature = legislature
        self._client = httpx.Client(timeout=30.0, headers={"User-Agent": _USER_AGENT})

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _fetch_type_page(self, tipo: str, file_index: int) -> list[dict]:
        resp = self._client.post(
            f"{self.base_url}{EXPORT_PATH}",
            data={
                "_iniciativas_legislatura": self.legislature,
                "_iniciativas_tipo": tipo,
                "_iniciativas_fileIndex": file_index,
                "_iniciativas_fileType": "xml",
                "_iniciativas_lastResult": file_index * PAGE_SIZE,
            },
        )
        resp.raise_for_status()
        return _parse_iniciativas_xml(resp.text)

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # No incremental "changed since" filter is exposed, and the total
        # across all six types is small (a few hundred items for a current
        # legislature) -- like PwC/California, every run re-pulls the full
        # set and the diff pipeline detects what's actually new via
        # (jurisdiction, source_bill_id, session).
        for tipo in INITIATIVE_TYPES:
            file_index = 1
            while True:
                items = self._fetch_type_page(tipo, file_index)
                if not items:
                    break

                for item in items:
                    normalized = self._normalize(item)
                    if normalized is not None:
                        yield normalized

                if len(items) < PAGE_SIZE:
                    break
                file_index += 1

    def _normalize(self, item: dict) -> NormalizedBill | None:
        title = item.get("titulo")
        source_bill_id = item.get("id_iniciativa")
        if not title or not source_bill_id:
            logger.warning("Skipping Spain initiative missing titulo/id_iniciativa: %s", item)
            return None

        is_relevant, matched = is_tax_relevant_spain(title)
        if not is_relevant:
            return None

        session = item.get("legislatura") or self.legislature
        author = item.get("autor")
        sponsors = [author] if author else []

        presented_date = _parse_date(item.get("fecha_presentado"))
        qualified_date = _parse_date(item.get("fecha_calificado"))
        result = item.get("resultado_tram")

        status_events = []
        if presented_date is not None:
            status_events.append(NormalizedStatusEvent(event_date=presented_date, action_text="Presentado"))
        if qualified_date is not None and qualified_date != presented_date:
            status_events.append(NormalizedStatusEvent(event_date=qualified_date, action_text="Calificado"))

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=source_bill_id,
            session=session,
            bill_number=source_bill_id,
            title=title,
            source_label=self.source_label,
            sponsors=sponsors,
            status_text=result or "En tramitación",
            last_action_date=qualified_date or presented_date,
            introduced_date=presented_date,
            source_url=f"{self.base_url}/es/busqueda-de-iniciativas",
            tax_keywords_matched=matched,
            raw_source_payload=item,
            status_events=status_events,
        )


def _parse_iniciativas_xml(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    return [
        {child.tag: (child.text or "").strip() for child in iniciativa}
        for iniciativa in root.findall("iniciativa")
    ]


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None

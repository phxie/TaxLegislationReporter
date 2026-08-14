from __future__ import annotations

import datetime as dt
import html
import logging
import re
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_mexico

logger = logging.getLogger(__name__)

# The Chamber of Deputies' own "Sistema de Información Legislativa" portal
# (sil.gobernacion.gob.mx) redirects to a domain that no longer resolves, so
# this instead uses "Gaceta Parlamentaria" -- the Chamber's official
# legislative-record site -- whose /gp_iniciativas.html index page links out
# to one HTML page per legislative period (ordinary/permanent-commission/
# extraordinary sessions), each listing every "iniciativa" (bill) presented
# in that period with its title, sponsor, committee referral, and a link to
# its Gaceta issue. No JS/API needed -- these are plain server-rendered
# pages, in the site's original iso-8859-1 encoding (no charset in the HTTP
# Content-Type header, only the page's own <meta> tag, so encoding is set
# explicitly rather than relying on autodetection).
#
# The index page lists every legislature back to 1997 (LVII); this adapter
# only follows the numerically highest legislature's period links, discovered
# dynamically each run rather than hardcoded (like Spain's/Germany's
# `legislature` settings need) since the index page itself exposes it. A
# legislature spans ~9 period pages and, as of the LXVI legislature, about
# 6,800 total iniciativas -- large enough that, like California/France, this
# re-pulls the whole current legislature's history every run rather than
# following an incremental filter (none is exposed), and belongs in the
# "heavy" adapter tier on its own cadence.
INDEX_PATH = "/gp_iniciativas.html"

_PERIOD_LINK_RE = re.compile(r'href="(/Gaceta/Iniciativas/(\d+)/[a-zA-Z0-9_]+\.html)"', re.IGNORECASE)
_BLOCK_RE = re.compile(r"<ul><li>(.*?)</li></ul>", re.S)
_BR_SPLIT_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_TRAILING_ID_RE = re.compile(r"\((\d+)\)\s*$")
_SPANISH_DATE_RE = re.compile(r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})", re.IGNORECASE)

_SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class MexicoDiputadosAdapter:
    source_name = "MEXICO"
    source_label = "Cámara de Diputados (Gaceta Parlamentaria)"

    def __init__(self, base_url: str = "https://gaceta.diputados.gob.mx"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0, headers={"User-Agent": _USER_AGENT})

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _get(self, path: str) -> str:
        resp = self._client.get(f"{self.base_url}{path}")
        resp.raise_for_status()
        # No charset in the HTTP Content-Type header -- only the page's own
        # <meta charset=iso-8859-1> tag, which httpx doesn't inspect.
        resp.encoding = "iso-8859-1"
        return resp.text

    def _discover_current_period_paths(self, index_html: str) -> tuple[str, list[str]]:
        links = _PERIOD_LINK_RE.findall(index_html)
        if not links:
            raise RuntimeError(
                "Could not find any legislative-period links on the Gaceta Parlamentaria "
                "iniciativas index page -- the site's layout likely changed."
            )
        current = max(int(legislature) for _, legislature in links)
        paths = [path for path, legislature in links if int(legislature) == current]
        return str(current), paths

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        index_html = self._get(INDEX_PATH)
        legislature, period_paths = self._discover_current_period_paths(index_html)

        for path in period_paths:
            try:
                page_html = self._get(path)
            except httpx.HTTPError:
                logger.warning("Skipping Mexico period page %s due to a request failure", path)
                continue

            for block in _BLOCK_RE.findall(page_html):
                parsed = _parse_block(block)
                if parsed is None:
                    continue

                is_relevant, matched = is_tax_relevant_mexico(parsed["title"])
                if not is_relevant:
                    continue

                normalized = self._normalize(parsed, legislature, matched)
                if normalized is not None:
                    yield normalized

    def _normalize(self, parsed: dict, legislature: str, matched: list[str]) -> NormalizedBill | None:
        reference_date = parsed["reference_date"]

        status_events: list[NormalizedStatusEvent] = []
        if reference_date is not None:
            status_events.append(NormalizedStatusEvent(event_date=reference_date, action_text="Presentada"))
            if parsed["committee"]:
                status_events.append(
                    NormalizedStatusEvent(event_date=reference_date, action_text=parsed["committee"])
                )

        url = f"{self.base_url}{parsed['relative_url']}"
        raw_payload = {**parsed, "reference_date": reference_date.isoformat() if reference_date else None}

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=parsed["source_bill_id"],
            session=legislature,
            bill_number=parsed["source_bill_id"],
            title=parsed["title"],
            source_label=self.source_label,
            sponsors=[parsed["sponsor"]] if parsed["sponsor"] else [],
            status_text=parsed["committee"] or "Presentada",
            introduced_date=reference_date,
            last_action_date=reference_date,
            full_text_url=url,
            source_url=url,
            tax_keywords_matched=matched,
            raw_source_payload=raw_payload,
            status_events=status_events,
        )


def _parse_block(block_html: str) -> dict | None:
    raw_lines = [ln.strip() for ln in _BR_SPLIT_RE.split(block_html) if ln.strip()]
    if len(raw_lines) < 2:
        return None

    title = _strip_html(raw_lines[0])
    reference_line = raw_lines[-1]
    reference_text = _strip_html(reference_line)

    id_match = _TRAILING_ID_RE.search(reference_text)
    href_match = _HREF_RE.search(reference_line)
    if not title or not id_match or not href_match:
        return None

    detail_lines = [_strip_html(ln) for ln in raw_lines[1:-1]]
    sponsor = next((ln for ln in detail_lines if ln.lower().startswith("presentada")), None)
    committee = next((ln for ln in detail_lines if ln.lower().startswith("turnada")), None)

    return {
        "title": title,
        "source_bill_id": id_match.group(1),
        "relative_url": href_match.group(1),
        "reference_date": _parse_spanish_date(reference_text),
        "sponsor": sponsor,
        "committee": committee,
    }


def _strip_html(value: str) -> str:
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_spanish_date(text: str) -> dt.date | None:
    match = _SPANISH_DATE_RE.search(text)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _SPANISH_MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return dt.date(int(year), month, int(day))
    except ValueError:
        return None

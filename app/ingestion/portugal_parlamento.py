from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
import re
from collections.abc import Iterator

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_portugal

logger = logging.getLogger(__name__)

# The Assembleia da República's own "Dados Abertos" (open data) download
# page is an old SharePoint document library whose actual file links are
# built client-side from an encrypted `Path` parameter -- confirmed
# unworkable even with a headless browser (the page renders blank; its
# sibling archive CSV of direct download URLs, from a community-maintained
# GitHub mirror, is years stale and doesn't even list an "Iniciativas"
# dataset). This adapter instead scrapes the live "Iniciativas Legislativas"
# search page directly: a classic ASP.NET WebForms page (no REST/JSON API)
# whose default view already lists the current legislature's bills, listing
# title/type/number/party/author-party per item with a link to a per-bill
# detail page addressed by a stable numeric "BID".
#
# Both listing and detail pages are plain server-rendered HTML -- no
# JS needed -- but the listing's pagination has no URL/query-string form:
# it's wired to classic ASP.NET postback (`__doPostBack`), requiring the
# page's own `__VIEWSTATE`/`__VIEWSTATEGENERATOR`/`__EVENTVALIDATION` hidden
# fields to be replayed on each subsequent POST, chained page to page. Also
# escapes strings as HTML entities. Confirmed reproducible with plain
# httpx (validated with a throwaway Playwright spike, removed again
# afterwards -- not a runtime dependency, same as the PwC/Singapore
# precedent); the "next page" link is followed by its own on-page target
# rather than a hardcoded page-number pattern, so pagination naturally
# stops once no more pages exist rather than needing a known total.
LISTING_PATH = "/ActividadeParlamentar/Paginas/IniciativasLegislativas.aspx"
DETAIL_PATH = "/ActividadeParlamentar/Paginas/DetalheIniciativa.aspx?BID={bid}"

_ITEM_RE = re.compile(
    r'TextoRegular-Titulo">Tipo</span><br\s*/?>\s*<span class="TextoRegular">\s*([^<]*?)\s*</span>.*?'
    r'TextoRegular-Titulo">N.mero</span><br\s*/?>\s*<span class="TextoRegular">\s*([^<]*?)\s*</span>.*?'
    r'TextoRegular-Titulo">Sess.o</span><br\s*/?>\s*<span class="TextoRegular">\s*([^<]*?)\s*</span>.*?'
    r'TextoRegular-Titulo">Autoria</span><br\s*/?>\s*<span class="TextoRegular">\s*([^<]*?)\s*</span>.*?'
    r'TextoRegular-Titulo">T.tulo</span><br\s*/?>\s*<span class="TextoRegular">\s*'
    r'<a[^>]*href="([^"]*BID=(\d+))"[^>]*>\s*([^<]*?)\s*</a>',
    re.S,
)
_PAGER_LINK_RE = re.compile(r"__doPostBack\('([^']*dpgResults[^']*)','[^']*'\)\">([^<]*)</a>")
_HIDDEN_FIELD_RE = {
    name: re.compile(rf'id="{name}" value="([^"]*)"')
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class PortugalParlamentoAdapter:
    source_name = "PORTUGAL"
    source_label = "Assembleia da República"

    def __init__(self, base_url: str = "https://www.parlamento.pt"):
        self.base_url = base_url.rstrip("/")
        # This site is unusually slow and highly variable -- observed
        # individual postback requests taking anywhere from ~10s to ~90s in
        # a live run, well past the 30s timeout every other adapter in this
        # project uses. Tenacity retry alone wasn't enough headroom for that
        # variance, so the client timeout itself is longer here too.
        self._client = httpx.Client(
            timeout=90.0, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
        )

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
        return html_mod.unescape(resp.text)

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _post_next_page(self, current_html: str, target: str) -> str:
        data = {
            "__EVENTTARGET": target,
            "__EVENTARGUMENT": "",
            **{name: _extract_hidden_field(current_html, name) for name in _HIDDEN_FIELD_RE},
        }
        resp = self._client.post(f"{self.base_url}{LISTING_PATH}", data=data)
        resp.raise_for_status()
        return html_mod.unescape(resp.text)

    def _fetch_listing_pages(self) -> Iterator[list[dict]]:
        html_text = self._get(LISTING_PATH)
        while True:
            yield _parse_listing_items(html_text)
            next_target = _find_next_page_target(html_text)
            if not next_target:
                return
            html_text = self._post_next_page(html_text, next_target)

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # No incremental "changed since" filter is exposed, and the current
        # legislature's full listing is small (~220 bills) -- like
        # Spain/UK/Singapore, every run re-pulls it in full. The (separate)
        # detail page -- where sponsors and the status timeline live -- is
        # only fetched for bills that already pass the relevance check on
        # the listing's own title, the same optimization applied to UK/India.
        is_first_page = True
        for items in self._fetch_listing_pages():
            if is_first_page and not items:
                raise RuntimeError(
                    "Assembleia da República iniciativas listing returned no bills on its "
                    "first page -- the site's markup likely changed."
                )
            is_first_page = False

            for item in items:
                is_relevant, matched = is_tax_relevant_portugal(item["titulo"])
                if not is_relevant:
                    continue

                try:
                    detail_html = self._get(DETAIL_PATH.format(bid=item["bid"]))
                except httpx.HTTPError:
                    logger.warning("Skipping Portugal bill %s due to a request failure", item["bid"])
                    continue

                normalized = self._normalize(item, detail_html, matched)
                if normalized is not None:
                    yield normalized

    def _normalize(self, item: dict, detail_html: str, matched: list[str]) -> NormalizedBill | None:
        bid = item["bid"]
        title = item["titulo"]
        numero = item["numero"]
        if not bid or not title or not numero:
            return None

        detail = _parse_detail(detail_html)
        status_events = detail["status_events"]
        introduced_date = min((e.event_date for e in status_events), default=None)
        last_action_date = max((e.event_date for e in status_events), default=None)
        status_text = status_events[-1].action_text if status_events else None

        session = numero.split("/")[-1].strip() if "/" in numero else "unknown"
        source_url = f"{self.base_url}{DETAIL_PATH.format(bid=bid)}"

        return NormalizedBill(
            jurisdiction=self.source_name,
            source_bill_id=bid,
            session=session,
            bill_number=numero,
            title=title,
            source_label=self.source_label,
            sponsors=detail["sponsors"],
            status_text=status_text,
            introduced_date=introduced_date,
            last_action_date=last_action_date,
            full_text_url=detail["full_text_url"],
            source_url=source_url,
            tax_keywords_matched=matched,
            raw_source_payload={**item, "status_events": [
                {"date": e.event_date.isoformat(), "action": e.action_text} for e in status_events
            ]},
            status_events=status_events,
        )


def _extract_hidden_field(html_text: str, name: str) -> str:
    match = _HIDDEN_FIELD_RE[name].search(html_text)
    return match.group(1) if match else ""


def _find_next_page_target(html_text: str) -> str | None:
    for target, label in _PAGER_LINK_RE.findall(html_text):
        if label.strip() == ">":
            return target
    return None


def _parse_listing_items(html_text: str) -> list[dict]:
    items = []
    for tipo, numero, sessao, autoria, _href, bid, titulo in _ITEM_RE.findall(html_text):
        items.append(
            {
                "tipo": tipo.strip(),
                "numero": numero.strip(),
                "sessao": sessao.strip(),
                "autoria": autoria.strip(),
                "bid": bid.strip(),
                "titulo": titulo.strip(),
            }
        )
    return items


def _parse_detail(html_text: str) -> dict:
    soup = BeautifulSoup(html_text, "html.parser")

    sponsors = [
        el.get_text(strip=True)
        for el in soup.find_all(id=re.compile(r"_rptAutores_ctl\d+_hplAutor$"))
        if el.get_text(strip=True)
    ]

    full_text_url = None
    doc_link = soup.find(id=re.compile(r"_ucLinkDocumento_hplDocumentoPDF$"))
    if doc_link is not None and doc_link.get("href"):
        full_text_url = doc_link["href"]

    status_events: list[NormalizedStatusEvent] = []
    event_els = {
        m.group(1): el.get_text(strip=True)
        for el in soup.find_all(id=re.compile(r"_rptFases_ctl(\d+)_lblEvento$"))
        if (m := re.search(r"_rptFases_ctl(\d+)_lblEvento$", el["id"]))
    }
    date_els = {
        m.group(1): el.get_text(strip=True)
        for el in soup.find_all(id=re.compile(r"_rptFases_ctl(\d+)_lblData$"))
        if (m := re.search(r"_rptFases_ctl(\d+)_lblData$", el["id"]))
    }
    for idx, action_text in event_els.items():
        event_date = _parse_date(date_els.get(idx))
        if event_date is None or not action_text:
            continue
        status_events.append(NormalizedStatusEvent(event_date=event_date, action_text=action_text))
    status_events.sort(key=lambda e: e.event_date)

    return {"sponsors": sponsors, "full_text_url": full_text_url, "status_events": status_events}


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None

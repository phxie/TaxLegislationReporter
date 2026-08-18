import datetime as dt

import httpx
import pytest

from app.ingestion.portugal_parlamento import (
    PortugalParlamentoAdapter,
    _find_next_page_target,
    _parse_detail,
    _parse_listing_items,
)


def _listing_item_html(tipo, numero, sessao, autoria, bid, titulo):
    # Mirrors the shape _get() hands to the parser -- i.e. *after*
    # html.unescape(), so plain characters here (not HTML entities).
    return f"""
    <div>
        <span class="TextoRegular-Titulo">Tipo</span><br />
        <span class="TextoRegular">{tipo}</span>
    </div>
    <div>
        <span class="TextoRegular-Titulo">Número</span><br />
        <span class="TextoRegular">{numero}</span>
    </div>
    <div>
        <span class="TextoRegular-Titulo">Sessão</span><br />
        <span class="TextoRegular">{sessao}</span>
    </div>
    <div>
        <span class="TextoRegular-Titulo">Autoria</span><br />
        <span class="TextoRegular">{autoria}</span>
    </div>
    <div>
        <span class="TextoRegular-Titulo">Título</span><br />
        <span class="TextoRegular">
            <a title="Detalhe da iniciativa"
               href="/ActividadeParlamentar/Paginas/DetalheIniciativa.aspx?BID={bid}">{titulo}</a></span>
    </div>
    """


def _listing_page_html(items, next_target=None):
    body = "\n".join(
        _listing_item_html(it["tipo"], it["numero"], it["sessao"], it["autoria"], it["bid"], it["titulo"])
        for it in items
    )
    pager = ""
    if next_target:
        pager = f"""
        <span id="...dpgResults"><span>1</span>&nbsp;
        <a href="javascript:__doPostBack('{next_target}','')">></a>&nbsp;</span>
        """
    hidden = (
        '<input type="hidden" id="__VIEWSTATE" value="VS1" />'
        '<input type="hidden" id="__VIEWSTATEGENERATOR" value="GEN1" />'
        '<input type="hidden" id="__EVENTVALIDATION" value="EV1" />'
    )
    return f"<html><body>{hidden}{body}{pager}</body></html>"


def _detail_html(events, sponsors, pdf_url=None):
    events_html = "".join(
        f"""
        <span id="ctl00_..._rptFases_ctl{idx:02d}_lblEvento">{action}</span>
        <span id="ctl00_..._rptFases_ctl{idx:02d}_lblData">{date}</span>
        """
        for idx, (date, action) in enumerate(events)
    )
    sponsors_html = "".join(
        f'<a id="ctl00_..._rptAutores_ctl{idx:02d}_hplAutor" '
        f'href="/DeputadoGP/Paginas/Biografia.aspx?BID=1">{name}</a>'
        for idx, name in enumerate(sponsors)
    )
    doc_html = ""
    if pdf_url:
        doc_html = f'<a id="ctl00_..._ucLinkDocumento_hplDocumentoPDF" href="{pdf_url}">[formato PDF]</a>'
    return f"<html><body>{events_html}{sponsors_html}{doc_html}</body></html>"


TAX_ITEM = {
    "tipo": "Projeto de Lei",
    "numero": "82/XVII",
    "sessao": "1",
    "autoria": "CH",
    "bid": "315249",
    "titulo": "Torna permanente a aplicação da taxa de IVA reduzida a equipamentos energéticos",
}
NON_TAX_ITEM = {
    "tipo": "Decreto-Lei",
    "numero": "17/XVII",
    "sessao": "1",
    "autoria": "PS",
    "bid": "377105",
    "titulo": "Procede à criação da Universidade Técnica do Porto",
}


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = PortugalParlamentoAdapter()
    adapter._get = lambda path: (
        _listing_page_html([TAX_ITEM, NON_TAX_ITEM])
        if "IniciativasLegislativas" in path
        else _detail_html(
            events=[("2025-07-02", "Entrada"), ("2025-07-09", "Baixa comissão distribuição")],
            sponsors=["Pedro Pinto (CH)"],
            pdf_url="https://app.parlamento.pt/webutils/docs/doc.pdf?path=abc",
        )
    )

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "PORTUGAL"
    assert bill.source_bill_id == "315249"
    assert bill.bill_number == "82/XVII"
    assert bill.session == "XVII"
    assert bill.source_label == "Assembleia da República"
    assert bill.sponsors == ["Pedro Pinto (CH)"]
    assert "iva" in bill.tax_keywords_matched
    assert bill.introduced_date == dt.date(2025, 7, 2)
    assert bill.last_action_date == dt.date(2025, 7, 9)
    assert bill.status_text == "Baixa comissão distribuição"
    assert bill.full_text_url == "https://app.parlamento.pt/webutils/docs/doc.pdf?path=abc"
    assert bill.source_url == (
        "https://www.parlamento.pt/ActividadeParlamentar/Paginas/DetalheIniciativa.aspx?BID=315249"
    )
    assert [e.action_text for e in bill.status_events] == ["Entrada", "Baixa comissão distribuição"]


def test_detail_not_fetched_for_irrelevant_bills():
    # The detail page needs a separate request per bill, so it should only
    # be fetched for bills that already passed the relevance check on the
    # listing's own title -- same optimization as the UK/India adapters.
    adapter = PortugalParlamentoAdapter()
    detail_calls = []

    def _get(path):
        if "IniciativasLegislativas" in path:
            return _listing_page_html([TAX_ITEM, NON_TAX_ITEM])
        detail_calls.append(path)
        return _detail_html(events=[], sponsors=[])

    adapter._get = _get

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    assert len(detail_calls) == 1
    assert "BID=315249" in detail_calls[0]


def test_fetch_updates_paginates_until_no_next_target():
    adapter = PortugalParlamentoAdapter()
    next_target = "ctl00$ctl51$g_x$ctl00$dpgResults$ctl00$ctl01"
    page1 = _listing_page_html([TAX_ITEM], next_target=next_target)
    item2 = {**TAX_ITEM, "bid": "999999", "numero": "1/XVII"}
    page2 = _listing_page_html([item2])

    calls = []

    def _get(path):
        assert "IniciativasLegislativas" in path
        return page1

    def _post_next_page(current_html, target):
        calls.append(target)
        return page2

    adapter._get = lambda path: (
        page1
        if "IniciativasLegislativas" in path
        else _detail_html(events=[("2026-01-01", "Entrada")], sponsors=[])
    )
    adapter._post_next_page = _post_next_page

    results = list(adapter.fetch_updates(since=None))

    assert calls == [next_target]
    assert {b.source_bill_id for b in results} == {"315249", "999999"}


def test_fetch_updates_raises_when_no_pages_returned():
    adapter = PortugalParlamentoAdapter()
    adapter._get = lambda path: "<html><body>no listing markup here</body></html>"

    with pytest.raises(RuntimeError):
        list(adapter.fetch_updates(since=None))


def test_fetch_updates_skips_bill_on_detail_request_failure():
    adapter = PortugalParlamentoAdapter()

    def _get(path):
        if "IniciativasLegislativas" in path:
            return _listing_page_html([TAX_ITEM])
        raise httpx.HTTPError("boom")

    adapter._get = _get

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_parse_listing_items_extracts_all_fields():
    html_text = _listing_page_html([TAX_ITEM])

    items = _parse_listing_items(html_text)

    assert len(items) == 1
    assert items[0] == TAX_ITEM


def test_find_next_page_target_returns_none_when_absent():
    assert _find_next_page_target(_listing_page_html([TAX_ITEM])) is None


def test_find_next_page_target_returns_target_when_present():
    target = "ctl00$ctl51$g_x$ctl00$dpgResults$ctl00$ctl10"
    html_text = _listing_page_html([TAX_ITEM], next_target=target)
    assert _find_next_page_target(html_text) == target


def test_parse_detail_pairs_events_by_index_and_sorts_by_date():
    html_text = _detail_html(
        events=[("2026-02-01", "Segundo evento"), ("2026-01-01", "Primeiro evento")],
        sponsors=["Autor Um (PS)", "Autor Dois (PS)"],
        pdf_url="https://app.parlamento.pt/webutils/docs/doc.pdf?path=xyz",
    )

    detail = _parse_detail(html_text)

    assert [e.action_text for e in detail["status_events"]] == ["Primeiro evento", "Segundo evento"]
    assert detail["status_events"][0].event_date == dt.date(2026, 1, 1)
    assert detail["sponsors"] == ["Autor Um (PS)", "Autor Dois (PS)"]
    assert detail["full_text_url"] == "https://app.parlamento.pt/webutils/docs/doc.pdf?path=xyz"


def test_parse_detail_handles_missing_document_link():
    html_text = _detail_html(events=[], sponsors=[])

    detail = _parse_detail(html_text)

    assert detail["full_text_url"] is None
    assert detail["status_events"] == []
    assert detail["sponsors"] == []

import datetime as dt

import httpx
import pytest

from app.ingestion.mexico_diputados import (
    MexicoDiputadosAdapter,
    _parse_block,
    _parse_spanish_date,
)

INDEX_HTML = """
<a HREF="/Gaceta/Iniciativas/66/gp66_a2segundo.html">Segundo periodo ordinario</a>
<a HREF="/Gaceta/Iniciativas/66/gp66_a2perma1.html">1er. periodo Com. Permanente</a>
<a HREF="/Gaceta/Iniciativas/65/gp65_a3segundo.html">Segundo periodo ordinario</a>
"""

TAX_BLOCK = """<ul><li>
Que reforma el artículo 2o.-A de la Ley del Impuesto al Valor Agregado, para reducir el IVA.
<br>Presentada por la diputada Ejemplo López, PAN.
<br>Turnada a la Comisión de Hacienda y Crédito Público.
<br><a href="/Gaceta/66/2026/feb/20260202-II-2.html#Iniciativa1">Gaceta Parlamentaria</a>,
número 6966-II-2, lunes 2 de febrero de 2026. (4079)
</li></ul>"""

NON_TAX_BLOCK = """<ul><li>
Que reforma el artículo 28 de la Constitución Política de los Estados Unidos Mexicanos,
en materia de transportes.
<br>Presentada por la diputada Paulina Rubio Fernández, PAN.
<br>Turnada a la Comisión de Puntos Constitucionales.
<br><a href="/Gaceta/66/2026/feb/20260201-II-2.html#Iniciativa13">Gaceta Parlamentaria</a>,
número 6965-II-2, domingo 1 de febrero de 2026. (4074)
</li></ul>"""

# Real title from the live source: contains "fiscalización" (government
# auditing) and "ejercicio fiscal" (fiscal year) but no actual tax-law
# content -- a regression check that whole-word "fiscal" matching still
# behaves given a title where "fiscal" only ever appears as a substring of
# a longer word ("fiscalización"), never standing alone.
FISCALIZACION_ONLY_BLOCK = """<ul><li>
Que reforma los artículos 6o. y 79 de la Constitución, para permitir la fiscalización de
obras públicas cuando existan indicios de corrupción.
<br>Presentada por el diputado Ejemplo Dos, MORENA.
<br>Turnada a la Comisión de Transparencia.
<br><a href="/Gaceta/66/2026/feb/20260203-II-2.html#Iniciativa2">Gaceta Parlamentaria</a>,
número 6967-II-2, martes 3 de febrero de 2026. (4080)
</li></ul>"""


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = MexicoDiputadosAdapter()
    pages = {
        "/gp_iniciativas.html": INDEX_HTML,
        "/Gaceta/Iniciativas/66/gp66_a2segundo.html": TAX_BLOCK + NON_TAX_BLOCK,
        "/Gaceta/Iniciativas/66/gp66_a2perma1.html": "",
    }
    adapter._get = lambda path: pages[path]

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "MEXICO"
    assert bill.source_bill_id == "4079"
    assert bill.bill_number == "4079"
    assert bill.session == "66"
    assert bill.source_label == "Cámara de Diputados (Gaceta Parlamentaria)"
    assert bill.sponsors == ["Presentada por la diputada Ejemplo López, PAN."]
    assert "iva" in bill.tax_keywords_matched
    assert bill.introduced_date == dt.date(2026, 2, 2)
    assert bill.last_action_date == dt.date(2026, 2, 2)
    assert bill.status_text == "Turnada a la Comisión de Hacienda y Crédito Público."
    assert bill.full_text_url == (
        "https://gaceta.diputados.gob.mx/Gaceta/66/2026/feb/20260202-II-2.html#Iniciativa1"
    )
    assert [e.action_text for e in bill.status_events] == [
        "Presentada",
        "Turnada a la Comisión de Hacienda y Crédito Público.",
    ]


def test_fiscalizacion_only_bill_is_not_a_false_positive():
    adapter = MexicoDiputadosAdapter()
    pages = {
        "/gp_iniciativas.html": INDEX_HTML,
        "/Gaceta/Iniciativas/66/gp66_a2segundo.html": FISCALIZACION_ONLY_BLOCK,
        "/Gaceta/Iniciativas/66/gp66_a2perma1.html": "",
    }
    adapter._get = lambda path: pages[path]

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_only_current_highest_legislature_period_pages_are_fetched():
    adapter = MexicoDiputadosAdapter()
    requested = []

    def _get(path):
        requested.append(path)
        if path == "/gp_iniciativas.html":
            return INDEX_HTML
        return ""

    adapter._get = _get

    list(adapter.fetch_updates(since=None))

    assert requested == [
        "/gp_iniciativas.html",
        "/Gaceta/Iniciativas/66/gp66_a2segundo.html",
        "/Gaceta/Iniciativas/66/gp66_a2perma1.html",
    ]


def test_fetch_updates_skips_period_page_on_request_failure():
    adapter = MexicoDiputadosAdapter()

    def _get(path):
        if path == "/gp_iniciativas.html":
            return INDEX_HTML
        if path == "/Gaceta/Iniciativas/66/gp66_a2segundo.html":
            raise httpx.HTTPError("boom")
        return TAX_BLOCK

    adapter._get = _get

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    assert results[0].source_bill_id == "4079"


def test_discover_current_period_paths_raises_when_no_links_found():
    adapter = MexicoDiputadosAdapter()

    with pytest.raises(RuntimeError):
        adapter._discover_current_period_paths("<html>no links here</html>")


def test_parse_block_extracts_all_fields():
    parsed = _parse_block(TAX_BLOCK[len("<ul><li>") : -len("</li></ul>")])

    assert parsed["title"] == (
        "Que reforma el artículo 2o.-A de la Ley del Impuesto al Valor Agregado, para reducir el IVA."
    )
    assert parsed["source_bill_id"] == "4079"
    assert parsed["relative_url"] == "/Gaceta/66/2026/feb/20260202-II-2.html#Iniciativa1"
    assert parsed["reference_date"] == dt.date(2026, 2, 2)
    assert parsed["sponsor"] == "Presentada por la diputada Ejemplo López, PAN."
    assert parsed["committee"] == "Turnada a la Comisión de Hacienda y Crédito Público."


def test_parse_block_returns_none_when_missing_trailing_id_or_href():
    malformed = "Some title with no <br>reference line at all"
    assert _parse_block(malformed) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("lunes 2 de febrero de 2026", dt.date(2026, 2, 2)),
        ("domingo 1 de febrero de 2026", dt.date(2026, 2, 1)),
        ("no date here", None),
        ("2 de mesinventado de 2026", None),
    ],
)
def test_parse_spanish_date(text, expected):
    assert _parse_spanish_date(text) == expected

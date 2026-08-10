import datetime as dt

from app.ingestion.spain_congreso import (
    INITIATIVE_TYPES,
    SpainCongresoAdapter,
    _parse_iniciativas_xml,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<iniciativas>
    <iniciativa>
        <titulo>Proyecto de Ley por la que se modifica la Ley del Impuesto sobre el Valor Añadido.</titulo>
        <legislatura>XV</legislatura>
        <fecha_presentado>17/07/2026</fecha_presentado>
        <fecha_calificado>23/07/2026</fecha_calificado>
        <autor>Gobierno</autor>
        <resultado_tram></resultado_tram>
        <id_iniciativa>121/000105</id_iniciativa>
    </iniciativa>
</iniciativas>
"""


def _item(
    titulo="Proyecto de Ley del Impuesto sobre Sociedades",
    id_iniciativa="121/000001",
    fecha_presentado="01/01/2026",
    fecha_calificado="05/01/2026",
    resultado_tram="",
    autor="Gobierno",
):
    return {
        "titulo": titulo,
        "legislatura": "XV",
        "fecha_presentado": fecha_presentado,
        "fecha_calificado": fecha_calificado,
        "autor": autor,
        "resultado_tram": resultado_tram,
        "id_iniciativa": id_iniciativa,
    }


def test_parse_iniciativas_xml():
    items = _parse_iniciativas_xml(SAMPLE_XML)
    assert len(items) == 1
    assert items[0]["id_iniciativa"] == "121/000105"
    assert items[0]["titulo"].startswith("Proyecto de Ley")


def test_fetch_updates_filters_tax_relevant_and_paginates():
    adapter = SpainCongresoAdapter()

    pages = {
        "Proyecto de ley": {
            1: [
                _item(titulo="Proyecto de Ley del Impuesto sobre Sociedades", id_iniciativa="121/000001"),
                _item(
                    titulo="Proyecto de Ley Orgánica de medidas en materia de violencia vicaria",
                    id_iniciativa="121/000002",
                ),
            ],
        },
    }
    adapter._fetch_type_page = lambda tipo, file_index: pages.get(tipo, {}).get(file_index, [])

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "SPAIN"
    assert bill.source_bill_id == "121/000001"
    assert bill.session == "XV"
    assert bill.source_label == "Congreso de los Diputados (España)"
    assert bill.sponsors == ["Gobierno"]
    assert "impuesto" in bill.tax_keywords_matched
    assert bill.introduced_date == dt.date(2026, 1, 1)
    assert bill.last_action_date == dt.date(2026, 1, 5)
    assert bill.status_text == "En tramitación"
    assert [e.action_text for e in bill.status_events] == ["Presentado", "Calificado"]


def test_fetch_updates_paginates_within_a_type():
    adapter = SpainCongresoAdapter()

    page_1 = [_item(id_iniciativa=f"121/{i:06d}") for i in range(100)]
    page_2 = [_item(id_iniciativa="121/000200")]
    calls = {"Proyecto de ley": {1: page_1, 2: page_2}}
    # Every other type has nothing.
    adapter._fetch_type_page = lambda tipo, file_index: calls.get(tipo, {}).get(file_index, [])

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 101
    assert results[-1].source_bill_id == "121/000200"


def test_status_text_uses_resultado_tram_when_present():
    adapter = SpainCongresoAdapter()
    def _fetch_type_page(tipo, file_index):
        if tipo == "Proyecto de ley" and file_index == 1:
            return [_item(resultado_tram="Aprobado sin modificaciones")]
        return []

    adapter._fetch_type_page = _fetch_type_page

    results = list(adapter.fetch_updates(since=None))

    assert results[0].status_text == "Aprobado sin modificaciones"


def test_initiative_types_include_bills_and_decree_laws():
    assert "Proyecto de ley" in INITIATIVE_TYPES
    assert "Real Decreto-Ley" in INITIATIVE_TYPES

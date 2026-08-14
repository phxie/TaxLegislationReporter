import datetime as dt

from app.ingestion.germany_bundestag import GermanyBundestagAdapter, _extract_status_events, _slugify


def _vorgang(
    id="337628",
    titel="Gesetz zur Nichterhebung einer Erbschaft- und Schenkungsteuer",
    abstract=None,
    beratungsstand="Verkündet",
    initiative=None,
    datum="2026-07-22",
):
    return {
        "id": id,
        "titel": titel,
        "abstract": abstract,
        "beratungsstand": beratungsstand,
        "initiative": initiative if initiative is not None else ["Bundesregierung"],
        "datum": datum,
        "aktualisiert": "2026-07-29T15:11:49+02:00",
    }


def _position(
    datum="2026-07-22", vorgangsposition="Gesetzentwurf", zuordnung="BT", dokumentnummer=None, pdf_url=None
):
    fundstelle = None
    if dokumentnummer or pdf_url:
        fundstelle = {"dokumentnummer": dokumentnummer, "pdf_url": pdf_url}
    return {
        "datum": datum,
        "vorgangsposition": vorgangsposition,
        "zuordnung": zuordnung,
        "fundstelle": fundstelle,
    }


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = GermanyBundestagAdapter()
    pages = {
        None: {
            "documents": [
                _vorgang(id="1", titel="Gesetz zur Änderung des Einkommensteuergesetzes"),
                _vorgang(id="2", titel="Gesetz über die Feststellung des Bundeshaushaltsplans"),
            ],
            "cursor": "c1",
        },
        "c1": {"documents": [], "cursor": "c1"},
    }
    adapter._fetch_vorgang_page = lambda cursor, since: pages[cursor]
    adapter._fetch_all_vorgangspositionen = lambda vorgang_id: (
        [
            _position(
                datum="2026-05-19",
                vorgangsposition="Gesetzentwurf",
                dokumentnummer="21/7251",
                pdf_url="https://dserver.bundestag.de/btd/21/072/2107251.pdf",
            ),
            _position(datum="2026-06-11", vorgangsposition="2. Beratung"),
        ]
        if vorgang_id == "1"
        else []
    )

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "GERMANY"
    assert bill.source_bill_id == "1"
    assert bill.session == "21"
    assert bill.bill_number == "21/7251"
    assert bill.source_label == "Deutscher Bundestag (DIP)"
    assert bill.sponsors == ["Bundesregierung"]
    assert "steuer" in bill.tax_keywords_matched
    assert bill.status_text == "Verkündet"
    assert bill.introduced_date == dt.date(2026, 5, 19)
    assert bill.last_action_date == dt.date(2026, 6, 11)
    assert bill.full_text_url == "https://dserver.bundestag.de/btd/21/072/2107251.pdf"
    assert bill.source_url.startswith("https://dip.bundestag.de/vorgang/")
    assert bill.source_url.endswith("/1")
    assert [e.action_text for e in bill.status_events] == ["Gesetzentwurf", "2. Beratung"]


def test_vorgangsposition_not_fetched_for_irrelevant_vorgaenge():
    # The status-timeline endpoint needs a separate request per Vorgang, so
    # it should only be called for Vorgaenge that already passed the
    # relevance check on the list response's title/abstract -- same
    # optimization as the UK adapter's Stages fetch.
    adapter = GermanyBundestagAdapter()
    pages = {
        None: {
            "documents": [
                _vorgang(id="1", titel="Gesetz zur Änderung des Einkommensteuergesetzes"),
                _vorgang(id="2", titel="Gesetz über die Feststellung des Bundeshaushaltsplans"),
            ],
            "cursor": "c1",
        },
        "c1": {"documents": [], "cursor": "c1"},
    }
    adapter._fetch_vorgang_page = lambda cursor, since: pages[cursor]
    position_calls = []

    def _fetch_all_vorgangspositionen(vorgang_id):
        position_calls.append(vorgang_id)
        return [_position()]

    adapter._fetch_all_vorgangspositionen = _fetch_all_vorgangspositionen

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    assert position_calls == ["1"]


def test_fetch_updates_paginates_vorgang_list():
    adapter = GermanyBundestagAdapter()
    pages = {
        None: {"documents": [_vorgang(id="1", titel="Steueränderungsgesetz 2025")], "cursor": "c1"},
        "c1": {"documents": [_vorgang(id="2", titel="Steueränderungsgesetz 2026")], "cursor": "c2"},
        "c2": {"documents": [], "cursor": "c2"},
    }
    adapter._fetch_vorgang_page = lambda cursor, since: pages[cursor]
    adapter._fetch_all_vorgangspositionen = lambda vorgang_id: [_position()]

    results = list(adapter.fetch_updates(since=None))

    assert {b.source_bill_id for b in results} == {"1", "2"}


def test_fetch_vorgang_page_passes_incremental_since_filter():
    adapter = GermanyBundestagAdapter(api_key="test-key", wahlperiode="21")
    captured = {}

    def _get(path, **params):
        captured["path"] = path
        captured["params"] = params
        return {"documents": [], "cursor": None}

    adapter._get = _get
    since = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)

    adapter._fetch_vorgang_page(cursor=None, since=since)

    assert captured["path"] == "/vorgang"
    assert captured["params"]["f.vorgangstyp"] == "Gesetzgebung"
    assert captured["params"]["f.wahlperiode"] == "21"
    assert captured["params"]["f.aktualisiert.start"] == since.isoformat()
    assert "cursor" not in captured["params"]


def test_bill_number_falls_back_to_vorgang_id_when_no_dokumentnummer():
    adapter = GermanyBundestagAdapter()
    adapter._fetch_vorgang_page = lambda cursor, since: {
        "documents": [_vorgang(id="42", titel="Steueränderungsgesetz 2025", datum=None)],
        "cursor": None,
    }
    adapter._fetch_all_vorgangspositionen = lambda vorgang_id: []

    results = list(adapter.fetch_updates(since=None))

    assert results[0].bill_number == "Vorgang 42"
    assert results[0].introduced_date is None
    assert results[0].status_events == []


def test_extract_status_events_sorts_by_date():
    positions = [
        _position(datum="2026-06-11", vorgangsposition="2. Beratung"),
        _position(datum="2026-05-19", vorgangsposition="Gesetzentwurf"),
    ]

    events = _extract_status_events(positions)

    assert [e.action_text for e in events] == ["Gesetzentwurf", "2. Beratung"]


def test_slugify_lowercases_and_hyphenates():
    assert _slugify("Steueränderungsgesetz 2025") == "steuer%C3%A4nderungsgesetz-2025"

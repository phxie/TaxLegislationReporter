import datetime as dt

from app.ingestion.uk_parliament import UkParliamentAdapter


def _detail(
    bill_id=1,
    short_title="Finance Bill",
    long_title="A Bill to make provision about taxation.",
    summary=None,
    session_id=40,
    sponsor_name="Jane Example MP",
):
    return {
        "billId": bill_id,
        "shortTitle": short_title,
        "longTitle": long_title,
        "summary": summary,
        "introducedSessionId": session_id,
        "sponsors": [{"member": {"name": sponsor_name}}],
        "currentStage": {"description": "Report stage", "abbreviation": "RS"},
        "lastUpdate": "2026-08-01T10:00:00",
    }


def _stages():
    return [
        {
            "description": "1st reading",
            "abbreviation": "1R",
            "house": "Commons",
            "sortOrder": 1,
            "stageSittings": [{"date": "2026-06-01T00:00:00"}],
        },
        {
            "description": "2nd reading",
            "abbreviation": "2R",
            "house": "Commons",
            "sortOrder": 2,
            "stageSittings": [{"date": "2026-06-15T00:00:00"}],
        },
    ]


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = UkParliamentAdapter()
    adapter._fetch_current_session_id = lambda: 40
    adapter._fetch_bills_page = lambda session_id, skip: (
        [{"billId": 1}, {"billId": 2}] if skip == 0 else []
    )
    adapter._fetch_bill_detail = lambda bill_id: {
        1: _detail(bill_id=1, short_title="Finance Bill"),
        2: _detail(bill_id=2, short_title="Sporting Events Bill", long_title="A Bill about sport."),
    }[bill_id]
    adapter._fetch_bill_stages = lambda bill_id: _stages()

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "UK"
    assert bill.source_bill_id == "1"
    assert bill.bill_number == "1"
    assert bill.session == "40"
    assert bill.source_label == "UK Parliament Bills"
    assert bill.sponsors == ["Jane Example MP"]
    assert bill.tax_keywords_matched == ["finance_bill"]
    assert bill.status_text == "Report stage"
    assert bill.status_code == "RS"
    assert bill.introduced_date == dt.date(2026, 6, 1)
    assert bill.last_action_date == dt.date(2026, 6, 15)
    assert bill.source_url == "https://bills.parliament.uk/bills/1"
    assert [e.action_text for e in bill.status_events] == [
        "1st reading (Commons)",
        "2nd reading (Commons)",
    ]


def test_fetch_updates_paginates_bill_list():
    adapter = UkParliamentAdapter()
    adapter._fetch_current_session_id = lambda: 40

    page_1 = [{"billId": i} for i in range(50)]
    page_2 = [{"billId": 50}]
    pages = {0: page_1, 50: page_2}
    adapter._fetch_bills_page = lambda session_id, skip: pages.get(skip, [])
    adapter._fetch_bill_detail = lambda bill_id: _detail(bill_id=bill_id)
    adapter._fetch_bill_stages = lambda bill_id: _stages()

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 51
    assert {b.source_bill_id for b in results} == {str(i) for i in range(51)}


def test_stages_not_fetched_for_irrelevant_bills():
    # The Stages endpoint is slow on the real API, so it should only be
    # called for bills that already passed the relevance check on `detail`.
    adapter = UkParliamentAdapter()
    adapter._fetch_current_session_id = lambda: 40
    adapter._fetch_bills_page = lambda session_id, skip: (
        [{"billId": 1}, {"billId": 2}] if skip == 0 else []
    )
    adapter._fetch_bill_detail = lambda bill_id: {
        1: _detail(bill_id=1, short_title="Finance Bill"),
        2: _detail(bill_id=2, short_title="Sporting Events Bill", long_title="A Bill about sport."),
    }[bill_id]

    stages_calls = []

    def _fetch_bill_stages(bill_id):
        stages_calls.append(bill_id)
        return _stages()

    adapter._fetch_bill_stages = _fetch_bill_stages

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    assert stages_calls == [1]


def test_summary_falls_back_to_long_title_when_summary_missing():
    adapter = UkParliamentAdapter()
    adapter._fetch_current_session_id = lambda: 40
    adapter._fetch_bills_page = lambda session_id, skip: [{"billId": 1}] if skip == 0 else []
    adapter._fetch_bill_detail = lambda bill_id: _detail(
        summary=None, long_title="A Bill to make provision about taxation."
    )
    adapter._fetch_bill_stages = lambda bill_id: []

    results = list(adapter.fetch_updates(since=None))

    assert results[0].summary == "A Bill to make provision about taxation."

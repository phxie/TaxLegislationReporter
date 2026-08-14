import datetime as dt

import pytest

from app.ingestion.singapore_parliament import (
    PAGE_SIZE,
    SingaporeParliamentAdapter,
    _extract_status_events,
    _parse_date,
)


def _bill(
    id="a328a64f-cd48-4b50-b61f-31692c1c535f",
    title="18/2026",
    description="Income Tax (Amendment) Bill",
    date_introduced="2026-08-04T12:00:00",
    date_of_second_reading=None,
    date_passed=None,
    file=None,
):
    return {
        "id": id,
        "title": title,
        "description": description,
        "date_introduced": date_introduced,
        "date_of_second_reading": date_of_second_reading,
        "date_passed": date_passed,
        "file": file
        if file is not None
        else {
            "id": "4885e7e0-f4c9-44d1-8280-d2d8f317f343",
            "filename_download": "Income Tax Bill 18-2026.pdf",
        },
    }


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = SingaporeParliamentAdapter()
    adapter._discover_action_id = lambda: "action123"
    adapter._fetch_page = lambda action_id, offset, limit: {
        "meta": {"filter_count": 2},
        "data": [
            _bill(id="1", title="18/2026", description="Income Tax (Amendment) Bill"),
            _bill(id="2", title="19/2026", description="Third-Party Taxi Booking Service Providers Bill"),
        ],
    }

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "SINGAPORE"
    assert bill.source_bill_id == "1"
    assert bill.bill_number == "18/2026"
    assert bill.session == "2026"
    assert bill.source_label == "Parliament of Singapore"
    assert bill.sponsors == []
    assert "tax" in bill.tax_keywords_matched
    assert bill.introduced_date == dt.date(2026, 8, 4)
    assert bill.status_text == "Introduced (1st Reading)"
    assert bill.full_text_url == (
        "https://www.parliament.gov.sg/api/media/4885e7e0-f4c9-44d1-8280-d2d8f317f343/"
        "Income%20Tax%20Bill%2018-2026.pdf"
    )
    assert bill.source_url == "https://www.parliament.gov.sg/parliamentary-business/bills-introduced"


def test_taxi_bill_is_not_a_false_positive():
    # "Taxi" contains "tax" as a substring -- confirms word-boundary
    # matching (not the shared substring-based matching_keywords()) is
    # actually wired up end to end, not just in the tax_filter unit tests.
    adapter = SingaporeParliamentAdapter()
    adapter._discover_action_id = lambda: "action123"
    adapter._fetch_page = lambda action_id, offset, limit: {
        "meta": {"filter_count": 1},
        "data": [_bill(description="Third-Party Taxi Booking Service Providers Bill")],
    }

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_fetch_updates_paginates_until_filter_count_reached():
    adapter = SingaporeParliamentAdapter()
    adapter._discover_action_id = lambda: "action123"
    # Real page size is fixed at PAGE_SIZE regardless of how many items a
    # page actually returns, so the offset only advances in PAGE_SIZE steps.
    pages = {
        0: {
            "meta": {"filter_count": PAGE_SIZE + 1},
            "data": [_bill(id="1"), _bill(id="2")],
        },
        PAGE_SIZE: {
            "meta": {"filter_count": PAGE_SIZE + 1},
            "data": [_bill(id="3")],
        },
    }
    calls = []

    def _fetch_page(action_id, offset, limit):
        calls.append(offset)
        return pages[offset]

    adapter._fetch_page = _fetch_page

    results = list(adapter.fetch_updates(since=None))

    assert calls == [0, PAGE_SIZE]
    assert {b.source_bill_id for b in results} == {"1", "2", "3"}


def test_fetch_updates_stops_on_empty_page():
    adapter = SingaporeParliamentAdapter()
    adapter._discover_action_id = lambda: "action123"
    adapter._fetch_page = lambda action_id, offset, limit: {"meta": {"filter_count": 0}, "data": []}

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_extract_status_events_skips_unparseable_second_reading_placeholder():
    item = _bill(
        date_introduced="2026-08-04T12:00:00",
        date_of_second_reading="Next Available Sitting",
        date_passed=None,
    )

    events = _extract_status_events(item)

    assert [e.action_text for e in events] == ["Introduced (1st Reading)"]


def test_extract_status_events_covers_full_lifecycle():
    item = _bill(
        date_introduced="2026-04-07T00:00:00",
        date_of_second_reading="07.05.2026",
        date_passed="07.05.2026",
    )

    events = _extract_status_events(item)

    assert [e.action_text for e in events] == ["Introduced (1st Reading)", "2nd Reading", "Passed"]
    assert events[1].event_date == dt.date(2026, 5, 7)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-08-04T12:00:00", dt.date(2026, 8, 4)),
        ("07.05.2026", dt.date(2026, 5, 7)),
        ("Next Available Sitting", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_date_handles_iso_and_dd_mm_yyyy_and_placeholders(value, expected):
    assert _parse_date(value) == expected


def test_discover_action_id_scans_chunks_for_search_bill_action():
    adapter = SingaporeParliamentAdapter()
    chunk_path = "/_next/static/chunks/1-aaaaaaaaaaaaaaaa.js"
    pages = {
        "/parliamentary-business/bills-introduced": f'<script src="{chunk_path}">',
        chunk_path: (
            '(0,s.createServerReference)("7f2e58132b2b782845f11241cce0c1647606c75d86",'
            's.callServer,void 0,s.findSourceMapURL,"searchBillAction")'
        ),
    }
    adapter._get = lambda path: pages[path]

    action_id = adapter._discover_action_id()

    assert action_id == "7f2e58132b2b782845f11241cce0c1647606c75d86"


def test_discover_action_id_raises_when_not_found_in_any_chunk():
    adapter = SingaporeParliamentAdapter()
    chunk_path = "/_next/static/chunks/1-aaaaaaaaaaaaaaaa.js"
    pages = {
        "/parliamentary-business/bills-introduced": f'<script src="{chunk_path}">',
        chunk_path: "some unrelated bundle content",
    }
    adapter._get = lambda path: pages[path]

    with pytest.raises(RuntimeError):
        adapter._discover_action_id()

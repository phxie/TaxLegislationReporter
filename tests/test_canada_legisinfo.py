import datetime as dt

from app.ingestion.canada_legisinfo import (
    CanadaLegisinfoAdapter,
    _extract_status_events,
    _strip_html,
)


def _list_entry(number="C-1", session="45-1", latest_activity="2026-08-01T10:00:00-04:00"):
    return {
        "BillNumberFormatted": number,
        "ParlSessionCode": session,
        "LatestActivityDateTime": latest_activity,
    }


def _detail(
    number_code="C-1",
    title="An Act to amend the Income Tax Act",
    summary="<div>Increases the basic personal <b>tax</b> credit.</div>",
    status_id=349,
    status_name="At second reading",
    sponsor="Jane Example",
):
    return {
        "NumberCode": number_code,
        "ShortTitleEn": title,
        "LongTitleEn": title,
        "ShortLegislativeSummaryEn": summary,
        "StatusId": status_id,
        "StatusNameEn": status_name,
        "SponsorPersonName": sponsor,
        "LatestBillEventDateTime": "2026-07-15T14:00:00",
        "BillStages": {
            "HouseBillStages": [
                {
                    "SignificantEvents": [
                        {
                            "EventNameEn": "Introduction and first reading",
                            "EventDateTime": "2026-06-01T00:00:00",
                            "EventTypeId": 60110,
                        }
                    ]
                }
            ],
        },
    }


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = CanadaLegisinfoAdapter()
    adapter._fetch_bills_list = lambda: [_list_entry("C-1"), _list_entry("C-2")]
    adapter._fetch_bill_detail = lambda number_code, session_code: {
        "C-1": _detail(number_code="C-1"),
        "C-2": _detail(
            number_code="C-2",
            title="An Act respecting national parks",
            summary="<div>Expands park boundaries.</div>",
        ),
    }[number_code]

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "CANADA"
    assert bill.source_bill_id == "C-1"
    assert bill.session == "45-1"
    assert bill.bill_number == "C-1"
    assert bill.source_label == "Parliament of Canada (LEGISinfo)"
    assert bill.sponsors == ["Jane Example"]
    assert bill.status_text == "At second reading"
    assert bill.status_code == "349"
    assert "tax" in bill.tax_keywords_matched
    assert bill.summary == "Increases the basic personal tax credit."
    assert bill.introduced_date == dt.date(2026, 6, 1)
    assert bill.last_action_date == dt.date(2026, 7, 15)
    assert bill.source_url == "https://www.parl.ca/legisinfo/en/bill/45-1/c-1"
    assert bill.full_text_url == "https://www.parl.ca/DocumentViewer/en/45-1/bill/C-1/first-reading"
    assert len(bill.status_events) == 1
    assert bill.status_events[0].action_text == "Introduction and first reading"


def test_fetch_updates_skips_bills_not_updated_since():
    adapter = CanadaLegisinfoAdapter()
    adapter._fetch_bills_list = lambda: [
        _list_entry("C-1", latest_activity="2026-01-01T00:00:00-05:00"),
        _list_entry("C-2", latest_activity="2026-09-01T00:00:00-04:00"),
    ]
    detail_calls = []

    def _fetch_bill_detail(number_code, session_code):
        detail_calls.append(number_code)
        return _detail(number_code=number_code)

    adapter._fetch_bill_detail = _fetch_bill_detail

    since = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    results = list(adapter.fetch_updates(since=since))

    # C-1's LatestActivityDateTime predates `since` -- no detail fetch, not yielded.
    assert detail_calls == ["C-2"]
    assert [b.source_bill_id for b in results] == ["C-2"]


def test_extract_status_events_covers_house_senate_and_royal_assent():
    bill_stages = {
        "HouseBillStages": [
            {"SignificantEvents": [{"EventNameEn": "First reading", "EventDateTime": "2026-01-01T00:00:00"}]}
        ],
        "SenateBillStages": [
            {"SignificantEvents": [{"EventNameEn": "Third reading", "EventDateTime": "2026-03-01T00:00:00"}]}
        ],
        "RoyalAssent": [
            {"SignificantEvents": [{"EventNameEn": "Royal assent", "EventDateTime": "2026-04-01T10:00:00"}]}
        ],
    }

    events = _extract_status_events(bill_stages)

    assert [e.action_text for e in events] == ["First reading", "Third reading", "Royal assent"]
    assert events[2].event_date == dt.date(2026, 4, 1)


def test_strip_html_removes_tags_and_unescapes_entities():
    assert _strip_html("<div>Amends &amp; clarifies the <b>Excise</b> Tax Act.</div>") == (
        "Amends & clarifies the Excise Tax Act."
    )

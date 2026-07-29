import httpx
import respx

from app.ingestion.congress_gov import CongressGovAdapter

BASE = "https://api.congress.gov/v3"


@respx.mock
def test_fetch_updates_filters_and_normalizes_tax_relevant_bill():
    list_page = {
        "bills": [
            {
                "congress": 119,
                "type": "hr",
                "number": "1234",
                "title": "A bill to amend the Internal Revenue Code",
                "latestAction": {"actionDate": "2026-07-01", "text": "Referred to committee"},
                "url": "https://api.congress.gov/v3/bill/119/hr/1234",
            },
            {
                "congress": 119,
                "type": "hr",
                "number": "9999",
                "title": "A bill about national parks",
                "latestAction": {"actionDate": "2026-07-02", "text": "Introduced"},
                "url": "https://api.congress.gov/v3/bill/119/hr/9999",
            },
        ]
    }
    respx.get(f"{BASE}/bill").mock(
        side_effect=[
            httpx.Response(200, json=list_page),
            httpx.Response(200, json={"bills": []}),
        ]
    )

    respx.get(f"{BASE}/bill/119/hr/1234/subjects").mock(
        return_value=httpx.Response(
            200,
            json={"subjects": {"policyArea": {"name": "Taxation"}, "legislativeSubjects": []}},
        )
    )
    respx.get(f"{BASE}/bill/119/hr/9999/subjects").mock(
        return_value=httpx.Response(
            200,
            json={"subjects": {"policyArea": {"name": "Environment"}, "legislativeSubjects": []}},
        )
    )

    respx.get(f"{BASE}/bill/119/hr/1234").mock(
        return_value=httpx.Response(
            200,
            json={
                "bill": {
                    "sponsors": [{"fullName": "Rep. Example"}],
                    "introducedDate": "2026-06-15",
                    "textVersions": {"url": "https://example.com/text"},
                }
            },
        )
    )
    respx.get(f"{BASE}/bill/119/hr/1234/actions").mock(
        return_value=httpx.Response(
            200,
            json={
                "actions": [
                    {"actionDate": "2026-06-15", "text": "Introduced in House", "actionCode": "Intro-H"},
                    {"actionDate": "2026-07-01", "text": "Referred to committee", "actionCode": "Refer"},
                ]
            },
        )
    )

    adapter = CongressGovAdapter(api_key="test-key", base_url=BASE)
    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "FEDERAL"
    assert bill.source_bill_id == "hr1234"
    assert bill.session == "119"
    assert bill.bill_number == "HR 1234"
    assert bill.source_label == "Congress.gov"
    assert bill.tax_keywords_matched == ["policy_area:taxation"]
    assert bill.sponsors == ["Rep. Example"]
    assert len(bill.status_events) == 2
    assert bill.status_events[0].action_text == "Introduced in House"

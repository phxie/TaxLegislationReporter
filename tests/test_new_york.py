import httpx
import respx

from app.ingestion.new_york import NewYorkSenateAdapter

BASE = "https://legislation.nysenate.gov/api/3"


@respx.mock
def test_fetch_updates_filters_and_normalizes_tax_relevant_bill():
    respx.get(url__regex=rf"{BASE}/bills/updates/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 2,
                "offsetStart": 1,
                "offsetEnd": 2,
                "result": {
                    "items": [
                        {"id": {"basePrintNo": "S1234", "session": 2025}},
                        {"id": {"basePrintNo": "S9999", "session": 2025}},
                    ]
                },
            },
        )
    )

    respx.get(f"{BASE}/bills/2025/S1234").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "title": "An act to amend the tax law, in relation to income tax credits",
                    "summary": "Provides a new income tax credit",
                    "sponsor": {"member": {"fullName": "Sen. Example"}},
                    "status": {
                        "statusType": "IN_SENATE_COMM",
                        "statusDesc": "In Senate Committee",
                        "actionDate": "2026-07-01",
                        "committeeName": "Budget",
                    },
                    "publishedDateTime": "2025-01-14T10:36:22",
                    "actions": {
                        "items": [
                            {"date": "2025-01-14", "text": "REFERRED TO BUDGET", "sequenceNo": 1},
                            {"date": "2026-07-01", "text": "REPORTED AND COMMITTED", "sequenceNo": 2},
                        ]
                    },
                }
            },
        )
    )
    respx.get(f"{BASE}/bills/2025/S9999").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "title": "An act relating to state parks",
                    "summary": "Expands state park boundaries",
                    "sponsor": {"member": {"fullName": "Sen. Other"}},
                    "status": {
                        "statusType": "IN_SENATE_COMM",
                        "statusDesc": "In Senate Committee",
                        "actionDate": "2026-07-01",
                        "committeeName": "Environmental Conservation",
                    },
                    "publishedDateTime": "2025-01-14T10:36:22",
                    "actions": {"items": []},
                }
            },
        )
    )

    adapter = NewYorkSenateAdapter(api_key="test-key", base_url=BASE)
    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "NY"
    assert bill.source_bill_id == "S1234"
    assert bill.session == "2025"
    assert bill.sponsors == ["Sen. Example"]
    assert bill.source_label == "NY Senate Open Legislation API"
    assert "tax" in bill.tax_keywords_matched
    assert len(bill.status_events) == 2

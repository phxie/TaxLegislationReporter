import time

import httpx
import pytest
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


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", calls.append)
    return calls


@respx.mock
def test_get_retries_429_honoring_retry_after(sleeps):
    route = respx.get(f"{BASE}/bills/2025/S1234").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json={"result": {"title": "ok"}}),
        ]
    )

    adapter = NewYorkSenateAdapter(api_key="test-key", base_url=BASE, min_request_interval=0)
    data = adapter._get("/bills/2025/S1234")

    assert data == {"result": {"title": "ok"}}
    assert route.call_count == 2
    assert sleeps == [3.0]


@respx.mock
def test_get_retries_429_without_retry_after_using_backoff(sleeps):
    route = respx.get(f"{BASE}/bills/2025/S1234").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"result": {}}),
        ]
    )

    adapter = NewYorkSenateAdapter(api_key="test-key", base_url=BASE, min_request_interval=0)
    adapter._get("/bills/2025/S1234")

    assert route.call_count == 2
    assert len(sleeps) == 1 and sleeps[0] >= 1


@respx.mock
def test_get_does_not_retry_client_errors(sleeps):
    route = respx.get(f"{BASE}/bills/2025/S1234").mock(return_value=httpx.Response(404))

    adapter = NewYorkSenateAdapter(api_key="test-key", base_url=BASE, min_request_interval=0)
    with pytest.raises(httpx.HTTPStatusError):
        adapter._get("/bills/2025/S1234")

    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_requests_are_throttled(sleeps):
    respx.get(f"{BASE}/bills/2025/S1234").mock(return_value=httpx.Response(200, json={}))

    adapter = NewYorkSenateAdapter(api_key="test-key", base_url=BASE, min_request_interval=0.15)
    adapter._get("/bills/2025/S1234")
    adapter._get("/bills/2025/S1234")

    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 0.15

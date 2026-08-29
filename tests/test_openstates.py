import csv
import datetime as dt
import io
import zipfile

import httpx

from app.ingestion.openstates import (
    OpenStatesAdapter,
    _extract_status_events,
    _full_text_url,
    _parse_session_zip,
)


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _bill_row(
    id="ocd-bill/1111",
    identifier="HB 123",
    title="An Act relating to the sales tax on digital goods",
    classification="['bill']",
    subject="[]",
    session_identifier="892",
    jurisdiction="Texas",
):
    return {
        "id": id,
        "identifier": identifier,
        "title": title,
        "classification": classification,
        "subject": subject,
        "session_identifier": session_identifier,
        "jurisdiction": jurisdiction,
        "organization_classification": "lower",
    }


def _build_session_zip(
    bills=None, actions=None, sponsorships=None, versions=None, version_links=None
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("TX/892/TX_892_bills.csv", _csv_bytes(bills or [_bill_row()]))
        zf.writestr("TX/892/TX_892_bill_actions.csv", _csv_bytes(actions or []))
        zf.writestr("TX/892/TX_892_bill_sponsorships.csv", _csv_bytes(sponsorships or []))
        zf.writestr("TX/892/TX_892_bill_versions.csv", _csv_bytes(versions or []))
        zf.writestr("TX/892/TX_892_bill_version_links.csv", _csv_bytes(version_links or []))
    return buffer.getvalue()


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas"])
    adapter._current_session_download_url = lambda jurisdiction: ("892", "https://example.com/tx.zip")
    zip_bytes = _build_session_zip(
        bills=[
            _bill_row(id="a", title="An Act relating to the sales tax on digital goods"),
            _bill_row(id="b", title="An Act relating to state park funding"),
        ],
        actions=[
            {
                "id": "1", "bill_id": "a", "organization_id": "", "description": "Filed",
                "date": "2025-01-14", "classification": "['filing']", "order": "0",
            },
            {
                "id": "2", "bill_id": "a", "organization_id": "", "description": "Referred to committee",
                "date": "2025-03-02", "classification": "[]", "order": "1",
            },
        ],
        sponsorships=[
            {
                "id": "s1", "name": "Jane Example", "entity_type": "person", "organization_id": "",
                "person_id": "", "bill_id": "a", "primary": "True", "classification": "primary",
            },
        ],
        versions=[
            {
                "id": "v1", "bill_id": "a", "note": "Introduced", "date": "2025-01-14",
                "classification": "", "extras": "{}",
            },
        ],
        version_links=[
            {
                "id": "l1", "media_type": "application/pdf",
                "url": "https://example.com/hb123.pdf", "version_id": "v1",
            },
        ],
    )
    adapter._download_zip = lambda url: zip_bytes

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "TX"
    assert bill.source_bill_id == "a"
    assert bill.bill_number == "HB 123"
    assert bill.session == "892"
    assert bill.source_label == "Open States"
    assert bill.sponsors == ["Jane Example"]
    assert "sales tax" in bill.tax_keywords_matched
    assert bill.introduced_date == dt.date(2025, 1, 14)
    assert bill.last_action_date == dt.date(2025, 3, 2)
    assert bill.status_text == "Referred to committee"
    assert bill.full_text_url == "https://example.com/hb123.pdf"
    assert bill.source_url == "https://openstates.org/tx/bills/892/HB123/"
    assert [e.action_text for e in bill.status_events] == ["Filed", "Referred to committee"]


def test_subject_taxonomy_catches_bills_a_title_check_alone_would_miss():
    # Real finding from live data: Open States' own "Taxation--*" subject
    # family catches bills whose titles never say "tax" at all. The
    # existing word-boundary "taxation" keyword already matches this
    # because subject is passed as the `summary` argument.
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas"])
    adapter._current_session_download_url = lambda jurisdiction: ("892", "https://example.com/tx.zip")
    zip_bytes = _build_session_zip(
        bills=[
            _bill_row(
                id="a",
                title="Relating to certain exemptions and appraisal procedures",
                subject="['Taxation--Property-Exemptions (I0793)']",
            )
        ]
    )
    adapter._download_zip = lambda url: zip_bytes

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    assert "taxation" in results[0].tax_keywords_matched


def test_source_bill_id_strips_the_ocd_bill_prefix():
    # Every other source's source_bill_id is slash-free; the dashboard's
    # bill-detail route/links build a path from it unescaped, so the raw
    # "ocd-bill/<uuid>" form (a literal "/") broke routing -- confirmed
    # live (a real 404) before this was fixed.
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas"])
    adapter._current_session_download_url = lambda jurisdiction: ("892", "https://example.com/tx.zip")
    zip_bytes = _build_session_zip(bills=[_bill_row(id="ocd-bill/1b57484f-e17f-406c-ab46-b4641268711a")])
    adapter._download_zip = lambda url: zip_bytes

    results = list(adapter.fetch_updates(since=None))

    assert results[0].source_bill_id == "1b57484f-e17f-406c-ab46-b4641268711a"
    assert "/" not in results[0].source_bill_id


def test_taxicab_bill_is_not_a_false_positive():
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas"])
    adapter._current_session_download_url = lambda jurisdiction: ("892", "https://example.com/tx.zip")
    zip_bytes = _build_session_zip(
        bills=[_bill_row(title="An Act relating to taxicab and rideshare licensing")]
    )
    adapter._download_zip = lambda url: zip_bytes

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_fetch_updates_covers_every_configured_jurisdiction():
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas", "Florida"])
    resolved = {"Texas": ("892", "https://example.com/tx.zip"), "Florida": ("2026", "https://example.com/fl.zip")}
    adapter._current_session_download_url = lambda jurisdiction: resolved[jurisdiction]

    tx_bills = [_bill_row(id="tx-1", jurisdiction="Texas")]
    fl_bills = [_bill_row(id="fl-1", jurisdiction="Florida")]
    zips = {
        "https://example.com/tx.zip": _build_session_zip(bills=tx_bills),
        "https://example.com/fl.zip": _build_session_zip(bills=fl_bills),
    }
    adapter._download_zip = lambda url: zips[url]

    results = list(adapter.fetch_updates(since=None))

    assert {b.source_bill_id for b in results} == {"tx-1", "fl-1"}
    assert {b.jurisdiction for b in results} == {"TX", "FL"}


def test_fetch_updates_skips_jurisdiction_with_no_csv_export():
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas", "Florida"])
    adapter._current_session_download_url = lambda jurisdiction: (
        None if jurisdiction == "Texas" else ("2026", "https://example.com/fl.zip")
    )
    fl_bills = [_bill_row(id="fl-1", jurisdiction="Florida")]
    adapter._download_zip = lambda url: _build_session_zip(bills=fl_bills)

    results = list(adapter.fetch_updates(since=None))

    assert {b.source_bill_id for b in results} == {"fl-1"}


def test_fetch_updates_skips_jurisdiction_on_download_failure():
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas", "Florida"])
    adapter._current_session_download_url = lambda jurisdiction: (jurisdiction, f"https://example.com/{jurisdiction}.zip")

    def _download_zip(url):
        if "Texas" in url:
            raise httpx.HTTPError("boom")
        return _build_session_zip(bills=[_bill_row(id="fl-1", jurisdiction="Florida")])

    adapter._download_zip = _download_zip

    results = list(adapter.fetch_updates(since=None))

    assert {b.source_bill_id for b in results} == {"fl-1"}


def test_fetch_updates_skips_jurisdiction_on_export_parsing_failure():
    # A bad/unparseable export for one state (e.g. a genuinely malformed
    # zip) shouldn't take down the other 47 -- confirmed necessary live
    # (see test_parse_session_zip_treats_a_missing_optional_table_as_empty
    # for the specific bug this caught).
    adapter = OpenStatesAdapter(api_key="key", jurisdictions=["Texas", "Florida"])
    adapter._current_session_download_url = lambda jurisdiction: (
        jurisdiction,
        f"https://example.com/{jurisdiction}.zip",
    )

    def _download_zip(url):
        if "Texas" in url:
            return b"not a valid zip file"
        fl_bills = [_bill_row(id="fl-1", jurisdiction="Florida")]
        return _build_session_zip(bills=fl_bills)

    adapter._download_zip = _download_zip

    results = list(adapter.fetch_updates(since=None))

    assert {b.source_bill_id for b in results} == {"fl-1"}


def test_full_text_url_prefers_the_latest_version_by_date():
    versions_by_bill = {
        "a": [
            {"id": "v-old", "bill_id": "a", "date": "2025-01-01"},
            {"id": "v-new", "bill_id": "a", "date": "2025-06-01"},
        ]
    }
    links_by_version = {
        "v-old": [{"url": "https://example.com/old.pdf"}],
        "v-new": [{"url": "https://example.com/new.pdf"}],
    }

    url = _full_text_url("a", versions_by_bill, links_by_version)

    assert url == "https://example.com/new.pdf"


def test_extract_status_events_sorts_by_order_not_list_order():
    actions = [
        {"date": "2025-03-02", "description": "Referred to committee", "order": "2"},
        {"date": "2025-01-14", "description": "Filed", "order": "1"},
    ]

    events = _extract_status_events(actions)

    assert [e.action_text for e in events] == ["Filed", "Referred to committee"]


def test_parse_session_zip_raises_when_bills_csv_is_missing():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("TX/892/TX_892_bill_actions.csv", _csv_bytes([]))
        # bills.csv deliberately omitted -- there's nothing to build without it.

    try:
        list(_parse_session_zip(buffer.getvalue()))
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_parse_session_zip_treats_a_missing_optional_table_as_empty():
    # Confirmed live: Kentucky's real export omitted bill_sponsorships.csv
    # entirely (an empty-table export doesn't include the file at all),
    # which used to raise and abort the whole 48-state run partway through.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("KY/2026RS/KY_2026RS_bills.csv", _csv_bytes([_bill_row(id="a")]))
        zf.writestr(
            "KY/2026RS/KY_2026RS_bill_actions.csv",
            _csv_bytes(
                [
                    {
                        "id": "1", "bill_id": "a", "organization_id": "", "description": "Filed",
                        "date": "2026-01-05", "classification": "['filing']", "order": "0",
                    }
                ]
            ),
        )
        # bill_sponsorships.csv, bill_versions.csv, bill_version_links.csv
        # all deliberately omitted.

    results = list(_parse_session_zip(buffer.getvalue()))

    assert len(results) == 1
    assert results[0].sponsors == []
    assert results[0].full_text_url is None
    assert results[0].status_text == "Filed"

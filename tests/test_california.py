import io
import zipfile

from app.ingestion.california import CaliforniaAdapter


def _row(*fields: str | None) -> str:
    # The parser strips backticks and treats the literal "NULL" as None
    # regardless of quoting, so quoting every non-null field uniformly here
    # (rather than mimicking MySQL's numeric-vs-string quoting) is sufficient.
    return "\t".join("NULL" if f is None else f"`{f}`" for f in fields)


def _build_zip() -> bytes:
    bill_tbl = "\n".join(
        [
            _row(
                "202520260AB100", "20252026", "0", "AB", "100", "Introduced",
                None, None, None, None, "20250AB10099INT", "Y", "HS_INT",
                "2026-07-22 10:41:43", "CS61", "Committee", "Senate", "In Committee Process",
                "2025-02-23 00:00:00",
            ),
            _row(
                "202520260AB200", "20252026", "0", "AB", "200", "Introduced",
                None, None, None, None, "20250AB20099INT", "Y", "HS_INT",
                "2026-07-22 10:41:43", "CS61", "Committee", "Senate", "In Committee Process",
                "2025-02-23 00:00:00",
            ),
        ]
    )

    bill_history_tbl = "\n".join(
        [
            _row(
                "202520260AB100", "1", "2025-01-10 00:00:00", "Introduced. Read first time.",
                "LEG_ESI", "2025-01-10 00:00:00", "1", "1000", "Applied", "Assembly", "Floor", None,
                "In Assembly Process",
            ),
            _row(
                "202520260AB100", "2", "2025-03-01 00:00:00", "Referred to Com. on REV. AND TAX.",
                "LEG_ESI", "2025-03-01 00:00:00", "2", "1010", "Applied", "Assembly", "Committee", None,
                "In Committee Process",
            ),
        ]
    )

    bill_version_tbl = "\n".join(
        [
            _row(
                "20250AB10099INT", "202520260AB100", "99", "2025-01-10 00:00:00", "Introduced",
                None, "Income taxes: credits: child care.", None, None, "No", None, None, None, "Y",
                "BILL_VERSION_TBL_1.lob", "Y", "LEG_ESI", "2025-01-10 00:00:00",
            ),
            _row(
                "20250AB20099INT", "202520260AB200", "99", "2025-01-10 00:00:00", "Introduced",
                None, "State parks: boundaries.", None, None, "No", None, None, None, "N",
                "BILL_VERSION_TBL_2.lob", "Y", "LEG_ESI", "2025-01-10 00:00:00",
            ),
        ]
    )

    bill_version_authors_tbl = "\n".join(
        [
            _row(
                "20250AB10099INT", "Lead Author", "Assembly", "Smith", "Author", None, "Y",
                "LEG_ESI", "2025-01-10 00:00:00", "Y",
            ),
        ]
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("BILL_TBL.dat", bill_tbl)
        zf.writestr("BILL_HISTORY_TBL.dat", bill_history_tbl)
        zf.writestr("BILL_VERSION_TBL.dat", bill_version_tbl)
        zf.writestr("BILL_VERSION_AUTHORS_TBL.dat", bill_version_authors_tbl)
    return buffer.getvalue()


def test_fetch_updates_filters_via_taxlevy_and_keyword():
    adapter = CaliforniaAdapter()
    adapter._download_zip = lambda day_abbr: _build_zip()

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "CA"
    assert bill.source_bill_id == "202520260AB100"
    assert bill.bill_number == "AB 100"
    assert bill.title == "Income taxes: credits: child care."
    assert bill.source_label == "California Legislative Information (PUBINFO)"
    assert bill.tax_keywords_matched == ["taxlevy_flag"]
    assert bill.sponsors == ["Smith"]
    assert len(bill.status_events) == 2
    assert bill.status_events[0].action_text == "Introduced. Read first time."
    assert bill.last_action_date.isoformat() == "2025-03-01"
    assert bill.introduced_date.isoformat() == "2025-01-10"

from __future__ import annotations

import datetime as dt
import io
import logging
import zipfile
from collections import defaultdict
from collections.abc import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import NormalizedBill, NormalizedStatusEvent
from app.ingestion.tax_filter import is_tax_relevant_ca

logger = logging.getLogger(__name__)

WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Column orders below are taken verbatim from the LOAD DATA statements in
# pubinfo_load.zip (bill_tbl.sql etc.) -- the .dat files have no header row.
BILL_TBL_COLUMNS = [
    "bill_id",
    "session_year",
    "session_num",
    "measure_type",
    "measure_num",
    "measure_state",
    "chapter_year",
    "chapter_type",
    "chapter_session_num",
    "chapter_num",
    "latest_bill_version_id",
    "active_flg",
    "trans_uid",
    "trans_update",
    "current_location",
    "current_secondary_loc",
    "current_house",
    "current_status",
    "days_31st_in_print",
]

BILL_HISTORY_TBL_COLUMNS = [
    "bill_id",
    "bill_history_id",
    "action_date",
    "action",
    "trans_uid",
    "trans_update_dt",
    "action_sequence",
    "action_code",
    "action_status",
    "primary_location",
    "secondary_location",
    "ternary_location",
    "end_status",
]

# Position 15 (`lob_file_ref`) holds the .lob filename consumed by the
# original MySQL LOAD DATA ... SET BILL_XML=LOAD_FILE(...) step; we don't
# need the bill full-text blob, just the column offset it occupies.
BILL_VERSION_TBL_COLUMNS = [
    "bill_version_id",
    "bill_id",
    "version_num",
    "bill_version_action_date",
    "bill_version_action",
    "request_num",
    "subject",
    "vote_required",
    "appropriation",
    "fiscal_committee",
    "local_program",
    "substantive_changes",
    "urgency",
    "taxlevy",
    "lob_file_ref",
    "active_flg",
    "trans_uid",
    "trans_update",
]

BILL_VERSION_AUTHORS_TBL_COLUMNS = [
    "bill_version_id",
    "type",
    "house",
    "name",
    "contribution",
    "committee_members",
    "active_flg",
    "trans_uid",
    "trans_update",
    "primary_author_flg",
]


def _parse_dat_rows(raw: bytes, columns: list[str]) -> list[dict[str, str | None]]:
    text = raw.decode("utf-8", errors="replace")
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        record: dict[str, str | None] = {}
        for idx, col in enumerate(columns):
            value = fields[idx] if idx < len(fields) else None
            if value is None or value == "NULL":
                record[col] = None
            else:
                record[col] = value.strip("`")
        rows.append(record)
    return rows


def _parse_ca_datetime(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None


class CaliforniaAdapter:
    source_name = "CA"

    def __init__(self, base_url: str = "https://downloads.leginfo.legislature.ca.gov"):
        self.base_url = base_url.rstrip("/")

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _download_zip(self, day_abbr: str) -> bytes:
        url = f"{self.base_url}/pubinfo_daily_{day_abbr}.zip"
        with httpx.stream("GET", url, timeout=300.0, follow_redirects=True) as resp:
            resp.raise_for_status()
            buffer = io.BytesIO()
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                buffer.write(chunk)
            return buffer.getvalue()

    def fetch_updates(self, since: dt.datetime | None) -> Iterator[NormalizedBill]:
        # California only publishes a full current-session snapshot once a
        # day (the small per-weekday delta zip omits BILL_VERSION_TBL, which
        # is where bill titles/subjects and the `taxlevy` flag live), so
        # `since` isn't used to narrow the fetch -- every run pulls the full
        # snapshot and the diff pipeline detects what's actually changed.
        today_abbr = WEEKDAY_ABBR[dt.date.today().weekday()]
        logger.info("Downloading CA pubinfo_daily_%s.zip", today_abbr)
        zip_bytes = self._download_zip(today_abbr)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            bill_rows = _parse_dat_rows(zf.read("BILL_TBL.dat"), BILL_TBL_COLUMNS)
            history_rows = _parse_dat_rows(zf.read("BILL_HISTORY_TBL.dat"), BILL_HISTORY_TBL_COLUMNS)
            version_rows = _parse_dat_rows(zf.read("BILL_VERSION_TBL.dat"), BILL_VERSION_TBL_COLUMNS)
            author_rows = _parse_dat_rows(
                zf.read("BILL_VERSION_AUTHORS_TBL.dat"), BILL_VERSION_AUTHORS_TBL_COLUMNS
            )

        versions_by_id = {row["bill_version_id"]: row for row in version_rows}

        history_by_bill: dict[str, list[dict]] = defaultdict(list)
        for row in history_rows:
            history_by_bill[row["bill_id"]].append(row)

        authors_by_version: dict[str, list[dict]] = defaultdict(list)
        for row in author_rows:
            authors_by_version[row["bill_version_id"]].append(row)

        for bill in bill_rows:
            normalized = self._normalize_bill(bill, versions_by_id, history_by_bill, authors_by_version)
            if normalized is not None:
                yield normalized

    def _normalize_bill(
        self,
        bill: dict,
        versions_by_id: dict[str, dict],
        history_by_bill: dict[str, list[dict]],
        authors_by_version: dict[str, list[dict]],
    ) -> NormalizedBill | None:
        bill_id = bill["bill_id"]
        version = versions_by_id.get(bill.get("latest_bill_version_id") or "")
        subject = version.get("subject") if version else None
        taxlevy = version.get("taxlevy") if version else None

        measure_type = bill.get("measure_type") or ""
        measure_num = bill.get("measure_num") or ""
        bill_number = f"{measure_type} {measure_num}".strip()

        is_relevant, matched = is_tax_relevant_ca(taxlevy, subject, bill_number)
        if not is_relevant:
            return None

        history = sorted(
            history_by_bill.get(bill_id, []),
            key=lambda r: (r.get("action_sequence") or "0").zfill(6),
        )
        status_events = []
        action_dates: list[dt.date] = []
        for row in history:
            action_date = _parse_ca_datetime(row.get("action_date"))
            if action_date is None:
                continue
            action_dates.append(action_date)
            status_events.append(
                NormalizedStatusEvent(
                    event_date=action_date,
                    action_text=row.get("action") or "",
                    action_code=row.get("action_code"),
                    sequence_num=int(row["action_sequence"]) if row.get("action_sequence") else None,
                )
            )

        sponsors = []
        if version:
            for author in authors_by_version.get(version["bill_version_id"], []):
                if author.get("name"):
                    sponsors.append(author["name"])

        source_url = f"https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id={bill_id}"

        return NormalizedBill(
            jurisdiction="CA",
            source_bill_id=bill_id,
            session=bill.get("session_year") or "",
            bill_number=bill_number,
            title=subject or bill_number,
            source_label="California Legislative Information (PUBINFO)",
            summary=subject,
            sponsors=sponsors,
            status_text=bill.get("current_status"),
            status_code=None,
            last_action_date=max(action_dates) if action_dates else None,
            introduced_date=min(action_dates) if action_dates else None,
            full_text_url=None,
            source_url=source_url,
            tax_keywords_matched=matched,
            raw_source_payload={"bill": bill, "version": version},
            status_events=status_events,
        )

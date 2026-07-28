from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app import repository
from app.db import get_db
from app.models import JURISDICTIONS
from app.template_engine import templates

router = APIRouter()

RECENT_WINDOW = dt.timedelta(days=14)


def _parse_optional_date(value: str | None) -> dt.date | None:
    # htmx serializes every form field, so an untouched <input type="date">
    # arrives as an empty string rather than being omitted -- FastAPI's date
    # query-param binding rejects "" outright, so parse it ourselves instead.
    if not value:
        return None
    return dt.date.fromisoformat(value)


def _filtered_bills(
    db: Session,
    jurisdiction: str | None,
    status_text: str | None,
    keyword: str | None,
    date_from: dt.date | None,
    date_to: dt.date | None,
):
    bills = repository.list_bills(
        db,
        jurisdiction=jurisdiction or None,
        status_text=status_text or None,
        keyword=keyword or None,
        date_from=date_from,
        date_to=date_to,
    )
    since = dt.datetime.now(dt.UTC) - RECENT_WINDOW
    recent_ids = repository.bill_ids_with_recent_changes(db, since=since)
    return bills, recent_ids


@router.get("/")
def dashboard(
    request: Request,
    jurisdiction: str | None = Query(default=None),
    status_text: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    parsed_date_from = _parse_optional_date(date_from)
    parsed_date_to = _parse_optional_date(date_to)
    bills, recent_ids = _filtered_bills(
        db, jurisdiction, status_text, keyword, parsed_date_from, parsed_date_to
    )
    recent_changes = repository.recent_changes(db, since=dt.datetime.now(dt.UTC) - RECENT_WINDOW)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "jurisdictions": JURISDICTIONS,
            "bills": bills,
            "recent_ids": recent_ids,
            "recent_changes": recent_changes,
            "filters": {
                "jurisdiction": jurisdiction or "",
                "status_text": status_text or "",
                "keyword": keyword or "",
                "date_from": parsed_date_from,
                "date_to": parsed_date_to,
            },
        },
    )


@router.get("/partials/bills")
def bills_partial(
    request: Request,
    jurisdiction: str | None = Query(default=None),
    status_text: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    bills, recent_ids = _filtered_bills(
        db,
        jurisdiction,
        status_text,
        keyword,
        _parse_optional_date(date_from),
        _parse_optional_date(date_to),
    )
    return templates.TemplateResponse(
        request,
        "partials/bill_table.html",
        {"bills": bills, "recent_ids": recent_ids},
    )

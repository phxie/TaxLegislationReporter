from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app import repository
from app.db import get_db
from app.template_engine import templates

router = APIRouter()


def _parse_optional_date(value: str | None) -> dt.date | None:
    # htmx serializes every form field, so an untouched <input type="date">
    # arrives as an empty string rather than being omitted -- FastAPI's date
    # query-param binding rejects "" outright, so parse it ourselves instead.
    if not value:
        return None
    return dt.date.fromisoformat(value)


@router.get("/publications")
def publications(
    request: Request,
    source: str | None = Query(default=None),
    relevant_jurisdiction: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    parsed_date_from = _parse_optional_date(date_from)
    parsed_date_to = _parse_optional_date(date_to)
    items = repository.list_publications(
        db,
        source=source or None,
        relevant_jurisdiction=relevant_jurisdiction or None,
        keyword=keyword or None,
        date_from=parsed_date_from,
        date_to=parsed_date_to,
    )

    return templates.TemplateResponse(
        request,
        "publications.html",
        {
            "publications": items,
            "sources": repository.distinct_publication_sources(db),
            "jurisdictions": repository.distinct_publication_jurisdictions(db),
            "filters": {
                "source": source or "",
                "relevant_jurisdiction": relevant_jurisdiction or "",
                "keyword": keyword or "",
                "date_from": parsed_date_from,
                "date_to": parsed_date_to,
            },
        },
    )


@router.get("/publications/partials/list")
def publications_list_partial(
    request: Request,
    source: str | None = Query(default=None),
    relevant_jurisdiction: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    items = repository.list_publications(
        db,
        source=source or None,
        relevant_jurisdiction=relevant_jurisdiction or None,
        keyword=keyword or None,
        date_from=_parse_optional_date(date_from),
        date_to=_parse_optional_date(date_to),
    )
    return templates.TemplateResponse(
        request,
        "partials/publication_table.html",
        {"publications": items},
    )


@router.get("/publications/{publication_id}")
def publication_detail(request: Request, publication_id: int, db: Session = Depends(get_db)):
    publication = repository.get_publication(db, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found")

    return templates.TemplateResponse(
        request,
        "publication_detail.html",
        {"publication": publication},
    )

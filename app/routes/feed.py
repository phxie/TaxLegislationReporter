from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import repository
from app.db import get_db
from app.template_engine import templates

router = APIRouter()

RECENT_WINDOW = dt.timedelta(days=14)


@router.get("/partials/recent-feed")
def recent_feed(request: Request, db: Session = Depends(get_db)):
    since = dt.datetime.now(dt.UTC) - RECENT_WINDOW
    changes = repository.recent_changes(db, since=since)
    return templates.TemplateResponse(
        request,
        "partials/recent_feed.html",
        {"recent_changes": changes},
    )

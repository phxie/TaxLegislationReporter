from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import repository
from app.db import get_db
from app.template_engine import templates

router = APIRouter()


@router.get("/bills/{jurisdiction}/{session}/{source_bill_id}")
def bill_detail(
    request: Request,
    jurisdiction: str,
    session: str,
    source_bill_id: str,
    db: Session = Depends(get_db),
):
    bill = repository.get_bill(db, jurisdiction.upper(), source_bill_id, session)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")

    return templates.TemplateResponse(
        request,
        "bill_detail.html",
        {"bill": bill},
    )

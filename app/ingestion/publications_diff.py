from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.publications_base import NormalizedPublication
from app.models import Publication


def apply_publication(db: Session, normalized: NormalizedPublication) -> tuple[Publication, bool]:
    """Insert or update a publication. Returns (publication, is_new).

    No change-log here (unlike `diff.apply_bill`): publications are
    effectively immutable once published, so updates just silently refresh
    the row and bump `last_seen_at` rather than logging a diff.
    """
    existing = db.scalars(
        select(Publication).where(
            Publication.source == normalized.source,
            Publication.url == normalized.url,
        )
    ).first()

    now = dt.datetime.now(dt.UTC)

    if existing is None:
        publication = Publication(
            source=normalized.source,
            source_label=normalized.source_label,
            url=normalized.url,
            title=normalized.title,
            summary=normalized.summary,
            published_date=normalized.published_date,
            topic_tags_json=normalized.topic_tags,
            content_type=normalized.content_type,
            is_tax_relevant=True,
            tax_keywords_matched=normalized.tax_keywords_matched,
            raw_source_payload=normalized.raw_source_payload,
            last_seen_at=now,
        )
        db.add(publication)
        db.flush()
        return publication, True

    existing.source_label = normalized.source_label
    existing.title = normalized.title
    existing.summary = normalized.summary
    existing.published_date = normalized.published_date
    existing.topic_tags_json = normalized.topic_tags
    existing.content_type = normalized.content_type
    existing.tax_keywords_matched = normalized.tax_keywords_matched
    existing.raw_source_payload = normalized.raw_source_payload
    existing.last_seen_at = now

    return existing, False

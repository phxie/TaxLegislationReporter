"""add EY_TAX_ALERTS source, drop relevant_jurisdiction check

Revision ID: ddefd3b7353c
Revises: 768919f9cc79
Create Date: 2026-08-04 17:55:05.523189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.ingestion.jurisdiction_detect import RELEVANT_JURISDICTIONS

# revision identifiers, used by Alembic.
revision: str = 'ddefd3b7353c'
down_revision: Union[str, Sequence[str], None] = '768919f9cc79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_SOURCES = ("PWC_TAX_LIBRARY",)
NEW_SOURCES = ("PWC_TAX_LIBRARY", "EY_TAX_ALERTS")


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("ck_publication_source", "publications", type_="check")
    values = ", ".join(repr(s) for s in NEW_SOURCES)
    op.create_check_constraint(
        "ck_publication_source", "publications", f"source IN ({values})"
    )

    # EY provides its own authoritative, global jurisdiction taxonomy (e.g.
    # "Guinea", "European Union") that the fixed US-state/Federal/
    # International/Multistate enum (sized for PwC's heuristic) can't cover.
    op.drop_constraint("ck_publication_relevant_jurisdiction", "publications", type_="check")


def downgrade() -> None:
    """Downgrade schema."""
    values = ", ".join(repr(j) for j in RELEVANT_JURISDICTIONS)
    op.create_check_constraint(
        "ck_publication_relevant_jurisdiction",
        "publications",
        f"relevant_jurisdiction IS NULL OR relevant_jurisdiction IN ({values})",
    )

    op.drop_constraint("ck_publication_source", "publications", type_="check")
    values = ", ".join(repr(s) for s in OLD_SOURCES)
    op.create_check_constraint(
        "ck_publication_source", "publications", f"source IN ({values})"
    )

"""add KPMG_TAXNEWSFLASH_EUROPE source

Revision ID: a1f2c3d4e5f6
Revises: ddefd3b7353c
Create Date: 2026-08-05 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1f2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'ddefd3b7353c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_SOURCES = ("PWC_TAX_LIBRARY", "EY_TAX_ALERTS")
NEW_SOURCES = ("PWC_TAX_LIBRARY", "EY_TAX_ALERTS", "KPMG_TAXNEWSFLASH_EUROPE")


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("ck_publication_source", "publications", type_="check")
    values = ", ".join(repr(s) for s in NEW_SOURCES)
    op.create_check_constraint(
        "ck_publication_source", "publications", f"source IN ({values})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_publication_source", "publications", type_="check")
    values = ", ".join(repr(s) for s in OLD_SOURCES)
    op.create_check_constraint(
        "ck_publication_source", "publications", f"source IN ({values})"
    )

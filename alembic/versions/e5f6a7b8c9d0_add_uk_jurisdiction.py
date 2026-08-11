"""add UK jurisdiction

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-11 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_JURISDICTIONS = ("FEDERAL", "CA", "NY", "CANADA", "SPAIN")
NEW_JURISDICTIONS = ("FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK")


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("ck_bill_jurisdiction", "bills", type_="check")
    values = ", ".join(repr(j) for j in NEW_JURISDICTIONS)
    op.create_check_constraint(
        "ck_bill_jurisdiction", "bills", f"jurisdiction IN ({values})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_bill_jurisdiction", "bills", type_="check")
    values = ", ".join(repr(j) for j in OLD_JURISDICTIONS)
    op.create_check_constraint(
        "ck_bill_jurisdiction", "bills", f"jurisdiction IN ({values})"
    )

"""add INDIA jurisdiction

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-12 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_JURISDICTIONS = ("FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK")
NEW_JURISDICTIONS = ("FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK", "INDIA")


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

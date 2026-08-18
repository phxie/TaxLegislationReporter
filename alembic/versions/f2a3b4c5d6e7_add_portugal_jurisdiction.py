"""add PORTUGAL jurisdiction

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_JURISDICTIONS = (
    "FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK", "INDIA", "FRANCE", "GERMANY", "SINGAPORE", "MEXICO",
)
NEW_JURISDICTIONS = (
    "FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK", "INDIA", "FRANCE", "GERMANY", "SINGAPORE", "MEXICO",
    "PORTUGAL",
)


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

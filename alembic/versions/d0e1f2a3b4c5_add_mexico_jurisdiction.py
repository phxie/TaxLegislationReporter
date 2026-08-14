"""add MEXICO jurisdiction

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-14 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_JURISDICTIONS = ("FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK", "INDIA", "FRANCE", "GERMANY", "SINGAPORE")
NEW_JURISDICTIONS = (
    "FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK", "INDIA", "FRANCE", "GERMANY", "SINGAPORE", "MEXICO",
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

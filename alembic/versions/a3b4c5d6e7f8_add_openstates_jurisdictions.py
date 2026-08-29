"""add Open States jurisdictions (48 US states excluding CA/NY)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_JURISDICTIONS = (
    "FEDERAL", "CA", "NY", "CANADA", "SPAIN", "UK", "INDIA", "FRANCE", "GERMANY", "SINGAPORE", "MEXICO",
    "PORTUGAL",
)
# California and New York already have dedicated adapters pulling directly
# from those states' own legislatures, so they're deliberately excluded
# here rather than duplicated -- see app/ingestion/openstates.py.
OPENSTATES_JURISDICTIONS = (
    "AK", "AL", "AR", "AZ", "CO", "CT", "DE", "FL", "GA", "HI", "IA", "ID",
    "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS",
    "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
)
NEW_JURISDICTIONS = OLD_JURISDICTIONS + OPENSTATES_JURISDICTIONS


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

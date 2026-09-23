"""add risk_score to bills

Revision ID: c1d2e3f4a5b6
Revises: a3b4c5d6e7f8
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("bills", sa.Column("risk_score", sa.Integer(), nullable=True))
    op.add_column(
        "bills",
        sa.Column("risk_score_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_bill_risk_score_range",
        "bills",
        "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_bill_risk_score_range", "bills", type_="check")
    op.drop_column("bills", "risk_score_requested_at")
    op.drop_column("bills", "risk_score")

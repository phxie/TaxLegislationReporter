"""add ai_summary to publications

Revision ID: b7c8d9e0f1a2
Revises: a1f2c3d4e5f6
Create Date: 2026-08-06 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'a1f2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("publications", sa.Column("ai_summary", sa.String(), nullable=True))
    op.add_column(
        "publications",
        sa.Column("ai_summary_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("publications", "ai_summary_requested_at")
    op.drop_column("publications", "ai_summary")

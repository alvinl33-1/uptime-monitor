"""Rename the check-status column.

Revision ID: 20261116_02
Revises: 20261116_01
Create Date: 2026-11-16

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261116_02"
down_revision: str | Sequence[str] | None = "20261116_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "checks",
        "10",
        new_column_name="status",
        existing_type=sa.String(),
    )


def downgrade() -> None:
    op.alter_column(
        "checks",
        "status",
        new_column_name="10",
        existing_type=sa.String(length=10),
    )

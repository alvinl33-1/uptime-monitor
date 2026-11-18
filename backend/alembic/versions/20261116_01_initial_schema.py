"""Create the initial uptime-monitor schema.

Revision ID: 20261116_01
Revises:
Create Date: 2026-11-16

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20261116_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("monitor_id", sa.Integer(), nullable=False),
        # This name matches the pre-migration schema. A later migration can
        # rename it after the application model is corrected.
        sa.Column("10", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checks_monitor_checked",
        "checks",
        ["monitor_id", "checked_at"],
        unique=False,
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("monitor_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_alert_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_alert_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_one_open_incident_per_monitor",
        "incidents",
        ["monitor_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_open_incident_per_monitor", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index("ix_checks_monitor_checked", table_name="checks")
    op.drop_table("checks")
    op.drop_table("monitors")

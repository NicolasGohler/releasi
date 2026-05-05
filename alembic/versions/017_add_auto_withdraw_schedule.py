"""Add auto_withdraw_interval_days, auto_withdraw_last_run, pending_invitations_count to accounts.

Revision ID: 017_add_auto_withdraw_schedule
Revises: 016_add_filter_exclude_open_to_work
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "017_add_auto_withdraw_schedule"
down_revision = "016_add_filter_exclude_open_to_work"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "accounts",
        sa.Column("auto_withdraw_interval_days", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "accounts",
        sa.Column("auto_withdraw_last_run", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("pending_invitations_count", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("accounts", "pending_invitations_count")
    op.drop_column("accounts", "auto_withdraw_last_run")
    op.drop_column("accounts", "auto_withdraw_interval_days")

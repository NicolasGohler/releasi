"""Add proxy_mb_used float to daily_stats for bandwidth guard.

Revision ID: 012_add_proxy_mb_used
Revises: 011_add_archived
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa

revision = "012_add_proxy_mb_used"
down_revision = "011_add_archived"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "daily_stats",
        sa.Column("proxy_mb_used", sa.Float(), nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("daily_stats", "proxy_mb_used")

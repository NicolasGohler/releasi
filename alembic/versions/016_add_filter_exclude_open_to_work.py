"""Add filter_exclude_open_to_work column to campaigns.

Revision ID: 016_add_filter_exclude_open_to_work
Revises: 015_add_lead_email_phone
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "016_add_filter_exclude_open_to_work"
down_revision = "015_add_lead_email_phone"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaigns",
        sa.Column("filter_exclude_open_to_work", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("campaigns", "filter_exclude_open_to_work")

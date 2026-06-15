"""Add priority column to campaign_lead_lists.

Controls dispatch ordering of pending leads within a campaign: when a
campaign has multiple assigned lists, leads from the higher-priority list
are dispatched first. Default 0 preserves the legacy created_at ordering
for all existing links.

Revision ID: 030_add_cll_priority
Revises: 029_add_lead_notes_table
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "030_add_cll_priority"
down_revision = "029_add_lead_notes_table"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaign_lead_lists",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("campaign_lead_lists", "priority")

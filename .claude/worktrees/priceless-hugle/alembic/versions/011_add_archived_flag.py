"""Add archived boolean to accounts, campaigns, and lead_lists.

Revision ID: 011_add_archived
Revises: 010_lead_campaign_nullable
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa

revision = "011_add_archived"
down_revision = "010_lead_campaign_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("campaigns", sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("lead_lists", sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("accounts", "archived")
    op.drop_column("campaigns", "archived")
    op.drop_column("lead_lists", "archived")

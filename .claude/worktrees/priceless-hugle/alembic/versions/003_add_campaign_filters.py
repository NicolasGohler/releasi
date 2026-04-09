"""Add profile filter columns to campaigns.

Revision ID: 003_campaign_filters
Revises: 002_timezone_proxy
Create Date: 2026-02-22
"""
from alembic import op
import sqlalchemy as sa

revision = "003_campaign_filters"
down_revision = "002_timezone_proxy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("filter_no_photo", sa.Boolean(), server_default="0"))
    op.add_column("campaigns", sa.Column("filter_min_connections", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "filter_min_connections")
    op.drop_column("campaigns", "filter_no_photo")

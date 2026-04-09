"""Make leads.campaign_id nullable for lead-list-only leads.

Revision ID: 010_lead_campaign_nullable
Revises: 009_proxy_country
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa

revision = "010_lead_campaign_nullable"
down_revision = "009_proxy_country"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite doesn't support ALTER COLUMN, so we recreate the table.
    # Use batch mode which handles the table recreation transparently.
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.alter_column(
            "campaign_id",
            existing_type=sa.String(36),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.alter_column(
            "campaign_id",
            existing_type=sa.String(36),
            nullable=False,
        )

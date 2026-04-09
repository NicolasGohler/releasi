"""Add weekend_enabled to campaigns.

Revision ID: 007_campaign_weekend
Revises: 006_remove_warmup
Create Date: 2026-02-28
"""
from alembic import op
import sqlalchemy as sa

revision = "007_campaign_weekend"
down_revision = "006_remove_warmup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.add_column(
            sa.Column("weekend_enabled", sa.Boolean(), server_default="0", nullable=False),
        )


def downgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.drop_column("weekend_enabled")

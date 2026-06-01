"""add tg_enrich_enabled to lead_lists

Revision ID: 028_list_tg_enrich_enabled
Revises: 027_add_lead_list_membership
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "028_list_tg_enrich_enabled"
down_revision = None   # standalone; apply directly
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lead_lists",
        sa.Column("tg_enrich_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("lead_lists", "tg_enrich_enabled")

"""Add twitter_url and telegram_username to leads.

Revision ID: 020_add_lead_social_fields
Revises: 019_lead_centric_model_phase1
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "020_add_lead_social_fields"
down_revision = "019_lead_centric_model_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("twitter_url", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("telegram_username", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "telegram_username")
    op.drop_column("leads", "twitter_url")

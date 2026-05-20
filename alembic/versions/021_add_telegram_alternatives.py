"""Add telegram_alternatives JSON column to leads

Revision ID: 021
Revises: 020
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("telegram_alternatives", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "telegram_alternatives")

"""Add tg_contacted_at to leads

Revision ID: 022
Revises: 021
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("tg_contacted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "tg_contacted_at")

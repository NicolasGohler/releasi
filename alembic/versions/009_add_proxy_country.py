"""Add proxy_country to accounts for auto-generated residential proxy.

Revision ID: 009_proxy_country
Revises: 008_withdraw_avatar
Create Date: 2026-03-03
"""
from alembic import op
import sqlalchemy as sa

revision = "009_proxy_country"
down_revision = "008_withdraw_avatar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("proxy_country", sa.String(63), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "proxy_country")

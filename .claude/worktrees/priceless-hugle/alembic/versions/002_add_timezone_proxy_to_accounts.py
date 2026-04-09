"""Add timezone and proxy_url to accounts, new action types.

Revision ID: 002_timezone_proxy
Revises: cff59c2df61b
Create Date: 2026-02-21
"""
from alembic import op
import sqlalchemy as sa

revision = "002_timezone_proxy"
down_revision = "cff59c2df61b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("timezone", sa.String(63), server_default="Europe/Berlin"))
    op.add_column("accounts", sa.Column("proxy_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "proxy_url")
    op.drop_column("accounts", "timezone")

"""Add dispatch_mode to accounts.

Revision ID: 013_add_dispatch_mode
Revises: 012_add_proxy_mb_used
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = "013_add_dispatch_mode"
down_revision = "012_add_proxy_mb_used"
branch_labels = None
depends_on = None

KARIN_ID = "75af155d-50b2-44f9-a34d-a8ad375ae43d"


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("dispatch_mode", sa.String(32), nullable=True),
    )
    op.execute(
        f"UPDATE accounts SET dispatch_mode = 'continuous' WHERE id = '{KARIN_ID}'"
    )


def downgrade() -> None:
    op.drop_column("accounts", "dispatch_mode")

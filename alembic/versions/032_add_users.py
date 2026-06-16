"""Add users table for multi-user login + attribution.

Revision ID: 032_add_users
Revises: 031_session_ledger
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa

revision = "032_add_users"
down_revision = "031_session_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("handle", name="uq_users_handle"),
    )


def downgrade():
    op.drop_table("users")

"""Add session-health ledger + full-cookie capture fields.

Adds three columns to ``accounts`` for complete cookie capture
(li_rm_cookie, cookies_json, li_at_expires_at) and a new ``session_events``
table that records one row per session "touch" so cookie-expiry causes can be
understood, not just detected.

Revision ID: 031_session_ledger
Revises: 030_add_cll_priority
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "031_session_ledger"
down_revision = "030_add_cll_priority"
branch_labels = None
depends_on = None


def upgrade():
    # Full cookie capture on accounts
    op.add_column("accounts", sa.Column("li_rm_cookie", sa.Text(), nullable=True))
    op.add_column("accounts", sa.Column("cookies_json", sa.Text(), nullable=True))
    op.add_column("accounts", sa.Column("li_at_expires_at", sa.DateTime(), nullable=True))

    # Session-health ledger
    op.create_table(
        "session_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("job", sa.String(32), nullable=False),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("egress_ip", sa.String(64), nullable=True),
        sa.Column("geo", sa.String(128), nullable=True),
        sa.Column("asn", sa.String(128), nullable=True),
        sa.Column("li_at_fp", sa.String(32), nullable=True),
        sa.Column("li_at_expires_at", sa.DateTime(), nullable=True),
        sa.Column("has_li_rm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cookie_names", sa.JSON(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(63), nullable=True),
        sa.Column("viewport", sa.String(32), nullable=True),
        sa.Column("consecutive_session_errors", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_session_events_account_created",
        "session_events",
        ["account_id", "created_at"],
    )
    op.create_index(
        "ix_session_events_account_id",
        "session_events",
        ["account_id"],
    )


def downgrade():
    op.drop_index("ix_session_events_account_id", table_name="session_events")
    op.drop_index("ix_session_events_account_created", table_name="session_events")
    op.drop_table("session_events")
    op.drop_column("accounts", "li_at_expires_at")
    op.drop_column("accounts", "cookies_json")
    op.drop_column("accounts", "li_rm_cookie")

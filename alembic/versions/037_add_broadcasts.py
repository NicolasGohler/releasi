"""Add broadcasts + broadcast_leads tables, daily_message_limit on accounts,
broadcast_id on action_log, direct_messages_sent on daily_stats.

Adds message-only campaigns ("Broadcasts"): sends a message sequence to
1st-degree connections, sharing the daily_message_limit with post-acceptance
follow-ups but not touching the connection daily_limit.

Revision ID: 037_add_broadcasts
Revises: 036_nullable_linkedin_url
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa

revision = "037_add_broadcasts"
down_revision = "036_nullable_linkedin_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── broadcasts ─────────────────────────────────────────────────────────
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("source_list_id", sa.String(36), sa.ForeignKey("lead_lists.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "ACTIVE", "PAUSED", "COMPLETED", name="broadcaststatus"),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("message_1", sa.Text, nullable=True),
        sa.Column("message_2", sa.Text, nullable=True),
        sa.Column("message_3", sa.Text, nullable=True),
        sa.Column("delay_between_hours", sa.Integer, nullable=False, server_default="24"),
        sa.Column("weekend_enabled", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("total_leads", sa.Integer, nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("paused_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_broadcasts_account_id", "broadcasts", ["account_id"])

    # ── broadcast_leads (snapshot) ─────────────────────────────────────────
    op.create_table(
        "broadcast_leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "broadcast_id",
            sa.String(36),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            sa.String(36),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime, nullable=True),
        sa.Column("last_message_sent_at", sa.DateTime, nullable=True),
        sa.Column("last_message_index", sa.Integer, nullable=True),
        sa.Column("next_message_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("broadcast_id", "lead_id", name="uq_broadcast_lead"),
    )
    op.create_index(
        "ix_broadcast_leads_broadcast_status",
        "broadcast_leads",
        ["broadcast_id", "status"],
    )
    op.create_index(
        "ix_broadcast_leads_scheduled_at", "broadcast_leads", ["scheduled_at"]
    )
    op.create_index(
        "ix_broadcast_leads_next_message_at", "broadcast_leads", ["next_message_at"]
    )

    # ── accounts.daily_message_limit ───────────────────────────────────────
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "daily_message_limit",
                sa.Integer,
                nullable=False,
                server_default="15",
            )
        )

    # ── action_log.broadcast_id ────────────────────────────────────────────
    with op.batch_alter_table("action_log") as batch_op:
        batch_op.add_column(
            sa.Column("broadcast_id", sa.String(36), nullable=True)
        )

    # ── daily_stats.direct_messages_sent ───────────────────────────────────
    with op.batch_alter_table("daily_stats") as batch_op:
        batch_op.add_column(
            sa.Column(
                "direct_messages_sent",
                sa.Integer,
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("daily_stats") as batch_op:
        batch_op.drop_column("direct_messages_sent")

    with op.batch_alter_table("action_log") as batch_op:
        batch_op.drop_column("broadcast_id")

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("daily_message_limit")

    op.drop_index("ix_broadcast_leads_next_message_at", table_name="broadcast_leads")
    op.drop_index("ix_broadcast_leads_scheduled_at", table_name="broadcast_leads")
    op.drop_index("ix_broadcast_leads_broadcast_status", table_name="broadcast_leads")
    op.drop_table("broadcast_leads")

    op.drop_index("ix_broadcasts_account_id", table_name="broadcasts")
    op.drop_table("broadcasts")

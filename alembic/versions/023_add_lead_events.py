"""Add lead_events table

Lightweight per-lead event log that doesn't require an account_id.
Used for system events like telegram_found, telegram_contacted, twitter_found.
Shows in the lead activity timeline alongside ActionLog entries.

Revision ID: 023
Revises: 022
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lead_events_lead_created", "lead_events", ["lead_id", "created_at"])


def downgrade() -> None:
    op.drop_table("lead_events")

"""Add paused_at to campaigns + last_catchup_at to accounts.

Revision ID: 018_add_campaign_paused_at
Revises: 017_add_auto_withdraw_schedule
Create Date: 2026-05-12

Semantics:
- campaigns.paused_at: timestamp of the start of an unscanned pause window.
  Set when the campaign is paused (only if currently NULL — preserves the
  earliest unscanned pause across serial pause/resume cycles). Cleared only
  when an acceptance catchup successfully completes for that campaign.
  A non-NULL value on an ACTIVE campaign means "there's a stale pause window
  that was never scanned for catchup acceptances."
- accounts.last_catchup_at: rate-limit guard. Most-recent successful catchup
  run for any campaign on this account. Used to enforce a per-account
  cooldown (default 1h) on catchup invocations.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "018_add_campaign_paused_at"
down_revision = "017_add_auto_withdraw_schedule"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaigns",
        sa.Column("paused_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("last_catchup_at", sa.DateTime(), nullable=True),
    )

    # Backfill: for any campaign currently in PAUSED status, stamp paused_at
    # with the campaign's updated_at as a coarse approximation. This is
    # imprecise (updated_at moves on any edit) but better than leaving NULL,
    # which the dialog treats as "duration unknown."
    op.execute(
        "UPDATE campaigns SET paused_at = updated_at "
        "WHERE status = 'PAUSED' AND paused_at IS NULL"
    )


def downgrade():
    op.drop_column("accounts", "last_catchup_at")
    op.drop_column("campaigns", "paused_at")

"""Phase 1 of the lead-centric model: add new tables alongside the old.

Revision ID: 019_lead_centric_model_phase1
Revises: 018_add_campaign_paused_at
Create Date: 2026-05-12

Background
----------
Today every CSV import creates new Lead rows with `campaign_id IS NULL`
(templates) and assignment-to-a-campaign COPIES those rows with
`campaign_id` set. Result: same LinkedIn URL produces 2+ rows in the leads
table — half of the 10k+ rows on prod are duplicates of this kind.

Phase 1 adds the new schema alongside the old without dropping anything:
- `lead_list_memberships` makes lead↔list a true many-to-many.
- `campaign_lead_assignments` makes lead↔campaign a true many-to-many and
  is where per-campaign state lives (status, scheduled_at, the three
  timestamps, error_message, retry_count). One row per (lead, campaign).

The old `leads` table is untouched in this migration. Backfill happens in
a separate Python script (`scripts/backfill_lead_centric_model.py`) where
we can log every collapse decision and roll back via the
`pre_option_c_migration.db` snapshot if anything looks off.

Phase 2 will cut reads over to the new tables one path at a time. Phase 3
will drop the now-deprecated columns from `leads`.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "019_lead_centric_model_phase1"
down_revision = "018_add_campaign_paused_at"
branch_labels = None
depends_on = None


def upgrade():
    # ── lead_list_memberships ───────────────────────────────────────────
    # Many-to-many between leads and lead_lists. Replaces Lead.lead_list_id
    # in Phase 3. UNIQUE(lead_id, lead_list_id) ensures a lead can be in
    # the same list only once (re-imports become idempotent).
    op.create_table(
        "lead_list_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "lead_id", sa.String(36),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_list_id", sa.String(36),
            sa.ForeignKey("lead_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "lead_id", "lead_list_id",
            name="uq_lead_list_membership",
        ),
    )
    op.create_index(
        "ix_lead_list_memberships_lead",
        "lead_list_memberships", ["lead_id"],
    )
    op.create_index(
        "ix_lead_list_memberships_list",
        "lead_list_memberships", ["lead_list_id"],
    )

    # ── campaign_lead_assignments ───────────────────────────────────────
    # Many-to-many between leads and campaigns. Per-campaign state lives
    # HERE, not on the Lead row. UNIQUE(lead_id, campaign_id) prevents
    # duplicates; you assign each lead to a campaign at most once.
    #
    # `lead_list_id` is denormalised here as a "which list brought this
    # lead into this campaign" pointer — useful for unassign-list-from-
    # campaign semantics and for the dashboard's lead-detail view.
    op.create_table(
        "campaign_lead_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "lead_id", sa.String(36),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id", sa.String(36),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_list_id", sa.String(36),
            sa.ForeignKey("lead_lists.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Status mirrors the existing LeadStatus enum values. Stored as
        # plain string here (SQLite handles enums as VARCHAR anyway) so
        # we don't have to coordinate enum changes across two columns.
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("connection_requested_at", sa.DateTime(), nullable=True),
        sa.Column("connection_accepted_at", sa.DateTime(), nullable=True),
        sa.Column("followup_sent_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "lead_id", "campaign_id",
            name="uq_campaign_lead_assignment",
        ),
    )
    op.create_index(
        "ix_campaign_lead_assignments_campaign_status",
        "campaign_lead_assignments", ["campaign_id", "status"],
    )
    op.create_index(
        "ix_campaign_lead_assignments_lead",
        "campaign_lead_assignments", ["lead_id"],
    )
    op.create_index(
        "ix_campaign_lead_assignments_scheduled_at",
        "campaign_lead_assignments", ["scheduled_at"],
    )


def downgrade():
    op.drop_index("ix_campaign_lead_assignments_scheduled_at",
                  table_name="campaign_lead_assignments")
    op.drop_index("ix_campaign_lead_assignments_lead",
                  table_name="campaign_lead_assignments")
    op.drop_index("ix_campaign_lead_assignments_campaign_status",
                  table_name="campaign_lead_assignments")
    op.drop_table("campaign_lead_assignments")
    op.drop_index("ix_lead_list_memberships_list",
                  table_name="lead_list_memberships")
    op.drop_index("ix_lead_list_memberships_lead",
                  table_name="lead_list_memberships")
    op.drop_table("lead_list_memberships")

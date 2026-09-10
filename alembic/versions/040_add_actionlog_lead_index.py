"""Add index on action_log(lead_id, created_at) for last-activity lookups.

Revision ID: 040_add_actionlog_lead_index
Revises: 039_broadcast_prior_sequence_and_delay
Create Date: 2026-09-10
"""
from alembic import op

revision = "040_add_actionlog_lead_index"
down_revision = "039_broadcast_prior_sequence_and_delay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_actionlog_lead_created", "action_log", ["lead_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_actionlog_lead_created", table_name="action_log")

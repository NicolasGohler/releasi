"""Add index on action_log(lead_id, created_at) for last-activity lookups.

Revision ID: 041_add_actionlog_lead_index
Revises: 040_broadcast_trial_sends_limit
Create Date: 2026-09-10
"""
from alembic import op

revision = "041_add_actionlog_lead_index"
down_revision = "040_broadcast_trial_sends_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_actionlog_lead_created", "action_log", ["lead_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_actionlog_lead_created", table_name="action_log")

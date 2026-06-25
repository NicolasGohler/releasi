"""Add actor_user_id to lead_events and lead_notes for manual-action attribution.

Stamps the dashboard user who performed a human-driven action (Telegram
outreach, handle changes, notes). NULL = automated/system (TG sweep,
scheduler) or legacy pre-attribution rows.

Revision ID: 034_add_actor_user_id
Revises: 033_add_apollo_id
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "034_add_actor_user_id"
down_revision = "033_add_apollo_id"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("lead_events", sa.Column("actor_user_id", sa.String(36), nullable=True))
    op.create_index("ix_lead_events_actor_user_id", "lead_events", ["actor_user_id"])
    op.add_column("lead_notes", sa.Column("actor_user_id", sa.String(36), nullable=True))
    op.create_index("ix_lead_notes_actor_user_id", "lead_notes", ["actor_user_id"])


def downgrade():
    op.drop_index("ix_lead_notes_actor_user_id", table_name="lead_notes")
    op.drop_column("lead_notes", "actor_user_id")
    op.drop_index("ix_lead_events_actor_user_id", table_name="lead_events")
    op.drop_column("lead_events", "actor_user_id")

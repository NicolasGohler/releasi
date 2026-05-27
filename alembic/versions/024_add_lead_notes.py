"""Add notes column to leads table.

Revision ID: 024_add_lead_notes
Revises: 023_add_lead_events
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "024_add_lead_notes"
down_revision = "023_add_lead_events"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("leads", sa.Column("notes", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("leads", "notes")

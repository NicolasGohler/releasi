"""Add location column to leads table.

Revision ID: 025_add_lead_location
Revises: 024_add_lead_notes
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "025_add_lead_location"
down_revision = "024_add_lead_notes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("leads", sa.Column("location", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("leads", "location")

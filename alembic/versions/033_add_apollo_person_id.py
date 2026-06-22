"""Add apollo_person_id to leads for cross-run Apollo credit caching.

Lets any script (fundraising agent, manual enrichment) check the leads
table for a person already enriched by Apollo before paying credits again.

Revision ID: 033_add_apollo_id
Revises: 032_add_users
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = "033_add_apollo_id"
down_revision = "032_add_users"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("leads", sa.Column("apollo_person_id", sa.String(100), nullable=True))
    op.create_index("ix_leads_apollo_person_id", "leads", ["apollo_person_id"])


def downgrade():
    op.drop_index("ix_leads_apollo_person_id", table_name="leads")
    op.drop_column("leads", "apollo_person_id")

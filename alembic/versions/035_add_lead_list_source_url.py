"""Add source_url and scrape_account_id to lead_lists

Revision ID: 035_add_lead_list_source_url
Revises: 034_add_actor_user_id
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa

revision = "035_add_lead_list_source_url"
down_revision = "034_add_actor_user_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_lists", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("lead_lists", sa.Column("scrape_account_id", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_lists", "scrape_account_id")
    op.drop_column("lead_lists", "source_url")

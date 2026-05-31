"""Add scraper_cookies table.

Revision ID: 026_add_scraper_cookies
Revises: 025_add_lead_location
Create Date: 2026-05-31
"""
from alembic import op
import sqlalchemy as sa

revision = "026_add_scraper_cookies"
down_revision = "025_add_lead_location"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scraper_cookies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site", sa.String(64), nullable=False),
        sa.Column("cookies_json", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site"),
    )


def downgrade():
    op.drop_table("scraper_cookies")

"""Add lead lists, campaign_lead_lists junction, lead_list_id on leads, REMOVED status.

Revision ID: 004_lead_lists
Revises: 003_campaign_filters
Create Date: 2026-02-28
"""
from alembic import op
import sqlalchemy as sa

revision = "004_lead_lists"
down_revision = "003_campaign_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create lead_lists table
    op.create_table(
        "lead_lists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("csv_filename", sa.String(255), nullable=True),
        sa.Column("total_leads", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # 2. Create campaign_lead_lists junction table
    op.create_table(
        "campaign_lead_lists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(36),
            sa.ForeignKey("campaigns.id"),
            nullable=False,
        ),
        sa.Column(
            "lead_list_id",
            sa.String(36),
            sa.ForeignKey("lead_lists.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "lead_list_id", name="uq_campaign_lead_list"),
    )
    op.create_index(
        "ix_campaign_lead_lists_campaign_id",
        "campaign_lead_lists",
        ["campaign_id"],
    )
    op.create_index(
        "ix_campaign_lead_lists_lead_list_id",
        "campaign_lead_lists",
        ["lead_list_id"],
    )

    # 3. Add lead_list_id column to leads (nullable — existing leads don't have one)
    op.add_column(
        "leads",
        sa.Column(
            "lead_list_id",
            sa.String(36),
            sa.ForeignKey("lead_lists.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_leads_lead_list_id", "leads", ["lead_list_id"])

    # 4. SQLite doesn't support ALTER ENUM, but since we use VARCHAR-backed enums
    #    the new 'removed' value is just a string — no DDL needed for SQLite.
    #    The ORM enum validation handles it.


def downgrade() -> None:
    op.drop_index("ix_leads_lead_list_id", table_name="leads")
    op.drop_column("leads", "lead_list_id")
    op.drop_index("ix_campaign_lead_lists_lead_list_id", table_name="campaign_lead_lists")
    op.drop_index("ix_campaign_lead_lists_campaign_id", table_name="campaign_lead_lists")
    op.drop_table("campaign_lead_lists")
    op.drop_table("lead_lists")

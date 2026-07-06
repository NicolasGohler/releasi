"""Make leads.linkedin_url nullable to support non-LinkedIn leads

Revision ID: 036_nullable_linkedin_url
Revises: 035_add_lead_list_source_url
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa

revision = "036_nullable_linkedin_url"
down_revision = "035_add_lead_list_source_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads", recreate="always") as batch_op:
        batch_op.alter_column(
            "linkedin_url",
            existing_type=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("leads", recreate="always") as batch_op:
        batch_op.alter_column(
            "linkedin_url",
            existing_type=sa.Text(),
            nullable=False,
        )

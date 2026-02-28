"""Add followup_enabled and followup_message_1/2/3 to campaigns.

Revision ID: 005_followup_messages
Revises: 004_lead_lists
Create Date: 2026-02-28
"""
from alembic import op
import sqlalchemy as sa

revision = "005_followup_messages"
down_revision = "004_lead_lists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.add_column(
            sa.Column("followup_enabled", sa.Boolean(), server_default="0", nullable=False),
        )
        batch_op.add_column(
            sa.Column("followup_message_1", sa.Text(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("followup_message_2", sa.Text(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("followup_message_3", sa.Text(), nullable=True),
        )

    # Migrate existing followup_message_template data into followup_message_1
    op.execute(
        "UPDATE campaigns SET followup_message_1 = followup_message_template "
        "WHERE followup_message_template IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.drop_column("followup_message_3")
        batch_op.drop_column("followup_message_2")
        batch_op.drop_column("followup_message_1")
        batch_op.drop_column("followup_enabled")

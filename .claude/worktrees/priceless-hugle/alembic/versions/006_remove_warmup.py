"""Remove warmup columns from accounts.

Revision ID: 006_remove_warmup
Revises: 005_followup_messages
Create Date: 2026-02-28
"""
from alembic import op
import sqlalchemy as sa

revision = "006_remove_warmup"
down_revision = "005_followup_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("warmup_enabled")
        batch_op.drop_column("warmup_start_date")
        batch_op.drop_column("warmup_week")


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(
            sa.Column("warmup_enabled", sa.Boolean(), server_default="0", nullable=False),
        )
        batch_op.add_column(
            sa.Column("warmup_start_date", sa.Date(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("warmup_week", sa.Integer(), server_default="0", nullable=False),
        )

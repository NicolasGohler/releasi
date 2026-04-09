"""Add withdraw_threshold and avatar_path to accounts.

Revision ID: 008_withdraw_avatar
Revises: 007_campaign_weekend
Create Date: 2026-03-02
"""
from alembic import op
import sqlalchemy as sa

revision = "008_withdraw_avatar"
down_revision = "007_campaign_weekend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(
            sa.Column("withdraw_threshold", sa.Integer(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("avatar_path", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("avatar_path")
        batch_op.drop_column("withdraw_threshold")

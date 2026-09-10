"""Add message_prior_only_2/3, is_prior_branch, and change delay to Float.

Revision ID: 039_broadcast_prior_sequence_and_delay
Revises: 038_broadcast_conversation_routing
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

revision = "039_broadcast_prior_sequence_and_delay"
down_revision = "038_broadcast_conversation_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("broadcasts") as batch:
        batch.add_column(sa.Column("message_prior_only_2", sa.Text(), nullable=True))
        batch.add_column(sa.Column("message_prior_only_3", sa.Text(), nullable=True))
        # SQLite stores the value as-is regardless of declared affinity; altering
        # the column type here is mainly for documentation.  Existing integer
        # values (e.g. 24) remain valid — they're interpreted as hours.
        batch.alter_column(
            "delay_between_hours",
            type_=sa.Float(),
            existing_type=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table("broadcast_leads") as batch:
        batch.add_column(
            sa.Column("is_prior_branch", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("broadcast_leads") as batch:
        batch.drop_column("is_prior_branch")

    with op.batch_alter_table("broadcasts") as batch:
        batch.drop_column("message_prior_only_3")
        batch.drop_column("message_prior_only_2")
        batch.alter_column(
            "delay_between_hours",
            type_=sa.Integer(),
            existing_type=sa.Float(),
            existing_nullable=False,
        )

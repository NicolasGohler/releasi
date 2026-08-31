"""Add conversation_routing + message_prior_only to broadcasts.

conversation_routing: "skip" (default, existing behaviour) or "branch"
  (send different message based on LinkedIn conversation history).
message_prior_only: optional message text used when routing="branch" and
  the lead was already messaged by us but has not replied.

Revision ID: 038_broadcast_conversation_routing
Revises: 037_add_broadcasts
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "038_broadcast_conversation_routing"
down_revision = "037_add_broadcasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("broadcasts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "conversation_routing",
                sa.String(16),
                nullable=False,
                server_default="skip",
            )
        )
        batch_op.add_column(
            sa.Column("message_prior_only", sa.Text, nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("broadcasts") as batch_op:
        batch_op.drop_column("message_prior_only")
        batch_op.drop_column("conversation_routing")

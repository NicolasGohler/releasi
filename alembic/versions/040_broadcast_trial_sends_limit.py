"""Add broadcasts.trial_sends_limit.

Retroactive migration file: this column was added directly to the live DB
via manual SQL in an earlier session (see the "Database Migrations" section
of CLAUDE.md for that workflow) and the DB was stamped with this revision
id at the time, but the migration file itself was never committed. Adding
it now so `alembic upgrade head` reflects what's actually live instead of
pointing at an unknown revision.

Revision ID: 040_broadcast_trial_sends_limit
Revises: 039_broadcast_prior_sequence_and_delay
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

revision = "040_broadcast_trial_sends_limit"
down_revision = "039_broadcast_prior_sequence_and_delay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("broadcasts") as batch:
        batch.add_column(sa.Column("trial_sends_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("broadcasts") as batch:
        batch.drop_column("trial_sends_limit")

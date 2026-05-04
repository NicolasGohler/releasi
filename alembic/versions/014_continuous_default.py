"""Set continuous dispatch mode as default for all accounts.

Revision ID: 014_continuous_default
Revises: 013_add_dispatch_mode
Create Date: 2026-05-04

Makes continuous the default dispatch mode. Accounts with dispatch_mode IS NULL
are migrated to 'continuous'. Only an explicit 'planned' value opts into the
legacy pre-scheduled slot behavior.
"""
from alembic import op

revision = "014_continuous_default"
down_revision = "013_add_dispatch_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Promote all accounts that have no explicit mode to continuous.
    # Accounts already on 'continuous' (e.g. Karin) are unaffected (WHERE clause filters them out via IS NULL).
    # Accounts with dispatch_mode = 'planned' keep their explicit opt-in.
    op.execute(
        "UPDATE accounts SET dispatch_mode = 'continuous' WHERE dispatch_mode IS NULL"
    )


def downgrade() -> None:
    # Revert: set back to NULL for accounts on 'continuous'
    # (NULL was the original "unset / use planned mode" state before this migration).
    op.execute(
        "UPDATE accounts SET dispatch_mode = NULL WHERE dispatch_mode = 'continuous'"
    )

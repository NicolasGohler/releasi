"""Add email and phone columns to leads; backfill from extra_data.

Revision ID: 015_add_lead_email_phone
Revises: 014_continuous_default
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import json

revision = "015_add_lead_email_phone"
down_revision = "014_continuous_default"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("leads", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("leads", sa.Column("phone", sa.String(100), nullable=True))

    # Backfill: move email / phone out of extra_data into the new columns
    # for any leads that were imported from CSVs that had those fields.
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, extra_data FROM leads WHERE extra_data IS NOT NULL"
    )).fetchall()

    for row_id, extra_raw in rows:
        if not extra_raw:
            continue
        try:
            extra = json.loads(extra_raw) if isinstance(extra_raw, str) else extra_raw
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(extra, dict):
            continue

        email = None
        phone = None
        changed = False

        # Collect matching keys (case-insensitive)
        for k in list(extra.keys()):
            kl = k.lower().strip()
            if kl in ("email", "e-mail", "email_address", "emailaddress") and not email:
                email = extra.pop(k)
                changed = True
            elif kl in ("phone", "phone_number", "phonenumber", "mobile", "tel", "telephone") and not phone:
                phone = extra.pop(k)
                changed = True

        if not changed:
            continue

        new_extra = json.dumps(extra) if extra else None
        conn.execute(
            sa.text(
                "UPDATE leads SET email = :email, phone = :phone, extra_data = :extra "
                "WHERE id = :id"
            ),
            {"email": email, "phone": phone, "extra": new_extra, "id": row_id},
        )


def downgrade():
    op.drop_column("leads", "phone")
    op.drop_column("leads", "email")

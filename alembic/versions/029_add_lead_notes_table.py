"""Add lead_notes table (HubSpot-style multi-note per lead).

Migrates any existing single-column ``leads.notes`` value into the new
``lead_notes`` table so legacy notes remain visible.

Revision ID: 029_add_lead_notes_table
Revises: 026_add_scraper_cookies
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

revision = "029_add_lead_notes_table"
down_revision = "026_add_scraper_cookies"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lead_notes_lead_created", "lead_notes", ["lead_id", "created_at"]
    )
    op.create_index(
        "ix_lead_notes_lead_id", "lead_notes", ["lead_id"]
    )

    # Backfill: copy existing single-column notes into the new table.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, notes, updated_at, created_at FROM leads "
            "WHERE notes IS NOT NULL AND TRIM(notes) != ''"
        )
    ).fetchall()
    import uuid as _uuid

    for lead_id, notes, updated_at, created_at in rows:
        ts = updated_at or created_at
        conn.execute(
            sa.text(
                "INSERT INTO lead_notes (id, lead_id, body, created_at, updated_at) "
                "VALUES (:id, :lead_id, :body, :created_at, :updated_at)"
            ),
            {
                "id": str(_uuid.uuid4()),
                "lead_id": lead_id,
                "body": notes,
                "created_at": ts,
                "updated_at": ts,
            },
        )


def downgrade():
    op.drop_index("ix_lead_notes_lead_id", table_name="lead_notes")
    op.drop_index("ix_lead_notes_lead_created", table_name="lead_notes")
    op.drop_table("lead_notes")

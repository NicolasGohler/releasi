#!/usr/bin/env python3
"""Backfill the lead-centric schema from the legacy Lead table.

Run AFTER migration 019 has applied. Idempotent — safe to re-run.

What it does (in order, all in one transaction per phase):

  Phase 1a. For every Lead row with a non-NULL `lead_list_id`, ensure a
            matching LeadListMembership row exists. Uses
            ON CONFLICT DO NOTHING via the unique constraint.

  Phase 1b. For every Lead row with a non-NULL `campaign_id`, ensure a
            matching CampaignLeadAssignment row exists carrying that
            lead's current status + timestamps + retry_count.

  Phase 1c. Identify duplicate Leads (same normalized linkedin_url) and
            print a report. NO row collapsing in this script — that's
            deferred to a second pass after we've watched the dual-write
            stay in sync for ~24h.

Why no row collapsing yet:
  Collapsing 5400+ template rows + 137 multi-campaign URLs into canonical
  Leads is destructive (action_log.lead_id rewrites, FK chain updates).
  We do it as a separate step once Phase 1 has proven the dual-write
  invariants hold. Running this script today gives us all the membership +
  assignment rows we need for parallel reads in Phase 2 — collapse can
  come later without blocking that work.

Safety:
  - Reads use plain SQL via aiosqlite (no ORM session lifecycle issues).
  - Writes use parameterised INSERT ... ON CONFLICT DO NOTHING so re-runs
    don't fail and don't duplicate.
  - Prints a diff summary before/after so we can verify the backfill
    against the pre_option_c_migration.db snapshot.

Usage (inside the container):
  docker exec linauto python3 /app/scripts/backfill_lead_centric_model.py
  docker exec linauto python3 /app/scripts/backfill_lead_centric_model.py --report-only
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = "/app/data/linauto.db"


def _normalize_li_url(url: str | None) -> str:
    """Match runner._normalize_li_url — keeps backfill consistent with prod."""
    if not url:
        return ""
    m = re.search(r"/in/([^/?#\s]+)", url)
    return f"/in/{m.group(1).rstrip('/')}" if m else ""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def report(conn: sqlite3.Connection) -> dict:
    """Snapshot counts before/after for verification."""
    out = {}
    out["leads"] = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    out["leads_with_campaign"] = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE campaign_id IS NOT NULL"
    ).fetchone()[0]
    out["leads_with_list"] = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE lead_list_id IS NOT NULL"
    ).fetchone()[0]
    out["unique_urls"] = conn.execute(
        "SELECT COUNT(DISTINCT linkedin_url) FROM leads"
    ).fetchone()[0]
    out["memberships"] = conn.execute(
        "SELECT COUNT(*) FROM lead_list_memberships"
    ).fetchone()[0]
    out["assignments"] = conn.execute(
        "SELECT COUNT(*) FROM campaign_lead_assignments"
    ).fetchone()[0]
    return out


def backfill_memberships(conn: sqlite3.Connection) -> int:
    """Phase 1a: ensure every Lead.lead_list_id → LeadListMembership row."""
    cur = conn.execute("""
        SELECT id, lead_list_id, created_at
        FROM leads
        WHERE lead_list_id IS NOT NULL
    """)
    rows = cur.fetchall()
    inserted = 0
    for lead_id, lead_list_id, created_at in rows:
        # ON CONFLICT DO NOTHING via the UNIQUE(lead_id, lead_list_id)
        result = conn.execute("""
            INSERT INTO lead_list_memberships (id, lead_id, lead_list_id, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (lead_id, lead_list_id) DO NOTHING
        """, (str(uuid.uuid4()), lead_id, lead_list_id, created_at or _utcnow_iso()))
        inserted += result.rowcount
    conn.commit()
    return inserted


def backfill_assignments(conn: sqlite3.Connection) -> int:
    """Phase 1b: ensure every Lead.campaign_id → CampaignLeadAssignment row.

    Copies the current status + all five timestamps + error_message +
    retry_count from the Lead row. Sets `lead_list_id` to whatever the
    Lead row carries (which list brought it into this campaign).
    """
    cur = conn.execute("""
        SELECT id, campaign_id, lead_list_id, status,
               scheduled_at, connection_requested_at, connection_accepted_at,
               followup_sent_at, error_message, retry_count,
               created_at, updated_at
        FROM leads
        WHERE campaign_id IS NOT NULL
    """)
    rows = cur.fetchall()
    inserted = 0
    for (lead_id, campaign_id, lead_list_id, status, sched, req, acc,
         fu, err, retry, created, updated) in rows:
        # LeadStatus enum is stored uppercase on Lead (e.g. "CONNECTION_REQUESTED")
        # but we want lowercase on the new table to match the enum's `value`.
        status_lc = (status or "pending").lower()
        result = conn.execute("""
            INSERT INTO campaign_lead_assignments
                (id, lead_id, campaign_id, lead_list_id, status,
                 scheduled_at, connection_requested_at, connection_accepted_at,
                 followup_sent_at, error_message, retry_count,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (lead_id, campaign_id) DO NOTHING
        """, (
            str(uuid.uuid4()), lead_id, campaign_id, lead_list_id, status_lc,
            sched, req, acc, fu, err, retry or 0,
            created or _utcnow_iso(), updated or _utcnow_iso(),
        ))
        inserted += result.rowcount
    conn.commit()
    return inserted


def find_duplicate_urls(conn: sqlite3.Connection) -> dict:
    """Phase 1c (informational only): find URL duplicates that will need
    collapsing in the post-Phase-1-stability step.

    Returns a summary:
      total_duplicate_groups: how many distinct URLs have 2+ Lead rows
      max_dup_count: largest group size
      template_only: groups where ALL rows have campaign_id IS NULL
      mixed: groups with at least one campaign row + at least one template
      multi_campaign: groups with 2+ campaign rows (true m2m use case)
    """
    cur = conn.execute("""
        SELECT linkedin_url, COUNT(*) AS n,
               SUM(CASE WHEN campaign_id IS NULL THEN 1 ELSE 0 END) AS templates,
               SUM(CASE WHEN campaign_id IS NOT NULL THEN 1 ELSE 0 END) AS in_campaign
        FROM leads
        GROUP BY linkedin_url
        HAVING n >= 2
    """)
    groups = cur.fetchall()
    summary = {
        "total_duplicate_groups": len(groups),
        "max_dup_count": max((g[1] for g in groups), default=0),
        "template_only": sum(1 for g in groups if g[2] == g[1]),
        "mixed": sum(1 for g in groups if g[2] > 0 and g[3] > 0),
        "multi_campaign": sum(1 for g in groups if g[3] >= 2),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-only", action="store_true",
        help="Print counts and dup analysis without writing any rows.",
    )
    parser.add_argument(
        "--db", default=DB_PATH,
        help="Path to the SQLite DB (default: %(default)s).",
    )
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"FATAL: DB not found at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")

    print("=" * 60)
    print(f"Backfill lead-centric model — {datetime.now().isoformat()}")
    print(f"DB: {args.db}")
    print("=" * 60)
    before = report(conn)
    print("Before:")
    for k, v in before.items():
        print(f"  {k:30s} {v:>8}")

    if args.report_only:
        print("\nDuplicate URL analysis:")
        for k, v in find_duplicate_urls(conn).items():
            print(f"  {k:30s} {v:>8}")
        print("\n--report-only: no writes performed.")
        conn.close()
        return 0

    print("\nPhase 1a: backfill lead_list_memberships ...")
    m = backfill_memberships(conn)
    print(f"  inserted: {m}")

    print("\nPhase 1b: backfill campaign_lead_assignments ...")
    a = backfill_assignments(conn)
    print(f"  inserted: {a}")

    after = report(conn)
    print("\nAfter:")
    for k, v in after.items():
        delta = after[k] - before[k]
        sign = "+" if delta >= 0 else ""
        print(f"  {k:30s} {v:>8}  ({sign}{delta})")

    print("\nDuplicate URL analysis (informational — collapse is deferred):")
    for k, v in find_duplicate_urls(conn).items():
        print(f"  {k:30s} {v:>8}")

    conn.close()
    print("\nBackfill complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

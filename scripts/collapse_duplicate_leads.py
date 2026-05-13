"""Phase 3a: Collapse duplicate Lead rows into canonical single-URL rows.

Background
----------
The legacy assign_list_to_campaign() cloned a Lead row for every campaign a
list was assigned to. The result: up to 6 Lead rows per LinkedIn URL, one per
campaign. Phase 1 dual-write + Phase 2 read cutover moved all state into
CampaignLeadAssignment / LeadListMembership. This script completes the
migration by merging every URL group into one canonical Lead row.

Algorithm (per URL group with > 1 row)
---------------------------------------
1. Pick canonical
   - Prefer the campaign_id=NULL (template) copy — it has a clean profile and,
     critically, NO campaign_lead_assignments pointing to it yet, so re-pointing
     is always conflict-free.
   - If no NULL-campaign copy exists: use the oldest row (min created_at).

2. Merge profile fields into canonical
   For each of (first_name, last_name, company, title, email, phone, extra_data):
   prefer the most-recently-updated non-null value across all copies.

3. Re-point campaign_lead_assignments → canonical.id
   (UPDATE ... SET lead_id = canonical where lead_id = non-canonical)
   This can't conflict on UNIQUE(lead_id, campaign_id) because canonical has
   no assignments yet. Verified on prod: 0 NULL-campaign leads have assignments.

4. Re-point lead_list_memberships → canonical.id
   Uses UPDATE OR IGNORE so existing (canonical.id, list_id) rows win and the
   duplicate non-canonical membership is simply skipped. Any non-canonical
   membership rows not successfully updated (because canonical already owns them)
   are deleted after.

5. Delete non-canonical Lead rows.

Run with --dry-run to see counts without touching the DB.
Run without flags to execute (wraps in a single transaction; rolls back on error).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

DB_PATH = "/app/data/linauto.db"
PROFILE_FIELDS = ["first_name", "last_name", "company", "title", "email", "phone", "extra_data"]


def collapse(db_path: str, dry_run: bool) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        # ── Gather all leads grouped by linkedin_url ───────────────────────
        rows = conn.execute(
            "SELECT id, linkedin_url, campaign_id, first_name, last_name, "
            "company, title, email, phone, extra_data, created_at, updated_at "
            "FROM leads ORDER BY created_at ASC"
        ).fetchall()

        groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            groups[row["linkedin_url"]].append(row)

        dup_groups = {url: copies for url, copies in groups.items() if len(copies) > 1}
        total_dup_leads = sum(len(v) for v in dup_groups.values())

        print(f"Total lead rows:         {len(rows)}")
        print(f"Unique URLs:             {len(groups)}")
        print(f"Duplicate URL groups:    {len(dup_groups)}")
        print(f"Leads in dup groups:     {total_dup_leads}")
        print(f"Rows to delete:          {total_dup_leads - len(dup_groups)}")
        print(f"Mode:                    {'DRY RUN' if dry_run else 'EXECUTE'}")
        print()

        if dry_run:
            # Sample a few groups to show what would happen
            sample = list(dup_groups.items())[:5]
            print("=== Sample groups ===")
            for url, copies in sample:
                canonical = _pick_canonical(copies)
                non_canonicals = [c for c in copies if c["id"] != canonical["id"]]
                print(f"  URL: ...{url[-40:]}")
                print(f"    canonical: id={canonical['id']} campaign_id={canonical['campaign_id']}")
                for nc in non_canonicals:
                    print(f"    delete:    id={nc['id']}  campaign_id={nc['campaign_id']}")
            print()
            print("No changes made (dry run). Re-run without --dry-run to execute.")
            return

        # ── Execute collapse in a single transaction ───────────────────────
        deleted = 0
        assignments_repointed = 0
        memberships_repointed = 0
        memberships_deduped = 0
        field_merges = 0

        with conn:  # transaction — commits on exit, rolls back on exception
            for url, copies in dup_groups.items():
                canonical = _pick_canonical(copies)
                canonical_id = canonical["id"]
                non_canonicals = [c for c in copies if c["id"] != canonical_id]

                # ── Merge profile fields into canonical ────────────────────
                merged = _merge_profile_fields(canonical, non_canonicals)
                if merged:
                    set_clause = ", ".join(f"{f} = ?" for f in merged)
                    values = [merged[f] for f in merged] + [canonical_id]
                    conn.execute(f"UPDATE leads SET {set_clause} WHERE id = ?", values)
                    field_merges += 1

                for nc in non_canonicals:
                    nc_id = nc["id"]

                    # ── Re-point assignments ───────────────────────────────
                    cur = conn.execute(
                        "UPDATE campaign_lead_assignments SET lead_id = ? WHERE lead_id = ?",
                        (canonical_id, nc_id),
                    )
                    assignments_repointed += cur.rowcount

                    # ── Re-point memberships (UPDATE OR IGNORE skips conflicts) ──
                    cur = conn.execute(
                        "UPDATE OR IGNORE lead_list_memberships SET lead_id = ? WHERE lead_id = ?",
                        (canonical_id, nc_id),
                    )
                    memberships_repointed += cur.rowcount

                    # ── Delete stranded memberships for this non-canonical ──
                    # (rows that UPDATE OR IGNORE skipped because canonical already owns them)
                    cur = conn.execute(
                        "DELETE FROM lead_list_memberships WHERE lead_id = ?",
                        (nc_id,),
                    )
                    memberships_deduped += cur.rowcount

                    # ── Delete non-canonical lead row ──────────────────────
                    conn.execute("DELETE FROM leads WHERE id = ?", (nc_id,))
                    deleted += 1

        # ── Sync leads.status from assignment (canonical may have been a
        # template with status=PENDING whose campaign copy had a later
        # status — fix the legacy column to match the most-recently-updated
        # assignment so the stale-status guard in the followup dispatcher
        # doesn't skip leads unnecessarily). ────────────────────────────
        sync_cur = conn.execute("""
UPDATE leads
SET status = lower((
    SELECT a.status FROM campaign_lead_assignments a
    WHERE a.lead_id = leads.id
    ORDER BY coalesce(a.updated_at, a.created_at) DESC
    LIMIT 1
))
WHERE id IN (
    SELECT DISTINCT l.id FROM leads l
    JOIN campaign_lead_assignments a ON a.lead_id = l.id
    WHERE lower(a.status) != lower(l.status)
)
""")
        status_synced = sync_cur.rowcount

        print(f"=== Collapse complete ===")
        print(f"  leads.status synced from assignment: {status_synced}")
        print(f"  Non-canonical rows deleted:       {deleted}")
        print(f"  Assignments re-pointed:           {assignments_repointed}")
        print(f"  Memberships re-pointed:           {memberships_repointed}")
        print(f"  Membership duplicates removed:    {memberships_deduped}")
        print(f"  Profile field merges performed:   {field_merges}")
        print()

        # ── Verification ──────────────────────────────────────────────────
        remaining = conn.execute("SELECT count(*) FROM leads").fetchone()[0]
        unique_urls = conn.execute("SELECT count(DISTINCT linkedin_url) FROM leads").fetchone()[0]
        dup_remaining = conn.execute(
            "SELECT count(*) FROM (SELECT linkedin_url FROM leads GROUP BY linkedin_url HAVING count(*) > 1)"
        ).fetchone()[0]
        orphan_assignments = conn.execute(
            "SELECT count(*) FROM campaign_lead_assignments a "
            "LEFT JOIN leads l ON l.id = a.lead_id WHERE l.id IS NULL"
        ).fetchone()[0]
        orphan_memberships = conn.execute(
            "SELECT count(*) FROM lead_list_memberships m "
            "LEFT JOIN leads l ON l.id = m.lead_id WHERE l.id IS NULL"
        ).fetchone()[0]
        status_mismatches = conn.execute(
            "SELECT count(*) FROM leads l "
            "JOIN campaign_lead_assignments a ON a.lead_id = l.id AND a.campaign_id = l.campaign_id "
            "WHERE lower(l.status) != lower(a.status)"
        ).fetchone()[0]

        print(f"=== Verification ===")
        print(f"  Remaining lead rows:              {remaining}")
        print(f"  Unique URLs:                      {unique_urls}")
        print(f"  Duplicate URL groups remaining:   {dup_remaining}  (should be 0)")
        print(f"  Orphaned assignments:             {orphan_assignments}  (should be 0)")
        print(f"  Orphaned memberships:             {orphan_memberships}  (should be 0)")
        print(f"  Status mismatches (case-insens.): {status_mismatches}  (should be 0)")

        if dup_remaining > 0 or orphan_assignments > 0 or orphan_memberships > 0:
            print("\nWARNING: verification failed — check output above.")
            sys.exit(1)
        else:
            print("\nAll checks passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def _pick_canonical(copies: list[sqlite3.Row]) -> sqlite3.Row:
    """Pick the canonical lead from a group sharing the same linkedin_url.

    Priority:
    1. campaign_id IS NULL (template / library copy)
       - If multiple NULL-campaign copies, pick the oldest (min created_at).
    2. If no NULL-campaign copy: oldest by created_at.
    """
    null_campaign = [c for c in copies if c["campaign_id"] is None]
    pool = null_campaign if null_campaign else copies
    return min(pool, key=lambda r: r["created_at"] or "")


def _merge_profile_fields(
    canonical: sqlite3.Row,
    non_canonicals: list[sqlite3.Row],
) -> dict[str, object]:
    """Merge profile fields from non-canonical copies into canonical.

    For each field: prefer the most-recently-updated non-null value.
    Only includes fields that actually need updating on canonical.
    """
    all_copies = [canonical] + non_canonicals
    updates: dict[str, object] = {}

    for field in PROFILE_FIELDS:
        # Collect (updated_at, value) pairs where value is not null/empty
        candidates = [
            (c["updated_at"] or c["created_at"] or "", c[field])
            for c in all_copies
            if c[field] is not None and c[field] != ""
        ]
        if not candidates:
            continue
        # Most recently updated wins
        best_value = max(candidates, key=lambda x: x[0])[1]
        if best_value != canonical[field]:
            updates[field] = best_value

    return updates


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collapse duplicate Lead rows.")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without making changes.")
    parser.add_argument("--db", default=DB_PATH, help=f"DB path (default: {DB_PATH})")
    args = parser.parse_args()

    collapse(db_path=args.db, dry_run=args.dry_run)

"""Fill missing lead metadata from original CSVs; dry-run unless --apply is set.

Run inside the container with the tested importer on PYTHONPATH. This never
changes URLs, memberships, campaign configuration, or delivery state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from releasi.campaign.importer import normalize_linkedin_url, parse_csv

FIELDS = (
    "first_name", "last_name", "company", "title", "email", "phone",
    "location", "twitter_url", "telegram_username", "apollo_person_id",
)


def build_plan(connection, sources):
    changes = {}
    reports = []
    for list_id, csv_path in sources:
        lead_list = connection.execute(
            "SELECT name FROM lead_lists WHERE id = ?", (list_id,),
        ).fetchone()
        if not lead_list:
            raise ValueError("Unknown list: " + list_id)
        imported, result = parse_csv(csv_path, lead_list_id=list_id)
        if result.errors:
            raise ValueError(str(result.errors))
        by_url, by_email = defaultdict(list), defaultdict(list)
        for lead in imported:
            if lead.linkedin_url:
                by_url[lead.linkedin_url].append(lead)
            elif lead.email:
                by_email[lead.email.strip().lower()].append(lead)

        rows = connection.execute(
            "SELECT l.* FROM leads l WHERE EXISTS (SELECT 1 FROM "
            "lead_list_memberships m WHERE m.lead_id=l.id AND m.lead_list_id=?) "
            "OR l.lead_list_id=? ORDER BY l.id", (list_id, list_id),
        ).fetchall()
        matched, unmatched, ambiguous = 0, [], []
        field_counts = Counter()
        for row in rows:
            url = normalize_linkedin_url(row["linkedin_url"] or "")
            candidates = by_url.get(url, []) if url else by_email.get(
                (row["email"] or "").strip().lower(), [],
            )
            if not candidates:
                unmatched.append(row["id"])
                continue
            if len(candidates) != 1:
                ambiguous.append(row["id"])
                continue
            matched += 1
            source = candidates[0]
            updates = {
                field: getattr(source, field) for field in FIELDS
                if not (row[field] or "").strip() and getattr(source, field)
            }
            if not updates:
                continue
            entry = changes.setdefault(row["id"], {
                "before": {field: row[field] for field in FIELDS}, "after": {},
            })
            for field, value in updates.items():
                if field in entry["after"] and entry["after"][field] != value:
                    raise ValueError("Conflicting sources for lead " + row["id"])
                entry["after"][field] = value
                field_counts[field] += 1
        reports.append({
            "list_id": list_id, "name": lead_list["name"], "csv": str(csv_path),
            "csv_sha256": hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
            "source_rows": result.total_rows, "source_duplicates": result.duplicates_skipped,
            "list_leads": len(rows), "matched": matched, "unmatched": unmatched,
            "ambiguous": ambiguous, "fields_filled": dict(field_counts),
        })
    return changes, reports


def delivery_state(connection):
    # Metadata is the only permitted change, including for already-sent leads.
    return {
        table: [dict(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY id")]
        for table in ("campaigns", "campaign_lead_assignments", "lead_list_memberships")
    }, [
        {key: row[key] for key in row.keys() if key not in FIELDS}
        for row in connection.execute("SELECT * FROM leads ORDER BY id")
    ]


def repair(db_path, sources, apply=False, audit_dir=None):
    mode = "rw" if apply else "ro"
    connection = sqlite3.connect("file:" + str(db_path) + "?mode=" + mode, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        if not apply:
            changes, reports = build_plan(connection, sources)
            return {"applied": False, "leads_to_update": len(changes), "lists": reports}

        if audit_dir is None:
            raise ValueError("--audit-dir is required with --apply")
        directory = Path(audit_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = directory / (stamp + "-before.db")
        fd = os.open(str(backup_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with sqlite3.connect(str(backup_path)) as backup:
            connection.backup(backup)

        connection.execute("BEGIN IMMEDIATE")
        changes, reports = build_plan(connection, sources)
        if any(report["ambiguous"] for report in reports):
            raise ValueError("Ambiguous matches; refusing repair")
        state_before = delivery_state(connection)
        audit_path = directory / (stamp + "-metadata.json")
        fd = os.open(str(audit_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as audit:
            json.dump({"lists": reports, "changes": changes}, audit, indent=2)
            audit.flush()
            os.fsync(audit.fileno())
        for lead_id, entry in changes.items():
            updates = entry["after"]
            connection.execute(
                "UPDATE leads SET " + ", ".join(field + "=?" for field in updates) + " WHERE id=?",
                tuple(updates.values()) + (lead_id,),
            )
        if delivery_state(connection) != state_before:
            raise RuntimeError("Delivery state changed; rolling back")
        connection.commit()
        remaining, _ = build_plan(connection, sources)
        if remaining:
            raise RuntimeError("Repair was not idempotent; inspect audit")
        return {
            "applied": True, "leads_updated": len(changes), "lists": reports,
            "delivery_state_unchanged": True, "remaining_repairs": len(remaining),
            "backup": str(backup_path), "audit": str(audit_path),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--source", nargs=2, action="append", required=True, metavar=("LIST_ID", "CSV"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit-dir")
    args = parser.parse_args()
    print(json.dumps(repair(args.db, args.source, args.apply, args.audit_dir), indent=2))


if __name__ == "__main__":
    main()

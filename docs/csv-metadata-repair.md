# Hiring CSV Metadata Repair

October 4, 2026: repaired missing metadata for 574 existing canonical leads.

## Cause

- `Hiring HRs - 09/25` used `pid,nm,ti,co,em,li` headers. The URL value was
  detected, but names, company, job title and email were stored in `extra_data`.
- `Hiring - 09/17` used `contact_name` and `contact_title`, which were not
  mapped. Its original CSV also supplies locations missing from 483 records.
- The list upload endpoint deletes the temporary original CSV after parsing.
  The original files were recovered locally and archived on the server.

## Changes

The importer recognizes the two proven schemas, related person-field aliases,
and `companyname`. Explicit first/last columns take precedence over full names
regardless of header order. Synthetic `pid` identifiers remain extra data and
are never treated as Apollo IDs. URL normalization and deduplication remain.

`scripts/repair_csv_metadata.py` is read-only by default. With explicit
`--apply`, it fills blank fields only, matching canonical LinkedIn URLs or a
unique email for records without URLs. Existing enriched fields are preserved.
It creates a private WAL-safe SQLite backup and before/after JSON audit, verifies
that all delivery state, memberships and campaigns remain unchanged, and checks
that a second run has nothing left to repair. It never imports additional leads.

## Production Result

Every existing record matched its source: 85 HR leads and 492 hiring leads.
There were no unmatched or ambiguous matches.

- HR list: restored first/last names, company and title for 83 leads, plus 79
  emails. Two pre-existing complete records were preserved.
- Hiring list: restored 269 first names, 268 last names, 270 titles, four emails
  and 483 locations. A source containing only a single name cannot supply a
  surname. Missing source URLs or job titles are not invented.
- 574 distinct leads were updated. A second dry run reports zero changes.
- Campaigns remain paused; lead statuses, URLs, timestamps, retries,
  connection history, extra data and memberships are unchanged.

Private server artifacts (excluded from Git):

- Source CSVs: `/root/linauto/data/import_sources/20261004/`
- Backup: `/root/linauto/data/repair_audits/20261004T154933051556Z-before.db`
- Audit: `/root/linauto/data/repair_audits/20261004T154933051556Z-metadata.json`
- Previous importer: `/root/linauto/data/deploy_backups/20261004-importer/importer.py`

Verification: importer/repair tests pass, along with the connection reliability,
URL search, browser pool, login lifecycle and classifier tests. The broad unit
suite reports 216 passing and 25 pre-existing failures; the two outdated importer
expectations were aligned with the existing named-no-URL and location behavior.
No real invitations were sent during verification.

# Slow, resumable event attendee extraction

Event imports now use conservative pacing by default. Search loads wait
360-420 seconds apart (roughly 85-100 attendees/hour for ten-result pages).
Larger pages extend the next delay to at least 36 seconds per returned person.
Feed validation shares this pacing, and the search tab stays on `about:blank`
between pages to stop background LinkedIn polling.

This is a workload limit, not a guarantee against LinkedIn restrictions.
Search rankings can change during a long run; a resumed cursor cannot guarantee
a complete or stable roster.

## Stop and resume

- Every successful page is committed before the cursor advances.
- Attendees remain in the same list after an error. Existing lead statuses,
  campaign assignments, and populated metadata are preserved.
- Login/checkpoint redirects, HTTP 401/403/429, restriction warnings, redirect
  loops, ambiguous pagination, repeated pages, and persistence failures stop
  immediately. No feed recovery or challenge retries are attempted.
- Re-scrape resumes the saved page using the same account. Duplicate attendees
  do not terminate pagination or create duplicate lead records.
- Only a disabled Next control, explicit no-results state, or a requested
  total limit counts as completion. The 100-page boundary is an incomplete stop.
- Completion and failures send the configured Slack DM with the saved count.
- A process restart does not automatically resume scraping. Persisted running
  jobs are shown as interrupted and require an explicit Re-scrape.

## Operational state

Private atomic JSON files are stored beside the configured SQLite database in
`event_scrape_jobs/`. They contain counts, cursor, account ID, limit, and pacing
timestamps, never cookies or proxy credentials. No schema migration is required.
Running/queued jobs are unique per list and account in this single-process app;
the existing BrowserPool external lease prevents simultaneous account contexts.
Only one API worker is supported by this job registry.

`POST /api/v1/lead-lists/event-import` accepts `start: false` to prepare a list
without launching a browser. The account must reconnect and Save, then the user
selects Re-scrape. Start/resume rejects accounts that are not active or lack a
configured proxy. No campaign is activated by these endpoints.

The list delete endpoint removes its job file and refuses deletion during an
active scrape. Shared lead records are retained by the existing list-delete
behavior.

## Verification

`tests/unit/test_event_scrape_reliability.py` covers pacing across restarts,
duplicate-run guards, partial persistence and resume, existing-status protection,
authentication/restriction stops, pagination ambiguity, and prepared login gates.
Tests use mocked pages and SQLite; they never contact LinkedIn.

# Data layer audit and implementation plan

Verified 2026-10-09 against local and deployed commit 04953d6. Production
database queries used read-only SQLite connections. No live data was changed.

## Reassessment of the July audit

| Prior finding | Current evidence | Disposition |
| --- | --- | --- |
| 226 single-assignment status differences | 462 now: 344 removed, 107 withdrawn, 11 scheduled | Mixed legacy reads remain; do not infer user intent from a scalar status |
| Skipped leads pending in active campaigns | Zero single-assignment skipped/pending pairs; 102 pairs across multi-campaign leads, all in paused campaigns | Old active-campaign warning is outdated; cross-campaign differences may be valid |
| 2,230 multi-campaign leads | 2,474 of 11,126 leads | Still relevant; one scalar cannot describe campaign state |
| Global list filter uses legacy list ID | Confirmed; 3,620 memberships across 1,923 leads differ from that ID | Fixed locally with membership EXISTS, including counts and combined unassigned filters |
| URL imports omit four enrichment fields | Confirmed in bulk_create_leads | Fixed locally; URL and no-URL regression tests added |
| Re-import does not enrich existing profiles | Confirmed in this method | Still relevant; separate enrichment methods do exist, so this is not a claim that all enrichment is broken |
| Canonical URL dedup lacks a database guard | Composite campaign/URL constraint remains; all legacy campaign IDs are NULL | Still relevant; zero duplicate stored URLs today does not prove concurrency safety |
| Campaign and list counters drift | Nicolas counter 5,835 versus 6,179 assignments; all list counters currently agree | Campaign issue remains; old list examples are outdated |
| Imports fetch up to 100,000 objects | Campaign CSV endpoint still does this | Still relevant; remove redundant prefiltering carefully |
| Import attachment N+1 queries | Per-lead assignment and membership SELECTs remain | Still relevant; actual July query count was an estimate, not a trace |
| Missing standalone URL index | Confirmed on live database | Still relevant |
| One orphan lead | 24 leads without memberships or assignments | Investigate provenance; do not delete automatically |

No assignments reference a list membership that is missing. Duplicate URL check
uses exact stored strings; it does not establish that URL variants represent
different people.

## Corrections to the old recommendations

- Mirroring a single Lead.status across assignments cannot eliminate drift for
  people in different campaigns. Assignment differences are often legitimate.
- A skipped legacy status does not prove that every other campaign was skipped.
  Historical user actions need campaign/account context before repair.
- Removed assignments may describe an intentional campaign exclusion rather
  than a globally deleted person. Global deletion needs its own explicit meaning.
- The counter discrepancy should not be repaired until total-versus-active
  assignment semantics are explicit. Nicolas has 5,477 non-removed assignments.
- There is no defensible SQLite threshold of ten accounts. Measure write lock
  waits, transaction durations, job queues, and browser capacity instead.
- The fundraising agent already uses API integration and also performs direct
  database reads and an activity-log write. Consolidate the remaining coupling.
- A read-only integrity report should classify differences before automated
  reconciliation. No status resetting or Slack messages should be implicit.

## Implementation sequence

### 1. Bounded regressions (started locally)

Preserve twitter_url, telegram_username, location, and apollo_person_id when
creating URL-keyed leads. Filter library list selections through memberships.
Test both URL/no-URL imports, multi-list selections without duplicate results,
pagination counts, and combined assigned/unassigned selection.

### 2. Transactional ingestion

Audit all CSV, scraper, CLI, and integration callers of bulk_create_leads.
Return explicit counts for new people, reused people, new memberships, new
assignments, and updated profiles. The current fallback return value can report
memberships as campaign additions when no assignment was added.

Replace per-row attachment reads with chunked batch reads and bulk inserts.
Avoid ORM loading just to collect URLs. Existing-campaign prefiltering must not
discard a row before a new list membership or profile enrichment is recorded.
Keep list creation, relationships, counters, and import reporting in one
transaction; clean up uploaded files even on failure.

Fill missing profile values by default; define source precedence before replacing
existing values. Test replay, partial failure rollback, empty imports, duplicate
rows, existing campaign membership plus a new list, and concurrent imports.

### 3. Explicit campaign state

Read/write state using assignment IDs or explicit campaign context. Separate
profile edits from campaign state updates: the current sync helper copies all
legacy state fields when a campaign can be resolved.

Approved library presentation: show the most recently created campaign
assignment's status with a 1/N indicator and all campaigns on hover/focus.
A campaign filter shows that campaign's status. Status filters and sorting use
the displayed assignment, including removed assignments; leads without any
assignment retain their legacy status. Assignment creation time defines recency,
with assignment ID as a deterministic tie-breaker.

Implemented locally: one batched campaign-summary lookup for the library page,
assignment-based status/error filtering and status sorting, selected-assignment
timestamps and error details in the response, and an accessible hover/focus
indicator. Regression tests ensure these reads do not mutate stored statuses.

Audit dispatcher, planner, follow-ups, exports, stats, skip/requeue/restore,
withdrawal, and acceptance checks. Verify two campaigns can advance independently
and a profile edit cannot change their state. Keep paused campaigns paused.

### 4. Database and integration guardrails

Verify URL normalization and duplicates before adding a unique canonical URL
index. Handle insert conflicts transactionally; an index alone does not make
retries correct. Add import run/source identifiers and per-row provenance,
including failure/retry information and idempotency keys.

Derive counters from relationships or update them in the same transaction.
Inspect query plans before dropping legacy indexes. Check production connection
settings for foreign keys, journal mode, busy timeout, and migration parity.
Add a read-only audit command with machine-readable results and explicit repair
options. Validate backup restoration before schema migrations.

### 5. Capacity-driven scaling

Measure ingestion query counts, lock waits, API latency, memory, and account job
backlogs. Add durable job claims and account-level serialization before multiple
worker processes. Integrations should normalize into one ingestion contract and
use durable retryable jobs. Consider PostgreSQL when observed contention or
multi-worker requirements warrant it, rather than at an arbitrary account count.

## Release gates

Focused regression tests first, then existing data-layer and dispatch unit tests.
Schema changes need migration and rollback rehearsal on a restored snapshot.
Production verification must check API behavior and relationship counts after
deployment. No blanket live status repair is part of this plan. Zero future bugs
cannot be guaranteed; tests, staged changes, and observability reduce the risk.

The expanded test run exposed seven existing failures: six read-parity tests
still expect canonical imports to populate legacy campaign IDs/statuses, and
one dual-write test expects an import to preserve SCHEDULED. These failures
reproduce against untouched HEAD and need explicit contract decisions before
updating fixtures. The new tests reproduce the field-loss and membership-filter
bugs on untouched HEAD.

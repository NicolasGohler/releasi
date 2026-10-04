# Connection Request Reliability Plan

Status: implemented and deployed, production checks passed (October 4, 2026).

## Implementation Notes

- The live, exclusive browser check reproduced the failure: the SDUI profile
  uses an `h2` for the owner name, and the Connect anchor identifies the target
  with `vanityName` in `/preload/custom-invite/`. The original code saw no owner,
  and its slug/name heuristic rejected the actual target. Exact link identity
  now wins over heuristic display-name matching. All matching candidates are
  checked, so an unrelated first sidebar result cannot hide the right action.
- Navigation waits for profile identity and actions without calling
  `window.stop()` on the first broadly matched button. Stylesheet blocking is
  unchanged: the target DOM was available under that policy, so it was not
  established as the cause.
- Existing assignment `retry_count`, `scheduled_at`, `updated_at`, and
  `error_message` persist retry state without a schema migration. DOM failures
  wait 24 hours, with three failures requiring manual review. Known pre-send
  navigation failures back off 15/30/60 minutes, capped at one hour, with six
  failures requiring review. Terminal/uncertain post-send outcomes never retry.
- Three consecutive DOM/network/action failures stop the current run and
  persist an account backoff of at least one hour. Session spacing is recorded
  even when nothing was sent. Daily/weekly send limits are unchanged.
- Only confirmed browser authentication loss marks connection cookies expired;
  error text and generic action failures do not prove expiry. CAPTCHA pauses
  the account for manual review. Authentication loss no longer resets leads.
- Startup and targeted post-login checks use the existing proxied browser, not
  bare HTTP cookies. Process checks and auth checks have separate timestamps.
  External/manual leases prevent concurrent use and reject queued stale
  credentials after a login changes the session.
- Login profile replacement is staged and preserves the previous profile on
  failure. The API exposes failed profile saves to the existing dashboard.
  Nicolas's profile directory was owned by UID 502 and unreadable by container
  UID 1000; ownership was repaired only for that account, and access verified.
- Event scraping reserves the account, reloads credentials after waiting, and
  releases its browser before enrichment. Browsers and manual login fail closed
  when no account proxy is available instead of falling back to the server IP.
- Per-lead failure logs contain category, reason, attempt number, next eligible
  time, and exhausted state. Daily errors increment once per failed attempt.
  Batch-stop summaries are separate, and exhausted retries notify Slack.
- Copied profile URLs are normalized before global/campaign searches and
  filtered exports, including `isSelfProfile`, tracking parameters, and hashes.
- Focused verification: 99 tests pass, including real local Playwright DOM
  fixtures (no LinkedIn traffic), durable retry eligibility across new DB
  sessions, campaign isolation, ambiguous outcomes, and login/pool lifecycle.
  The broad suite's 27 failures also reproduce with the original repository;
  these concern older fixtures and unrelated importer/planner/state behavior.
  Broad verification reproduces the same 27 baseline failures. No previously
  passing test regressed; focused cases also cover confirmed expiry/CAPTCHA
  flags and a stale canonical status on an eligible campaign assignment.
- Actual production mount: `/root/linauto/src` to `/app/src`. Scoped source
  backups are in `/root/linauto/data/deploy_backups/20261004T131946Z`. Deployment
  uses tested changed files and a container restart, not a divergent Git pull.
  No campaign is resumed and no historical lead state is reset.

## Production Verification

- All 12 changed backend files match their tested local SHA-256 hashes and
  compile under the actual container Python environment.
- Read-only, exclusive checks on `royapourmand`, `madhusudan16`, and `iaksamit`
  find the intended Connect anchor and owner heading on each profile. No Connect
  or Send button is clicked by these checks.
- The full-browser account check returns `valid: true`. Nicolas remains ACTIVE
  with proxy country `ca` and timezone `America/Toronto`.
- Both global `/leads` and Nicolas's campaign lead search return the same single
  lead for the canonical URL and the URL with `?isSelfProfile=false`.
- All eight campaigns remain PAUSED. The three affected assignments remain
  `pending` with retry count zero. There were zero sent requests on October 4
  at the end of verification. Existing errors and skips were not requeued.
- Profile permissions were verified as container UID 1000. New login saving is
  covered by lifecycle/copy-failure tests; no manual re-login was initiated.
- Actual invitation delivery is intentionally untested while campaigns are
  paused. These checks confirm lookup, session, API search, and deployment,
  not an end-to-end send or a promise about future LinkedIn restrictions.

## Evidence and Goal

The October 3 investigation found 18 recorded successful sends and 69 Connect
lookup failures across three profiles. Twenty-two batches stopped after three
consecutive failures and then retried the same pending leads. Their retry counts
remained zero. Saved screenshots contain visible Connect links.

The earlier `no_connect_button` fix avoids a false COOKIE_EXPIRED classification,
but it calls a DOM failure a network failure and supplies no persistent retry
policy. The dispatcher updates its session gap only after successful sends.

There are also two independent problems: the browser pool stamps session
validation after a local tab-open check, and the October 3 login failed to copy
Nicolas's full browser profile because of directory permissions. Daily error
statistics omit the failed batches.

Goal: recognize the intended profile's Connect action, prevent failed profiles
from blocking the queue, and report failures accurately without confusing them
with expired authentication. Preserve configured limits and campaign pauses.

## 1. Establish the Profile Detection Failure

- Inspect the failing profiles using the existing account context, exclusively
  and through its configured proxy. Observe DOM and screenshots without sending
  an invitation. Do not start a second context alongside the pool or noVNC.
- Capture profile identity, candidate labels/hrefs, their containing profile
  card, heading structure, and readiness signals. Do not log cookie values.
- Compare a successful profile and the three failing profiles. Determine
  whether this is a changed layout, premature loading cancellation, or both.
- Current resource blocking removes profile stylesheets, and the navigator calls
  `window.stop()` after a broadly scoped action selector appears. These are
  plausible contributors, not established causes. Test them individually.

Acceptance: a saved DOM fixture reproduces the failure and explains why the
visible Connect action is rejected.

## 2. Repair Profile Identity and Action Selection

Files: `linkedin/selectors.py`, `linkedin/actions.py`, `linkedin/navigator.py`.

- Define readiness around the target profile's identity and action container,
  rather than any Connect link on the page, including sidebar suggestions.
- Support the observed heading structure; do not depend only on `main h1`.
- Evaluate candidates in the verified profile action area. Reject unrelated
  sidebar invitations without allowing the first unrelated match to hide the
  actual profile action.
- Match identity using the observed profile URL/identity signals and display
  name. Keep uncertainty explicit; never solve missing ownership by disabling
  the guard or inviting whichever Connect link is found.
- Keep primary Connect, More > Connect, already-connected, pending, and
  email-required flows distinct. Change resource blocking/loading cancellation
  only if the controlled comparison demonstrates that it causes the failure.

Acceptance: fixtures cover the new layout, sidebar candidates, primary and
dropdown actions, missing identity, pending requests, and existing connections.
An observation-only live check locates the intended action on the failing pages.

## 3. Introduce Persistent, Bounded Retry Handling

Files: `campaign/executor.py`, `safety/dispatch_decisions.py`,
`scheduler/runner.py`, `db/repository.py`; assignment fields and migration as needed.

- Extend the existing result/DispatchIntent contract with a failure category:
  DOM mismatch, network failure, confirmed authentication loss, action limit,
  or ambiguous send outcome. Keep decisions in the pure classifier.
- Stop treating `no_connect_button` as a network error. Only confirmed
  authentication loss can mark COOKIE_EXPIRED; generic failures cannot prove it.
- Persist attempt time, next eligible attempt time, and failure category on
  CampaignLeadAssignment. Reuse its retry count where compatible. Queue reads
  honor eligibility before applying list priority and creation-time order.
- Proposed initial DOM policy: at most one attempt per profile per day, with
  three attempts before manual review. The attempt limit applies to failures,
  not successful sends. Allow other eligible leads to proceed.
- For repeated failures across distinct profiles, stop the account's current
  run with a persisted next-attempt time rather than sweeping the whole queue.
  Proposed network backoff starts at 15 minutes and grows to 60 minutes.
- Record an end-of-attempt time even when a batch sends nothing. Preserve the
  account's configured session spacing and daily/weekly budgets.
- An uncertain outcome after clicking Send needs reconciliation before retry;
  it must not risk a duplicate invitation.
- Do not requeue historical ERROR/SKIPPED leads or activate paused campaigns
  as part of migration or deployment.

Acceptance: simulated scheduler ticks and process restarts do not repeatedly
select the same ineligible leads. Tests cover queue progress, retry limits,
cross-profile failures, budgets, and duplicate-send prevention.

## 4. Separate Browser Health from LinkedIn Authentication

Files: `linkedin/pool.py`, `scheduler/runner.py`, `linkedin/login_session.py`,
`api/routes/accounts.py`.

- Give browser-process health and verified LinkedIn authentication separate
  timestamps. Opening a blank tab must not suppress the real session check.
- Stamp authentication only after positive browser-level authenticated-page
  evidence. Timeouts and missing UI are inconclusive, not expired cookies.
- Retain reactive validation and the existing validation cooldown. Avoid
  adding periodic cookie probes or bare HTTP requests with `li_at`.
- Inspect and repair ownership only for the affected profile directories.
  Confirm the container user can preserve the saved profile.
- Coordinate login save with pool eviction and exclusive account ownership
  before copying the profile. Surface a failed profile save in the API/UI
  instead of returning an unqualified success.
- Ensure login, event scraping, diagnostics, and dispatch honor one account
  context at a time; separate browser objects do not establish exclusivity.

Acceptance: blank-tab health checks cannot refresh auth status; session-loss,
network timeout, profile-copy failure, and busy-account paths are covered.

## 5. Make Failure Reporting Consistent

Files: executor/intent application, action log and daily statistics; dashboard
activity presentation only where needed.

- Log each failed lead attempt with lead/campaign IDs, failure category,
  reason, attempt count, and next eligible attempt time.
- Increment daily errors once per failed attempt. Batch-stop summaries remain
  summaries and must not double-count the underlying failures.
- Replace misleading proxy-connectivity labels for DOM failures. Expose
  waiting-for-retry and manual-review reasons using persisted state.
- Keep send success separate from attempted, skipped, deferred, and failed.

Acceptance: one fixture run reconciles attempt logs and summary counts, including
an all-failure batch. The dashboard shows the actual reason for stalled progress.

## Rollout and Verification

1. Review the isolated URL-search fix and this plan.
2. Build reproduction fixtures, then implement profile detection and retry
   handling together so successful recognition and failure containment agree.
3. Complete auth validation, profile-save handling, and reporting fixes.
4. Run focused unit/integration tests, including state across restarts and both
   continuous and planned dispatch modes. Validate migration on a DB copy.
5. Perform an exclusive, observation-only check through Nicolas's proxy.
6. Deploy the reviewed changes at a time that avoids an in-flight dispatch.
   Verify the actual Docker mount and reconcile the server's Git divergence
   before relying on the generic deploy script; do not reset server changes.
7. Observe naturally scheduled runs and verify queue progress, bounded retries,
   stable session checks, and matching error counts. Do not send a test invitation
   or resume a paused campaign without an explicit request.

The URL-search change needs no DB migration or frontend deployment. It applies
the existing canonical LinkedIn normalizer before global/campaign search and
filtered campaign exports; `isSelfProfile`, other URL parameters, and trailing
slashes are ignored while ordinary name/company searches continue to work.

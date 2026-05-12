# Linauto — Backlog

Pending follow-up work, ranked by leverage. Cross items off as they ship.
Use this as a working doc — the bar to add is "I'd otherwise forget."

## High priority

- [ ] **Refactor dispatchers to use the pure classifiers** — Pure
  `classify_followup_result` / `classify_connection_result` shipped in
  `safety/dispatch_decisions.py` with 20 unit tests. Dispatchers in
  `scheduler/runner.py` still inline the same logic. Refactor risk: the
  classifier output already encodes the intended behaviour, so the
  refactor is mostly mechanical (call classifier → switch on the intent
  fields → execute side effects). Without this, the classifiers can
  drift silently from the actual dispatcher code. _Started 2026-05-12._

- [ ] **Centralize session-health detection** — Four places currently
  duplicate variants of "classify exception → mark cookie_expired → notify":
  connection dispatcher, followup dispatcher, daily acceptance checker,
  acceptance catchup. A `with_session_guard(account, repo, action)` wrapper
  would eliminate the drift risk. Best done after one more LinkedIn action
  is added so the right abstraction is obvious.

- [ ] **Audit `navigator.go_to_feed` exception path** — The pre-dispatch
  feed ping uses a `session_valid` boolean. Verify that a redirect-loop
  exception inside `go_to_feed` propagates correctly to the classifier
  rather than being swallowed by a generic log.

## Medium priority

- [ ] **Dashboard "Recover stuck leads" button per account** — The SQL we
  ran manually for Nicolas (274 leads → PENDING, 1 → FOLLOWUP_SCHEDULED)
  should be a one-click action. Query: leads in ERROR with
  `error_message LIKE '%TOO_MANY_REDIRECTS%'` or session-signal keywords,
  revert based on `connection_accepted_at` / `followup_sent_at` state.

- [ ] **Catchup: in-flight cancellation** — Long catchup scans (~30 days)
  hold the browser pool. Need a Cancel button + polling endpoint that
  flips a flag the scroll loop checks. From the safety review (item #13).

- [ ] **Catchup: invitation-manager diff** — v1 is acceptance-only. After
  a 3-week pause, half the pending invitations may have expired or been
  declined. Add the diff step, but only mark `WITHDRAWN` if we're
  confident the invitation manager scrolled to the end (safety review #12).

- [ ] **Auto-recovery loop verification** — `CLAUDE.md` says
  `check_cookie_health` auto-recovers cookie_expired accounts on next
  dispatch. We now mark cookie_expired more aggressively — manual-test
  the recovery flow to make sure it still fires correctly.

## Low priority — observability

- [ ] **Per-account cookie-expiry telemetry** — New `cookie_expiry_events`
  table or counter columns on Account. Spots accounts that repeatedly
  expire (likely fingerprint/proxy issues, not true expiry).

- [ ] **Surface consecutive-error counter in dashboard** — Persist a
  ring-buffer of last N action outcomes per account. UI shows
  "⚠️ 2 of last 3 followups failed" before the 3rd-strike triggers
  cookie_expired.

- [ ] **Catchup rate-limit user-facing message** — Currently the catchup
  service skips silently with `skipped_reason="rate_limited_..."`. The
  dashboard should surface "Catchup ran X minutes ago, available again
  at HH:MM" with a countdown.

- [ ] **Telemetry for first few catchup scrolls** — Structured logs of
  scroll duration, card count, hit_iteration_cap. Trust the 30-day scroll
  only after seeing real-world numbers.

## Low priority — UX polish

- [ ] **Catchup completion notification when tab is closed** — Browser
  Notification API, fired by the polling hook when status transitions
  `running → done`. Useful for the long pause case.

- [ ] **Cross-campaign dedup of `recent_slugs`** — Back-to-back catchups
  on different campaigns of the same account each re-load the connections
  page. Cache `recent_slugs` per account for ~10 minutes (in-process is
  fine).

- [ ] **Rename `account.last_catchup_at` → `last_pause_catchup_at`** —
  Slightly ambiguous now that we have both "daily acceptance check" and
  "long-pause catchup." Only worth doing if a migration is happening for
  another reason.

## Test coverage gaps

- [ ] **Per-account exception handler in daily acceptance checker** —
  Mock `get_recent_connections` to raise `Exception("net::ERR_TOO_MANY_REDIRECTS")`,
  assert the account is marked cookie_expired without aborting the loop.

- [ ] **Catchup outer-exception session classification** — Same shape:
  mock a redirect-loop exception during scan, assert
  `account.status == COOKIE_EXPIRED` is set.

- [ ] **End-to-end resume-dialog flow** — Cypress/Playwright dashboard
  test: paused campaign with `paused_at > 72h` → click Activate → dialog
  appears → click "Resume & run catchup" → task_id returned → polling
  completes → toast appears.

## Done

- [x] One-shot acceptance catchup with paused_at tracking + resume dialog (commit `ab66724`)
- [x] Followup dispatcher cookie-expiry detection via redirect-loop signal (commit `86c0580`)
- [x] Connection-request + acceptance checker + catchup session-expiry classification + CAPTCHA Slack notify (commit `23fd592`)
- [x] Recover 275 stuck ERROR leads from pre-fix redirect-loop failures (prod-only SQL, 2026-05-12)
- [x] Pure `classify_followup_result` + `classify_connection_result` decision functions with 20 unit tests (next commit)

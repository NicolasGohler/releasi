# Linauto Roadmap

> **DO NOT DELETE OR MODIFY** — This is the project roadmap maintained across sessions.
> Last updated: 2026-02-25

---

## Current State

### Phase 1 (Foundation) — COMPLETE
- Typer CLI with all core commands
- CSV import with auto-detect LinkedIn URLs, dedup, column mapping
- Template rendering with `{{variable}}` syntax
- Playwright browser module with persistent contexts per account
- SQLAlchemy async ORM (5 tables: Account, Campaign, Lead, ActionLog, DailyStat)

### Phase 2 (Scheduling & Safety) — COMPLETE
- APScheduler daemon with 4 jobs (daily planner, dispatcher, acceptance checker, cooldown checker)
- Warmup ramp: ~120/week in week 1 → ~200/week by week 3, then uncapped
- Cooldown: auto-pause on weekly limit, resume Monday 8-11 AM
- Clustered daily plans with deterministic seeds per account/day
- Browsing noise (profile views, feed likes, feed scroll) — 40% chance before each action
- Stealth: playwright-stealth, anti-automation Chromium args, per-account fingerprinting (UA, proxy, timezone, locale)
- Profile filtering: skip no-photo or low-connection profiles (configurable per campaign)
- Multi-strategy Connect button finding: scoped CSS → get_by_role → JS DOM evaluation
- Debug tooling: `debug-profile` command, auto-screenshots on failure, button dumps
- Lead management: `reset-leads` command for reprocessing
- Docker deployment with layer-cached builds, non-root user, 2G memory limit

### What Works Today
- **Multi-account**: Fully isolated (separate browser profiles, cookies, rate limits, warmup state)
- **Deployment**: Docker Compose on Hetzner VPS (CX22, 4GB RAM)
- **Monitoring**: SSH + `docker compose logs -f` + `linauto campaign status` via CLI
- **Tests**: 74 total (39 run locally, all 74 pass in Docker)

### Known Limitations
- Accounts are processed **sequentially** in each dispatch tick (not concurrently)
- **SQLite** — single-writer, no concurrent API access possible
- **No remote monitoring** — must SSH into server for all operations
- **No follow-up messages** — can only send connection requests, not follow-ups after acceptance

---

## Phase 3: Follow-up Messages — NOT STARTED

Send a follow-up DM after a connection request is accepted.

### What's Needed
- **Acceptance detection** already exists (`check_acceptances` job, runs every 3 hours)
- When a lead moves to `CONNECTED`, schedule a follow-up after `followup_delay_hours`
- New `send_followup` action in the dispatcher that uses `actions.send_message()`
- Lead transitions: `CONNECTED` → `FOLLOWUP_SCHEDULED` → `FOLLOWUP_SENT` → `COMPLETED`
- State machine transitions already defined for this flow

### Files to Change
| File | Change |
|------|--------|
| `scheduler/runner.py` | Add follow-up scheduling in `check_acceptances`, add follow-up dispatch in `dispatch` |
| `campaign/executor.py` | Add `execute_followup()` method |
| `db/repository.py` | Add `get_followup_due_leads()` query |
| `cli.py` | Show follow-up stats in `campaign status` |

### Estimated Scope
Small — most infrastructure already exists. ~200 lines of new code.

---

## Phase 4: Server Mode + Remote Monitoring — NOT STARTED

Replace SSH+CLI workflow with a web dashboard for remote monitoring and control.

### Architecture Decision (from user discussion)
```
Docker Compose on Hetzner VPS
├── linauto-worker     (scheduler daemon — runs campaigns)
├── linauto-api        (FastAPI — serves dashboard data)
├── linauto-dashboard  (Nginx serving static frontend)
└── postgres           (shared DB, persistent volume)
```

### User Requirements
- **Monitoring level**: Web dashboard (API + UI) — not just logs
- **Scale**: Starting with 1 account for testing, ramping to 5+
- **Server**: Hetzner VPS (already have CX22)

### Implementation Order

#### Step 1: PostgreSQL Migration
- Swap `aiosqlite` → `asyncpg` in `db/engine.py`
- Update `db_url` in config to `postgresql+asyncpg://...`
- Add `postgres` service to `docker-compose.yml` with persistent volume
- Regenerate alembic migrations (or test existing ones work with pg)
- **Why first**: Everything else builds on concurrent DB access

#### Step 2: FastAPI Read-Only API
- New `src/linauto/api/` module (stubs already exist)
- Endpoints:
  - `GET /health` — scheduler status, last dispatch time, errors
  - `GET /accounts` — list with status, warmup progress, cooldown state
  - `GET /campaigns` — list with progress counts
  - `GET /campaigns/{id}/status` — detailed status + lead breakdown
  - `GET /campaigns/{id}/leads` — paginated lead list with filters
  - `GET /activity` — recent action log (last N entries)
- Run alongside scheduler in same container or as separate service

#### Step 3: Simple Web Dashboard
- Static HTML/JS page served by Nginx (or embedded in FastAPI)
- Account cards with status indicators
- Campaign progress bars
- Live activity feed (polling or SSE)
- Error/alert highlighting

#### Step 4: Write Endpoints + Full Control
- `POST /accounts` — add account
- `POST /campaigns` — create campaign
- `POST /campaigns/{id}/import` — upload CSV
- `PUT /campaigns/{id}` — update settings, toggle filters
- `POST /campaigns/{id}/activate` / `POST /campaigns/{id}/pause`
- `POST /campaigns/{id}/reset-leads`

#### Step 5: Concurrent Account Dispatch
- Change `dispatch()` inner loop from sequential `for` to `asyncio.gather()` per account
- Each account gets its own browser in parallel
- Important for 5+ accounts to avoid dispatch ticks timing out

#### Step 6: Notifications (Optional)
- Telegram bot or webhook on critical events:
  - Weekly limit hit / cooldown started
  - Session expired / CAPTCHA detected
  - Campaign completed
- Cheaper alternative to checking the dashboard constantly

### VPS Sizing
- CX22 (2 vCPU, 4GB RAM) handles 2-3 concurrent Chromium instances
- For 5+ accounts: consider CX32 (4 vCPU, 8GB RAM) or stagger dispatch windows
- PostgreSQL adds ~200MB RAM overhead

---

## Phase 5: Multi-Campaign Intelligence — IDEAS (Not Planned)

Future improvements if the tool proves effective:

- **A/B testing**: Run two message templates per campaign, track acceptance rates, auto-switch to winner
- **Smart scheduling**: Adjust send times based on when acceptances happen (time-of-day correlation)
- **Auto-retry with backoff**: Leads that error out get retried with exponential backoff
- **Campaign cloning**: Duplicate a campaign's settings/template for a new lead list
- **Lead enrichment**: Pull profile data (headline, company, location) during the filter check, save to `extra_data`
- **CSV export**: Export campaign results (lead status, timestamps) for CRM import

---

## Technical Debt & Quick Wins

| Item | Effort | Impact |
|------|--------|--------|
| Fix pydantic_core arch mismatch (local tests) | Low | DX improvement — all 74 tests run locally |
| Add `--dry-run` flag to `execute-once` | Low | Safer testing — navigate + filter but don't click Send |
| Structured JSON logging (for log aggregation) | Low | Better debugging in production |
| Connection count selector improvements | Low | Current selectors may miss some LinkedIn layouts |
| Acceptance check optimization | Medium | Check invitation manager page instead of visiting each profile individually |
| Graceful shutdown mid-batch | Medium | Currently loses progress if killed during a dispatch tick |

---

## File Quick Reference

```
src/linauto/
├── cli.py                  # All CLI commands (account, campaign, execute-once, run, debug-profile)
├── config.py               # Pydantic settings from YAML + env vars
├── db/
│   ├── engine.py           # Async SQLAlchemy engine (NullPool, SQLite)
│   ├── models.py           # 5 ORM tables (Account, Campaign, Lead, ActionLog, DailyStat)
│   └── repository.py       # All DB queries
├── campaign/
│   ├── executor.py         # execute_batch() + execute_single_lead()
│   ├── importer.py         # CSV import with auto-detect
│   ├── state_machine.py    # Lead status transitions
│   └── template.py         # {{variable}} rendering
├── linkedin/
│   ├── actions.py          # send_connection_request(), send_message(), multi-strategy button finding
│   ├── browser.py          # Playwright persistent context per account
│   ├── detector.py         # Limit/CAPTCHA/session detection
│   ├── navigator.py        # Page navigation + render wait
│   ├── noise.py            # Feed scroll, likes, profile views
│   ├── profile_filter.py   # Live photo + connection count filtering
│   └── selectors.py        # ALL LinkedIn DOM selectors (single source of truth)
├── safety/
│   ├── cooldown.py         # Monday-retry cooldown logic
│   ├── delays.py           # Human-like delays + typing simulation
│   └── limits.py           # Rate limit checking
└── scheduler/
    ├── planner.py          # Clustered daily plan generation
    ├── runner.py           # APScheduler daemon (4 jobs)
    └── warmup.py           # 3-week warmup ramp
```

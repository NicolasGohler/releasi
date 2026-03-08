# Linauto — LinkedIn Automation Tool

## Server
- **IP**: REDACTED
- **SSH**: `ssh root@REDACTED`
- **Docker container**: `linauto`
- **Live DB**: `/app/data/linauto.db` inside container
- **Query DB**: `docker exec linauto python3 -c "import sqlite3; ..."` (no sqlite3 binary in container)

## Overview
Dripify alternative. Automates LinkedIn connection requests and follow-up messages with safety-first design (warmup ramps, cooldowns, rate limits, stealth browsing).

## Tech Stack
- **Python 3.9+** (system has 3.9.6 — use `Optional[X]` not `X | None` in SQLAlchemy `Mapped[]`)
- **Playwright 1.58+** (async) + playwright-stealth for browser automation
- **SQLAlchemy 2.0** async ORM + aiosqlite (SQLite)
- **FastAPI** + uvicorn for REST API (port 8000)
- **Typer** CLI + Rich for terminal output
- **APScheduler** for daemon scheduling (6 jobs)
- **Pydantic Settings** for YAML config (`config/settings.yaml`)

## Project Structure
```
src/linauto/
├── cli.py                  # Typer CLI (all commands)
├── config.py               # Pydantic settings from YAML
├── db/
│   ├── engine.py           # Async SQLAlchemy engine (NullPool)
│   ├── models.py           # ORM models (5 tables)
│   └── repository.py       # Data access layer
├── campaign/
│   ├── executor.py         # Orchestrates browser actions + DB updates
│   ├── importer.py         # CSV import with auto-detect LinkedIn URLs
│   ├── state_machine.py    # Lead status transitions
│   └── template.py         # {{variable}} rendering
├── linkedin/
│   ├── actions.py          # Atomic actions (connect, message, withdraw)
│   ├── browser.py          # Playwright browser lifecycle + fingerprinting
│   ├── detector.py         # Limit/CAPTCHA/session detection
│   ├── login_session.py    # noVNC-based manual login flow (ephemeral Xvfb)
│   ├── navigator.py        # Page navigation
│   ├── noise.py            # Feed scroll, likes, profile views (human-like)
│   ├── pool.py             # Persistent BrowserPool singleton (max 3 contexts)
│   ├── profile_filter.py   # Live profile quality filtering
│   └── selectors.py        # ALL LinkedIn DOM selectors (update here when LinkedIn changes)
├── safety/
│   ├── cooldown.py         # Smart Monday-retry cooldown
│   ├── delays.py           # Human-like delays + typing simulation
│   └── limits.py           # Rate tracking
├── scheduler/
│   ├── planner.py          # Clustered daily plan generation
│   ├── runner.py           # APScheduler daemon (6 jobs)
│   └── warmup.py           # Warmup ramp with daily variation
└── api/
    ├── app.py              # FastAPI app factory
    ├── auth.py             # API key auth
    ├── schemas.py          # Pydantic request/response models
    └── routes/
        ├── accounts.py     # Account CRUD + login-session endpoints
        ├── campaigns.py    # Campaign management
        ├── leads.py        # Lead management
        ├── lead_lists.py   # Lead list endpoints
        ├── stats.py        # Stats endpoints
        └── health.py       # Health check
```

## Key Files
- `src/linauto/linkedin/selectors.py` — ALL LinkedIn DOM selectors. Update here when LinkedIn changes their UI.
- `src/linauto/linkedin/browser.py` — Browser fingerprinting. `_CHROMIUM_MAJOR` must match the actual Playwright Chromium binary (check with `chrome --version` in container).
- `src/linauto/db/models.py` — SQLAlchemy models. Use `Optional[X]` (not `X | None`) for Mapped[] annotations.
- `config/settings.yaml.example` — Reference config with all available settings.

## Scheduler Jobs (6 total)
| Job | Schedule | Purpose |
|-----|----------|---------|
| `daily_planner` | 06:00 daily | Assign scheduled_at to pending leads |
| `dispatcher` | Every 5 min | Execute due connection requests |
| `acceptance_checker` | Every 3h | Detect accepted connections |
| `cooldown_checker` | 00:00 daily | Resume paused accounts |
| `followup_dispatcher` | Every 30 min | Send follow-up messages |
| `keepalive` | 08:00 ±90min daily | Organic morning LinkedIn session |

## Anti-Detection Layer

### Browser Fingerprinting (`browser.py`)
- **UA must match Chromium binary**: `_CHROMIUM_MAJOR = 145` — update when rebuilding with newer Playwright. Mismatch between `navigator.userAgent` and `navigator.userAgentData` is a strong bot signal.
  - Check actual version: `docker exec linauto /home/appuser/.cache/ms-playwright/chromium-*/chrome-linux64/chrome --version`
- **Country-aware UA pool**: Mac UAs only for `{us, ca, gb, au, nz, ie}`; Windows-only for all other markets.
- **Sec-CH-UA headers**: Set on context to align Client Hints with UA string.
- **Deterministic viewport**: 5 realistic sizes `[(1366,768),(1440,900),(1920,1080),(1280,800),(1536,864)]`, hash-selected per account for consistent fingerprint.
- **Residential proxy**: IPRoyal sticky sessions via `proxy_country` per account. Session ID derived from `account_id` hash for consistent IP.
- **playwright-stealth**: Applied on context launch (gracefully skipped if not installed).
- **`--disable-blink-features=AutomationControlled`**: Suppresses `navigator.webdriver`.

### Click Behaviour (`actions.py`)
- **`_hover_and_click()`**: All button activations hover first (80–350ms pause) before clicking. Triggers `mouseover`/`mousemove` events that real users always generate.
- **No DOM mutations**: JS finders use index-based `page.locator('button').nth(idx)` — no `data-*` attribute injection that LinkedIn's JS could observe.
- **Focus clicks stay direct**: `note_field.click()`, `msg_input.click()` are focus actions, not button activations — left as-is.
- **JS fallback**: `el.click()` only used when Playwright pointer events are intercepted by sticky nav bar.

### Browsing Noise (`noise.py`)
- **Back-scroll 15%**: Occasional upward scroll during feed browsing.
- **Horizontal drift**: `delta_x = random.randint(-3, 3)` — humans don't scroll perfectly vertically.
- **PageDown 25%**: Substitutes mouse wheel ~25% of forward scrolls.
- **Hover before like**: `btn.hover()` + 80–300ms pause before clicking Like.
- **Profile view back-scroll**: 15% chance per step to scroll back briefly.

### Session Health Model
**Reactive-first** — LinkedIn sessions last months when left alone. Do not ping proactively.

| Trigger | Action |
|---------|--------|
| Scheduler startup | `check_cookie_health()` once — catches pre-deploy expiry |
| Login "Save" clicked | Background `check_cookie_health()` — confirms proxy route healthy |
| 3 consecutive dispatch failures | Mark `cookie_expired` (primary detection mechanism) |
| Morning warm-up detects login redirect | Mark `cookie_expired` |
| `check_cookie_health` passes for expired account | Auto-recover to `active` |

**What NOT to do**: Never send bare HTTP requests with only `li_at` from the server IP. LinkedIn treats this as a stolen-cookie test and invalidates the session.

### Morning Warm-Up (`runner.py` → `keep_alive`)
- Runs once daily in the 6:30–9:30 AM window (CronTrigger jitter=5400s).
- Step 1: Feed scroll 15–35 seconds (most natural morning action).
- Step 2: One additional page — notifications, network, or messaging (random weighted).
- Detects session expiry via URL redirect check, not HTTP pre-check.

### Proxy Health Check (`_http_check_session`)
- Always routed through account's residential proxy (consistent IP = no location jump signal).
- Used only on startup + post-login, never on a recurring schedule.

## noVNC Login Flow
- `POST /api/v1/accounts/{id}/login-session` → starts ephemeral Xvfb + x11vnc + websockify on port 6080.
- User navigates to `http://SERVER_IP:6080/vnc.html`, logs in manually.
- `POST /api/v1/accounts/{id}/login-session/finish` → extracts cookies, copies browser profile, evicts stale pool slot, triggers background health check.
- noVNC is **ephemeral** — torn down after finish. "Disconnection" after saving is expected.

## Running
```bash
# Install
pip install -e ".[dev]"

# CLI
linauto account add --name nicolas --li-at <cookie>
linauto campaign create --name test --account nicolas
linauto campaign import --campaign test --csv leads.csv
linauto execute-once --campaign test --limit 3
linauto run  # Start scheduler daemon
```

## Testing
```bash
pytest                    # Run all tests
pytest tests/unit/        # Unit tests only
pytest -x                 # Stop on first failure
```
Note: pydantic-dependent tests (cooldown, planner, warmup) fail locally on ARM Mac due to x86 pydantic_core mismatch. Run in Docker for full suite.

## Database Migrations
```bash
alembic upgrade head      # Apply all migrations
alembic downgrade -1      # Rollback one migration
```
Migration files are in `alembic/versions/`. Follow the existing naming pattern (e.g., `003_add_campaign_filters.py`).

## Conventions
- **Python 3.9 compat**: `from __future__ import annotations` for function signatures, but use `Optional[X]` (not `X | None`) in SQLAlchemy `Mapped[]` type annotations.
- **NullPool**: Using `sqlalchemy.pool.NullPool` to avoid GC warnings in CLI context.
- **Deterministic seeds**: `hashlib.sha256(f"{account_id}-{date}")` for reproducible daily variation.
- **Fail open**: Safety checks (profile filters, noise) should fail open — don't block actions if detection is uncertain.
- **Selector resilience**: Each selector has primary + fallback entries. Try all before giving up.
- **Pool vs ephemeral**: Scheduler jobs use the persistent `BrowserPool`. `execute-once` CLI uses ephemeral browsers.
- **Fingerprint stability**: UA, viewport, proxy session ID are all hash-derived from `account_id` — same account always presents identical fingerprint across restarts.

## Phase Status
- Phase 1 (Foundation): COMPLETE — CLI, CSV import, template rendering, browser module
- Phase 2 (Scheduling & Safety): COMPLETE — Clustered planner, warmup, cooldown, APScheduler, stealth, noise, proxy/timezone
- Phase 3 (Follow-ups): IN PROGRESS — executor.execute_followup_sequence() implemented, dispatcher wired
- Phase 4 (Polish): NOT STARTED

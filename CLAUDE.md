# Linauto — LinkedIn Automation Tool

## Server
- **IP**: REDACTED
- **SSH**: `ssh root@REDACTED`
- **Docker container**: `linauto`
- **Live DB**: `/app/data/linauto.db` inside container
- **Query DB**: `docker exec linauto python3 -c "import sqlite3; ..."` (no sqlite3 binary in container)

## Deploying code changes
`src/` is volume-mounted from `/root/linauto/src` — **`git pull` on the server is all that's needed for Python code changes**. The running process reads source files directly from the host.

```bash
ssh root@REDACTED
cd /root/linauto && git pull   # changes are live immediately
```

Only rebuild the image when changing **dependencies** (`pyproject.toml`) or **`config.py`/`models.py`** (pydantic/SQLAlchemy schema changes). `docker-compose build` is broken (v1.29 incompatibility) — use `docker run` directly:
```bash
cd /root/linauto && git pull
docker-compose build
docker stop linauto && docker rm linauto
docker run -d --name linauto --restart unless-stopped \
  -v /root/linauto/data:/app/data \
  -v /root/linauto/config/settings.yaml:/app/config/settings.yaml:ro \
  -v /root/linauto/src:/app/src \
  -p 8000:8000 -p 6080:6080 \
  -e LINAUTO_API_ENABLED=true \
  -e LINAUTO_API_KEY=REDACTED \
  -e "LINAUTO_CORS_ORIGINS=[\"*\"]" \
  -e LINAUTO_LOG_LEVEL=INFO -e TZ=Europe/Berlin \
  --memory=3g --cpus=1.5 \
  linauto_linauto:latest
```

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
- `src/linauto/campaign/importer.py` — CSV import. Handles `name`/`full_name` columns (splits into first/last) and `project`/`project_name` columns (maps to company). Add new column aliases to `_COLUMN_MAP` or `_FULL_NAME_COLUMNS` here.

## Scheduler Jobs (6 total)
| Job | Schedule | Purpose |
|-----|----------|---------|
| `daily_planner` | 06:00 daily | Assign scheduled_at to pending leads |
| `dispatcher` | Every 5 min | Execute due connection requests |
| `acceptance_checker` | 10:00 daily | Detect accepted connections via invitation manager diff |
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
| Pre-dispatch browser feed ping fails (redirect) | Mark `cookie_expired` immediately, before any lead is attempted |
| Pre-dispatch browser feed ping times out | Skip cycle (proxy/network issue), do NOT mark `cookie_expired` |
| 3 consecutive session errors (non-network) | Mark `cookie_expired` as last resort |
| 3 consecutive network/timeout errors | Log `proxy_connectivity_issues`, skip cycle, do NOT mark `cookie_expired` |
| Morning warm-up detects login redirect | Mark `cookie_expired` |
| Acceptance checker invitation manager → session invalid | Mark `cookie_expired` |
| `check_cookie_health` passes for expired account | Auto-recover to `active` |

**What NOT to do**: Never send bare HTTP requests with only `li_at` from the server IP. LinkedIn treats this as a stolen-cookie test and invalidates the session.

**Failure mode separation** (`executor.py` → `_is_network_error()`): Navigation timeouts and proxy errors set `result["network_error"]=True` and are tracked separately from session errors. Only session errors (non-network) count toward cookie expiry detection.

### Acceptance Checker (`runner.py` → `check_acceptances`)
- Runs **once daily at 10:00** (`acceptance_check_hour` setting, default 10).
- Loads the LinkedIn invitation manager page **once** per account.
- Extracts all pending sent invitation URLs via a single JS evaluation (no per-profile visits for the check itself).
- **Diffs** against `CONNECTION_REQUESTED` leads in DB: leads missing from the pending list have either accepted or declined.
- Only visits profiles of disappeared leads to confirm status (~new acceptances per day, not all pending).
- `"connected"` → mark `CONNECTED`, schedule follow-up.
- `"not_connected"` → confirmed declined/expired → mark `WITHDRAWN`. Includes Creator-mode profiles showing "Follow" instead of "Connect".
- `"unknown"` → profile navigation failed (network/proxy error) → leave as `CONNECTION_REQUESTED`, retry tomorrow.
- `check_connection_status` logic: successful page load + no 1st-degree badge = `"not_connected"`. `"unknown"` only when navigation fails. This handles LinkedIn Creator profiles (Follow-primary, no Connect button).
- Manual testing: `linauto check-acceptances --account "Name" [--dry-run]` — uses ephemeral browser, safe to run while scheduler is active (no BrowserPool conflict).
- Withdrawal check runs on the same already-loaded page (no second navigation).
- Cost: ~1–2 MB/day (1 invitation manager page + a few profile visits) vs. old approach (~240 MB/day).

### Morning Warm-Up (`runner.py` → `keep_alive`)
- Runs once daily in the 6:30–9:30 AM window (CronTrigger jitter=5400s).
- Step 1: Feed scroll 15–35 seconds (most natural morning action).
- Step 2: One additional page — notifications, network, or messaging (random weighted).
- Detects session expiry via URL redirect check, not HTTP pre-check.

### Proxy Health Check (`_http_check_session`)
- Always routed through account's residential proxy (consistent IP = no location jump signal).
- Used only on startup + post-login, never on a recurring schedule.
- Uses lightweight `httpx` — confirms cookie validity but NOT browser-level connectivity. A passing HTTP check does not guarantee Playwright navigation will succeed.

### Navigation Timeouts (`navigator.py`)
- All `page.goto()` calls use `wait_until="domcontentloaded"` (not `"load"`) and 15s timeout.
- `domcontentloaded` fires immediately on a /login redirect → expired session detected in <2s instead of a 30s timeout that masks the root cause.
- Profile content rendering is handled separately by `_wait_for_profile_rendered()` after session is confirmed valid.

### Proxy Bandwidth Budget (1 account, 40 leads/day)
After all optimizations (resource blocking, DB pre-check, validation cooldown, invitation manager diff):

| Component | MB/day |
|-----------|--------|
| Connection requests (actual work, 40 leads × 2 pages × ~1 MB) | ~80 |
| Dispatch feed pre-check (max 6× per 30-min cooldown window) | ~6 |
| Acceptance checker (1 invitation manager page + new acceptances) | ~2–10 |
| Morning keep-alive (2 pages) | ~3 |
| **Total** | **~90–100 MB/day → ~3 GB/month** |

The ~3 GB/month floor is essentially irreducible — it's the cost of actually navigating to 40 LinkedIn profiles per day. Any further reduction would require LinkedIn API access.

**What NOT to do to reduce bandwidth further**: do not increase `_VALIDATION_COOLDOWN` beyond 30 min or reduce the acceptance_check to less than daily — you'd miss accepted connections and delay follow-ups.

### Playwright Proxy Credentials
- **Always use separate `username`/`password` fields** — Playwright/Chromium silently ignores credentials embedded in the server URL string (`http://user:pass@host:port`). The browser code in `browser.py` parses the URL and splits them out; do not revert this.
- httpx (`_http_check_session`, `check-connection` endpoint) uses the URL format fine — only Playwright needs the split.

### Proxy Sticky Sessions (`_build_proxy_url`)
- Session ID is `sha256(f"{account_id}-{year}-w{isoweek}")[:12]` — **rotates every Monday**. This prevents being permanently stuck on a dead/slow residential IP; worst case is one bad week.
- Do not remove the week component. The old fixed hash caused Italy proxy failures for 10+ days with no self-healing.

### Proxy Location Notes
- **Avoid city-level specificity** — use `ca` not `ca-montreal`. City filtering shrinks the IP pool and IPRoyal often returns 502 when no city IP is available for the session. Country-level is always preferred.
- **Avoid Greece (`gr`)**: residential IPs there are too slow for browser-grade traffic (Playwright timeouts even with valid session). Use `it`, `de`, `nl`, `fr`, or `es` for Southern/Central European accounts.
- **Mac UA markets**: `{us, ca, gb, au, nz, ie}` — all others get Windows UA. Account for this when choosing proxy country if the login browser UA matters.

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

## Auto-Withdraw (currently disabled)
The auto-withdraw feature is **fully implemented but disabled**. It withdraws the N oldest pending invitations per daily acceptance check run when the pending count exceeds a threshold.

To enable for an account:
```bash
# Set threshold (e.g. 300 = withdraw oldest invitations when pending > 300, 10/day)
docker exec linauto python3 -c "
import sqlite3; c=sqlite3.connect('/app/data/linauto.db')
c.execute(\"UPDATE accounts SET withdraw_threshold=300 WHERE name='Nicolas Goehler'\")
c.commit(); c.close()
"
```

To disable again (set back to NULL):
```bash
docker exec linauto python3 -c "
import sqlite3; c=sqlite3.connect('/app/data/linauto.db')
c.execute(\"UPDATE accounts SET withdraw_threshold=NULL WHERE name='Nicolas Goehler'\")
c.commit(); c.close()
"
```

Context: ~510 pre-system invitations are sitting in LinkedIn. Setting threshold=300 would clean them up at 10/day over ~21 days. The withdrawal happens on the already-loaded invitation manager page (no extra navigation cost). Code is in `runner.py` → `check_acceptances()`, `actions.py` → `withdraw_oldest_invitations()`.

## Phase Status
- Phase 1 (Foundation): COMPLETE — CLI, CSV import, template rendering, browser module
- Phase 2 (Scheduling & Safety): COMPLETE — Clustered planner, warmup, cooldown, APScheduler, stealth, noise, proxy/timezone
- Phase 3 (Follow-ups): IN PROGRESS — executor.execute_followup_sequence() implemented, dispatcher wired
- Phase 4 (Polish): NOT STARTED

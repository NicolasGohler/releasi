# Linauto — LinkedIn Automation Tool

## Overview
Dripify alternative. Automates LinkedIn connection requests and follow-up messages with safety-first design (warmup ramps, cooldowns, rate limits, stealth browsing).

## Tech Stack
- **Python 3.9+** (system has 3.9.6 — use `Optional[X]` not `X | None` in SQLAlchemy `Mapped[]`)
- **Playwright** (async) + playwright-stealth for browser automation
- **SQLAlchemy 2.0** async ORM + aiosqlite (SQLite)
- **Typer** CLI + Rich for terminal output
- **APScheduler** for daemon scheduling
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
│   ├── actions.py          # Atomic actions (connect, message)
│   ├── browser.py          # Playwright browser lifecycle
│   ├── detector.py         # Limit/CAPTCHA/session detection
│   ├── navigator.py        # Page navigation
│   ├── noise.py            # Feed scroll, likes, profile views
│   ├── profile_filter.py   # Live profile quality filtering
│   └── selectors.py        # ALL LinkedIn DOM selectors (update here when LinkedIn changes)
├── safety/
│   ├── cooldown.py         # Smart Monday-retry cooldown
│   ├── delays.py           # Human-like delays
│   └── limits.py           # Rate tracking
└── scheduler/
    ├── planner.py          # Clustered daily plan generation
    ├── runner.py           # APScheduler daemon (4 jobs)
    └── warmup.py           # Warmup ramp with daily variation
```

## Key Files
- `src/linauto/linkedin/selectors.py` — ALL LinkedIn DOM selectors. Update here when LinkedIn changes their UI.
- `src/linauto/db/models.py` — SQLAlchemy models. Use `Optional[X]` (not `X | None`) for Mapped[] annotations.
- `config/settings.yaml.example` — Reference config with all available settings.

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

## Phase Status
- Phase 1 (Foundation): COMPLETE — CLI, CSV import, template rendering, browser module
- Phase 2 (Scheduling & Safety): COMPLETE — Clustered planner, warmup, cooldown, APScheduler, stealth, noise, proxy/timezone
- Phase 3 (Follow-ups): NOT STARTED
- Phase 4 (Polish): NOT STARTED

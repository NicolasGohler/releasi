"""Warm-up ramp with deterministic daily variation."""
from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Optional

from linauto.config import get_settings


def _deterministic_seed(account_id: str, day: date) -> int:
    """Create a deterministic seed from account + date for reproducible daily variation."""
    key = f"{account_id}-{day.isoformat()}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def get_daily_target(
    account_id: str,
    day: date,
    warmup_start: date,
    schedule: Optional[list] = None,
) -> Optional[int]:
    """
    Get today's connection request target with deterministic daily variation.

    Returns:
        int: target for today during warmup weeks
        None: no cap (post-warmup — run until LinkedIn blocks)
    """
    if schedule is None:
        schedule = get_settings().warmup_schedule

    weeks_active = (day - warmup_start).days // 7

    if weeks_active < 0:
        # Before warmup started (shouldn't happen, but be safe)
        return 0

    if weeks_active < len(schedule):
        min_day, max_day = schedule[weeks_active]
    else:
        return None  # Post-warmup: no artificial cap

    # Deterministic random for same account+date
    rng = random.Random(_deterministic_seed(account_id, day))
    return rng.randint(min_day, max_day)


def get_warmup_week(warmup_start: date, day: Optional[date] = None) -> int:
    """Get the current warmup week number (0-indexed)."""
    day = day or date.today()
    return max(0, (day - warmup_start).days // 7)


def is_warmup_complete(warmup_start: date, day: Optional[date] = None) -> bool:
    """Check if the warmup period is over."""
    schedule = get_settings().warmup_schedule
    week = get_warmup_week(warmup_start, day)
    return week >= len(schedule)

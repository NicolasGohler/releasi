"""Smart cooldown — Monday-start retry with daily push if still blocked."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from typing import Optional

import structlog

from linauto.config import get_settings

logger = structlog.get_logger()


def _next_weekday(start: date, weekday: int) -> date:
    """Find the next occurrence of a weekday (0=Monday)."""
    days_ahead = weekday - start.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)


def calculate_cooldown_resume(account_timezone: Optional[str] = None) -> datetime:
    """
    Calculate when to resume after a limit hit.

    Returns next Monday at a random hour between cooldown_resume_hour_min
    and cooldown_resume_hour_max.
    """
    settings = get_settings()
    today = date.today()

    next_monday = _next_weekday(today, 0)  # Monday = 0
    resume_hour = random.randint(
        settings.cooldown_resume_hour_min,
        settings.cooldown_resume_hour_max,
    )
    resume_minute = random.randint(0, 59)

    return datetime.combine(next_monday, time(resume_hour, resume_minute))


def push_cooldown_one_day(current_paused_until: datetime) -> datetime:
    """
    Push cooldown to tomorrow at same time when still blocked.

    Used when Monday arrives, we check, and LinkedIn is still blocking.
    """
    new_resume = current_paused_until + timedelta(days=1)
    # Add some jitter to the hour (±30 min)
    jitter_minutes = random.randint(-30, 30)
    new_resume += timedelta(minutes=jitter_minutes)

    # Clamp to reasonable hours (7am-12pm)
    if new_resume.hour < 7:
        new_resume = new_resume.replace(hour=random.randint(8, 10), minute=random.randint(0, 59))
    elif new_resume.hour > 12:
        new_resume = new_resume.replace(hour=random.randint(8, 11), minute=random.randint(0, 59))

    return new_resume


def is_cooldown_expired(paused_until: Optional[datetime]) -> bool:
    """Check if the cooldown period has passed."""
    if paused_until is None:
        return True  # Not paused
    return datetime.utcnow() >= paused_until

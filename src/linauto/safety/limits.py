"""Rate tracking — no artificial cap post-warmup, enforces warmup daily target."""
from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

import structlog

from linauto.scheduler.warmup import get_daily_target

logger = structlog.get_logger()


async def can_send_today(
    account_id: str,
    warmup_start: Optional[date],
    sent_today: int,
    warmup_schedule: Optional[list] = None,
) -> Tuple[bool, Optional[int]]:
    """
    Check if we can still send connection requests today.

    Returns:
        (can_send, remaining_today)
        remaining_today is None if post-warmup (no cap).
    """
    if warmup_start is None:
        # Warmup not enabled — no cap
        return True, None

    daily_target = get_daily_target(
        account_id, date.today(), warmup_start, warmup_schedule
    )

    if daily_target is None:
        # Post-warmup: no artificial cap
        return True, None

    remaining = daily_target - sent_today
    can_send = remaining > 0

    if not can_send:
        logger.info(
            "limits.daily_target_reached",
            account_id=account_id,
            target=daily_target,
            sent=sent_today,
        )

    return can_send, max(0, remaining)

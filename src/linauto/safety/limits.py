"""Rate tracking — enforces daily_limit as a target with natural variation."""
from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Tuple

import structlog

logger = structlog.get_logger()


def _daily_target(account_id: str, daily_limit: int) -> int:
    """Apply ±15% deterministic variation to daily_limit."""
    key = f"{account_id}-{date.today().isoformat()}"
    seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    variation = max(1, int(daily_limit * 0.15))
    return max(1, rng.randint(daily_limit - variation, daily_limit + variation))


async def can_send_today(
    account_id: str,
    daily_limit: int,
    sent_today: int,
) -> Tuple[bool, int]:
    """
    Check if we can still send connection requests today.

    Returns:
        (can_send, remaining_today)
    """
    target = _daily_target(account_id, daily_limit)
    remaining = target - sent_today
    can_send = remaining > 0

    if not can_send:
        logger.info(
            "limits.daily_target_reached",
            account_id=account_id,
            target=target,
            sent=sent_today,
        )

    return can_send, max(0, remaining)

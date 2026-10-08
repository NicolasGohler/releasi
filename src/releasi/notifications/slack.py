"""Slack notifications — direct internet, never routed through proxy."""
from __future__ import annotations

import structlog
import httpx
from typing import Optional
from releasi.config import get_settings

logger = structlog.get_logger()


async def notify(text: str, *, bot_token: Optional[str] = None, channel: Optional[str] = None) -> bool:
    """Send to the default user or an explicitly supplied configured destination."""
    settings = get_settings()
    token = bot_token if bot_token is not None else settings.slack_bot_token
    destination = channel if channel is not None else settings.slack_user_id
    if not token or not destination:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": destination, "text": text},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("slack.notify_failed", error=data.get("error"))
                return False
            return True
    except Exception as e:
        logger.warning("slack.notify_error", error=str(e))
        return False

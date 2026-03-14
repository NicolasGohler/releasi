"""Slack DM notifications — direct internet, never routed through proxy."""
from __future__ import annotations

import structlog
import httpx
from linauto.config import get_settings

logger = structlog.get_logger()


async def notify(text: str) -> None:
    """Send a Slack DM to the configured user. Fire-and-forget."""
    settings = get_settings()
    if not settings.slack_bot_token or not settings.slack_user_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                json={"channel": settings.slack_user_id, "text": text},
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("slack.notify_failed", error=data.get("error"))
    except Exception as e:
        logger.warning("slack.notify_error", error=str(e))

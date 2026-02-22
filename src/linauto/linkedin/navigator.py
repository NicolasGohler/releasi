"""Page navigation helpers with safety checks."""
from __future__ import annotations

import random
import asyncio
from dataclasses import dataclass
from typing import Optional

import structlog
from playwright.async_api import Page

from linauto.config import get_settings
from linauto.linkedin.selectors import (
    FEED_URL, LOGIN_URL_PATTERNS, INVITATION_MANAGER_URL,
)

logger = structlog.get_logger()


@dataclass
class NavigationResult:
    success: bool
    url: str
    session_valid: bool = True
    error: Optional[str] = None


class LinkedInNavigator:
    """Handles all page navigation with human-like delays and safety checks."""

    def __init__(self, page: Page):
        self.page = page
        self._settings = get_settings()

    async def _random_delay(self, min_s: Optional[float] = None, max_s: Optional[float] = None):
        """Wait a random amount of time to simulate human behavior."""
        lo = min_s or self._settings.page_load_delay_min
        hi = max_s or self._settings.page_load_delay_max
        delay = random.uniform(lo, hi)
        await asyncio.sleep(delay)

    def _check_session(self, url: str) -> bool:
        """Check if the current URL indicates we're still logged in."""
        for pattern in LOGIN_URL_PATTERNS:
            if pattern in url:
                return False
        return True

    async def go_to_profile(self, profile_url: str) -> NavigationResult:
        """Navigate to a LinkedIn profile page."""
        try:
            await self._random_delay(0.5, 2.0)
            await self.page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            await self._random_delay()

            session_valid = self._check_session(self.page.url)
            if not session_valid:
                logger.warning("navigator.session_expired", target=profile_url)

            return NavigationResult(
                success=True, url=self.page.url, session_valid=session_valid
            )
        except Exception as e:
            logger.error("navigator.profile_failed", url=profile_url, error=str(e))
            return NavigationResult(
                success=False, url=profile_url, error=str(e)
            )

    async def go_to_feed(self) -> NavigationResult:
        """Navigate to LinkedIn feed (useful for session validation)."""
        try:
            await self.page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30000)
            await self._random_delay()
            return NavigationResult(
                success=True,
                url=self.page.url,
                session_valid=self._check_session(self.page.url),
            )
        except Exception as e:
            return NavigationResult(success=False, url=FEED_URL, error=str(e))

    async def go_to_invitation_manager(self) -> NavigationResult:
        """Navigate to the sent invitations page."""
        try:
            await self._random_delay(0.5, 2.0)
            await self.page.goto(
                INVITATION_MANAGER_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await self._random_delay()
            return NavigationResult(
                success=True,
                url=self.page.url,
                session_valid=self._check_session(self.page.url),
            )
        except Exception as e:
            return NavigationResult(
                success=False, url=INVITATION_MANAGER_URL, error=str(e)
            )

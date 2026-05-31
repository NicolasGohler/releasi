"""Detect LinkedIn safety signals: limits, CAPTCHAs, session expiry."""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

import structlog
from playwright.async_api import Page

from releasi.linkedin.selectors import (
    WEEKLY_LIMIT_BANNERS, RATE_LIMIT_MODAL,
    CAPTCHA_INDICATORS, LOGIN_URL_PATTERNS,
)

logger = structlog.get_logger()


class DetectionType(str, enum.Enum):
    NONE = "none"
    WEEKLY_LIMIT = "weekly_limit"
    RATE_LIMIT_MODAL = "rate_limit_modal"
    CAPTCHA = "captcha"
    SESSION_EXPIRED = "session_expired"


@dataclass
class DetectionResult:
    detected: DetectionType = DetectionType.NONE
    details: Optional[str] = None

    @property
    def is_clear(self) -> bool:
        return self.detected == DetectionType.NONE

    @property
    def requires_cooldown(self) -> bool:
        return self.detected in (
            DetectionType.WEEKLY_LIMIT,
            DetectionType.RATE_LIMIT_MODAL,
        )

    @property
    def requires_manual_intervention(self) -> bool:
        return self.detected in (
            DetectionType.CAPTCHA,
            DetectionType.SESSION_EXPIRED,
        )


class LimitDetector:
    """Detects LinkedIn safety signals on the current page."""

    async def _try_selectors(self, page: Page, selectors: list, timeout_ms: int = 2000) -> bool:
        """Try a list of selectors, return True if any match."""
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count > 0:
                    return True
            except Exception:
                continue
        return False

    async def check_after_action(self, page: Page) -> DetectionResult:
        """Run all detection checks. Call after every LinkedIn action."""
        # Check session first (cheapest check)
        if not self._is_session_valid(page):
            logger.warning("detector.session_expired")
            return DetectionResult(
                detected=DetectionType.SESSION_EXPIRED,
                details="Redirected to login page",
            )

        # Check for CAPTCHA
        if await self._try_selectors(page, CAPTCHA_INDICATORS):
            logger.warning("detector.captcha_detected")
            return DetectionResult(
                detected=DetectionType.CAPTCHA,
                details="CAPTCHA challenge detected",
            )

        # Check for weekly limit banner
        if await self._try_selectors(page, WEEKLY_LIMIT_BANNERS):
            logger.warning("detector.weekly_limit_reached")
            return DetectionResult(
                detected=DetectionType.WEEKLY_LIMIT,
                details="Weekly invitation limit banner detected",
            )

        # Check for rate limit modal
        if await self._try_selectors(page, RATE_LIMIT_MODAL):
            logger.warning("detector.rate_limit_modal")
            return DetectionResult(
                detected=DetectionType.RATE_LIMIT_MODAL,
                details="Rate limit modal detected",
            )

        return DetectionResult()

    async def is_weekly_limit_reached(self, page: Page) -> bool:
        return await self._try_selectors(page, WEEKLY_LIMIT_BANNERS)

    async def is_captcha_present(self, page: Page) -> bool:
        return await self._try_selectors(page, CAPTCHA_INDICATORS)

    def _is_session_valid(self, page: Page) -> bool:
        url = page.url
        for pattern in LOGIN_URL_PATTERNS:
            if pattern in url:
                return False
        return True

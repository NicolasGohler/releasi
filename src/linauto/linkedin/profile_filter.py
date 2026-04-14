"""Live profile filtering: skip profiles that don't meet quality criteria."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import structlog
from playwright.async_api import Page

from linauto.linkedin import selectors

logger = structlog.get_logger()


@dataclass
class ProfileFilters:
    """Per-campaign filter configuration."""
    no_photo: bool = False
    min_connections: Optional[int] = None

    @property
    def any_enabled(self) -> bool:
        return self.no_photo or self.min_connections is not None


class ProfileFilter:
    """Check a loaded LinkedIn profile page against filter criteria."""

    async def check(self, page: Page, filters: ProfileFilters) -> Optional[str]:
        """
        Returns a skip reason string if the profile should be filtered out,
        or None if the profile passes all filters.
        """
        if filters.no_photo and not await self._has_photo(page):
            logger.info("profile_filter.no_photo")
            return "filter_no_photo"

        if filters.min_connections is not None:
            count = await self._get_connection_count(page)
            if count is None:
                # Fail open: selector couldn't read the count (LinkedIn DOM change
                # or restricted profile visibility). Don't block the lead — per
                # CLAUDE.md "Safety checks should fail open".
                logger.info("profile_filter.connections_unknown_fail_open", min=filters.min_connections)
                return None
            if count < filters.min_connections:
                logger.info("profile_filter.low_connections", count=count, min=filters.min_connections)
                return f"filter_low_connections:{count}"

        return None

    async def _has_photo(self, page: Page) -> bool:
        """Check whether the profile has a real photo (not a placeholder)."""
        # First check for ghost/placeholder indicators (definitive no-photo)
        for sel in selectors.PROFILE_NO_PHOTO:
            try:
                if await page.locator(sel).count() > 0:
                    return False
            except Exception:
                continue

        # Then check for real photo element
        for sel in selectors.PROFILE_PHOTO:
            try:
                if await page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue

        # If neither found, assume photo exists (fail open)
        return True

    async def _get_connection_count(self, page: Page) -> Optional[int]:
        """
        Extract the connection count from the profile page.
        Parses text like "500+", "127", "1,234" into an integer.
        Returns None if the count can't be determined.
        """
        for sel in selectors.PROFILE_CONNECTION_COUNT:
            try:
                locator = page.locator(sel).first
                if await locator.count() == 0:
                    continue
                text = (await locator.text_content() or "").strip()
                if text:
                    return self._parse_count(text)
            except Exception:
                continue
        return None

    @staticmethod
    def _parse_count(text: str) -> Optional[int]:
        """Parse connection count text: '500+' -> 500, '1,234' -> 1234."""
        cleaned = text.replace(",", "").replace("+", "").strip()
        match = re.search(r"\d+", cleaned)
        if match:
            return int(match.group())
        return None

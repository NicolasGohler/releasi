"""Live profile filtering: skip profiles that don't meet quality criteria."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import structlog
from playwright.async_api import Page

from releasi.linkedin import selectors

logger = structlog.get_logger()


@dataclass
class ProfileFilters:
    """Per-campaign filter configuration."""
    no_photo: bool = False
    min_connections: Optional[int] = None
    exclude_open_to_work: bool = False

    @property
    def any_enabled(self) -> bool:
        return self.no_photo or self.min_connections is not None or self.exclude_open_to_work


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

        if filters.exclude_open_to_work and await self._is_open_to_work(page):
            logger.info("profile_filter.open_to_work")
            return "filter_open_to_work"

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

        LinkedIn now uses obfuscated CSS class names that change with each
        deploy, so CSS selectors are unreliable. Primary strategy is JS
        content-matching: find the element labelled "connections" and read
        the number from its parent's text. CSS selectors are tried as a
        fallback for any older LinkedIn layouts still in the wild.
        """
        # Primary: JS content-based search (robust against obfuscated classes)
        try:
            result = await page.evaluate("""
                () => {
                    // Find any <p> or <span> whose trimmed text is exactly
                    // "connections" or "connection", then read the count from
                    // the parent element's full text (LinkedIn renders the
                    // number as a bare text node alongside the label child).
                    const labels = document.querySelectorAll('p, span');
                    for (const el of labels) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (t !== 'connections' && t !== 'connection') continue;
                        let cur = el.parentElement;
                        for (let i = 0; i < 3 && cur; i++) {
                            const full = (cur.innerText || cur.textContent || '').trim();
                            const m = full.match(/^([\d,]+)\+?\s*connections?$/i);
                            if (m) return m[1].replace(/,/g, '');
                            cur = cur.parentElement;
                        }
                    }
                    return null;
                }
            """)
            if result is not None:
                count = self._parse_count(str(result))
                if count is not None:
                    return count
        except Exception:
            pass

        # Fallback: CSS selectors (may work on older/cached LinkedIn layouts)
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

    async def _is_open_to_work(self, page: Page) -> bool:
        """
        Detect the LinkedIn "Open to Work" badge on a profile page.

        Primary: JS content scan — find any element whose trimmed text is exactly
        "Open to work" (case-insensitive). Robust against obfuscated class names.
        Fallback: img alt attribute and aria-label attribute selectors.
        Fails open (returns False) if detection is uncertain.
        """
        try:
            found = await page.evaluate("""
                () => {
                    // Badge text in the profile intro section
                    const els = document.querySelectorAll('div, span, p');
                    for (const el of els) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (t === 'open to work') return true;
                    }
                    return false;
                }
            """)
            if found:
                return True
        except Exception:
            pass

        # Fallback: profile photo alt text includes "open to work"
        for sel in selectors.OPEN_TO_WORK:
            try:
                if await page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue

        return False

    @staticmethod
    def _parse_count(text: str) -> Optional[int]:
        """Parse connection count text: '500+' -> 500, '1,234' -> 1234."""
        cleaned = text.replace(",", "").replace("+", "").strip()
        match = re.search(r"\d+", cleaned)
        if match:
            return int(match.group())
        return None

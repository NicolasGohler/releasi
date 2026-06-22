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
        # Primary: JS content-based search (robust against obfuscated classes).
        #
        # CRITICAL: the count must come from the actual "<N>+ connections" label,
        # NOT the mutual-connections line ("Matt, Frank and 45 other mutual
        # connections"). LinkedIn renders the real count as a single element
        # whose text is exactly "500+ connections" — the number and the word
        # are no longer in separate child nodes, so matching an exact
        # "connections" label and reading the parent no longer works.
        #
        # We scan every element and match the anchored pattern
        # ^<digits>+? connections$ against its OWN text. The anchor guarantees
        # the text starts with the number, so the mutual line (which starts
        # with names) can never match. We additionally skip anything containing
        # "mutual" as a belt-and-suspenders guard.
        try:
            result = await page.evaluate(r"""
                () => {
                    const els = document.querySelectorAll('a, span, p, li');
                    for (const el of els) {
                        const t = (el.innerText || el.textContent || '').trim();
                        if (/mutual/i.test(t)) continue;
                        const m = t.match(/^([\d,]+)\+?\s*connections?$/i);
                        if (m) return m[1].replace(/,/g, '');
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

        # Fallback: CSS selectors (may work on older/cached LinkedIn layouts).
        # Guard against the mutual-connections line: the substring-matching
        # `span:has-text("connections")` selector also matches "... mutual
        # connections", so we require the text to anchor on the count and
        # reject anything mentioning "mutual" before parsing.
        for sel in selectors.PROFILE_CONNECTION_COUNT:
            try:
                for locator in await page.locator(sel).all():
                    text = (await locator.text_content() or "").strip()
                    if not text or "mutual" in text.lower():
                        continue
                    if re.match(r"^[\d,]+\+?\s*connections?$", text, re.I):
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

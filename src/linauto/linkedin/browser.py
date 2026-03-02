"""Playwright browser lifecycle management per LinkedIn account."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import async_playwright, BrowserContext, Playwright

from linauto.config import get_settings
from linauto.linkedin.selectors import FEED_URL, LOGIN_URL_PATTERNS, NAV_AVATAR

logger = structlog.get_logger()

# Realistic user agents (updated periodically)
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Timezone to locale mapping for natural browser fingerprint
_TIMEZONE_LOCALE_MAP = {
    "Europe/Berlin": "de-DE",
    "Europe/London": "en-GB",
    "Europe/Paris": "fr-FR",
    "Europe/Amsterdam": "nl-NL",
    "Europe/Zurich": "de-CH",
    "Europe/Vienna": "de-AT",
    "Europe/Madrid": "es-ES",
    "Europe/Rome": "it-IT",
    "America/New_York": "en-US",
    "America/Chicago": "en-US",
    "America/Denver": "en-US",
    "America/Los_Angeles": "en-US",
    "America/Toronto": "en-CA",
    "America/Sao_Paulo": "pt-BR",
    "Asia/Tokyo": "ja-JP",
    "Asia/Shanghai": "zh-CN",
    "Asia/Singapore": "en-SG",
    "Asia/Dubai": "en-AE",
    "Australia/Sydney": "en-AU",
}


def _timezone_to_locale(timezone_str: Optional[str]) -> str:
    """Always use en-US. LinkedIn UI language is controlled by account settings,
    not browser locale, so non-English locales just break our selectors."""
    return "en-US"


class LinkedInBrowser:
    """Manages Playwright browser instances per LinkedIn account."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None

    async def launch(
        self,
        account_id: str,
        li_at_cookie: str,
        user_agent: Optional[str] = None,
        proxy_url: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> BrowserContext:
        """
        Launch a persistent Playwright Chromium browser context.
        Injects the li_at session cookie for authentication.
        Optionally applies stealth patches, proxy, and timezone.
        """
        settings = get_settings()

        # Ensure browser data directory exists
        user_data_dir = Path("data/browser_data") / account_id
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()

        ua = user_agent or random.choice(_USER_AGENTS)
        tz = timezone or settings.default_timezone
        locale = _timezone_to_locale(tz)

        # Build context kwargs
        context_kwargs = dict(
            user_data_dir=str(user_data_dir),
            headless=settings.browser_headless,
            viewport={
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            },
            locale=locale,
            timezone_id=tz,
            user_agent=ua,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        # Add proxy if provided
        if proxy_url:
            context_kwargs["proxy"] = {"server": proxy_url}
            logger.info("browser.proxy_configured", proxy=proxy_url.split("@")[-1])

        self._context = await self._playwright.chromium.launch_persistent_context(
            **context_kwargs
        )

        # Apply stealth patches
        if settings.stealth_enabled:
            try:
                from playwright_stealth import Stealth
                stealth = Stealth()
                await stealth.apply_stealth_async(self._context)
                logger.info("browser.stealth_applied")
            except ImportError:
                logger.warning("browser.stealth_not_installed", hint="pip install playwright-stealth")

        # Inject LinkedIn session cookie
        await self._context.add_cookies([
            {
                "name": "li_at",
                "value": li_at_cookie,
                "domain": ".linkedin.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ])

        logger.info(
            "browser.launched",
            account_id=account_id,
            headless=settings.browser_headless,
            timezone=tz,
            locale=locale,
        )
        return self._context

    async def validate_session(self) -> bool:
        """Navigate to LinkedIn feed and check if we're logged in.

        Also scrapes the profile photo URL from the nav bar if available.
        """
        if not self._context:
            return False

        self._avatar_url: Optional[str] = None
        page = await self._context.new_page()
        try:
            await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30000)
            current_url = page.url

            for pattern in LOGIN_URL_PATTERNS:
                if pattern in current_url:
                    logger.warning("session.expired", url=current_url)
                    return False

            # Wait for page to fully render before scraping avatar
            try:
                await page.wait_for_load_state("load", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            # Try to scrape profile photo from nav bar
            try:
                self._avatar_url = await self._scrape_nav_avatar(page)
            except Exception:
                pass

            logger.info("session.valid")
            return True
        except Exception as e:
            logger.error("session.validation_failed", error=str(e))
            return False
        finally:
            await page.close()

    async def _scrape_nav_avatar(self, page) -> Optional[str]:
        """Extract profile photo URL from the LinkedIn nav bar."""
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        # Try CSS selectors first
        for sel in NAV_AVATAR:
            try:
                img = page.locator(sel).first
                await img.wait_for(state="visible", timeout=3000)
                src = await img.get_attribute("src")
                if src and src.startswith("http"):
                    return src
            except (PlaywrightTimeout, Exception):
                continue
        # Fallback: JS evaluation to find nav profile image
        try:
            src = await page.evaluate("""() => {
                // Try nav bar Me button area
                const nav = document.querySelector('.global-nav__me')
                          || document.querySelector('[data-test-global-nav-me]')
                          || document.querySelector('.global-nav');
                if (nav) {
                    const img = nav.querySelector('img');
                    if (img && img.src && img.src.startsWith('http')) return img.src;
                }
                // Try any small circular profile image in the header
                const imgs = document.querySelectorAll('nav img, header img, .global-nav img');
                for (const img of imgs) {
                    if (img.src && img.src.startsWith('http') && img.naturalWidth > 0) return img.src;
                }
                return null;
            }""")
            if src:
                return src
        except Exception:
            pass
        return None

    async def quick_check_session(self) -> dict:
        """Fast session check using a fresh ephemeral context.

        Uses a clean browser context (no cached data) to truly test whether
        the li_at cookie alone is valid. Returns dict with valid, title, etc.
        """
        import time
        start = time.monotonic()

        if not self._playwright:
            return {"valid": False, "error": "no playwright instance", "elapsed_ms": 0}

        settings = get_settings()
        browser = await self._playwright.chromium.launch(
            headless=settings.browser_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context()

        # Inject only the li_at cookie — no cached state
        cookies = await self._context.cookies() if self._context else []
        li_at_cookie = next((c for c in cookies if c["name"] == "li_at"), None)
        if li_at_cookie:
            await context.add_cookies([li_at_cookie])
        else:
            await browser.close()
            return {"valid": False, "error": "no li_at cookie found", "elapsed_ms": 0}

        page = await context.new_page()
        try:
            await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=15000)
            current_url = page.url

            for pattern in LOGIN_URL_PATTERNS:
                if pattern in current_url:
                    elapsed = int((time.monotonic() - start) * 1000)
                    return {"valid": False, "reason": "redirected_to_login", "url": current_url, "elapsed_ms": elapsed}

            title = await page.title()
            has_content = await page.evaluate("""() => {
                return document.body && document.body.innerHTML.length > 1000;
            }""")

            elapsed = int((time.monotonic() - start) * 1000)
            return {
                "valid": True,
                "title": title,
                "has_content": has_content,
                "url": current_url,
                "elapsed_ms": elapsed,
            }
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return {"valid": False, "error": str(e), "elapsed_ms": elapsed}
        finally:
            await page.close()
            await context.close()
            await browser.close()

    def get_avatar_url(self) -> Optional[str]:
        """Return the avatar URL scraped during validate_session()."""
        return getattr(self, '_avatar_url', None)

    async def save_avatar(self, account_id: str) -> Optional[str]:
        """Download the scraped avatar and save to data/avatars/{account_id}.jpg."""
        url = self.get_avatar_url()
        if not url:
            return None

        import asyncio
        from urllib.request import urlopen, Request

        avatar_dir = Path("data/avatars")
        avatar_dir.mkdir(parents=True, exist_ok=True)
        path = avatar_dir / f"{account_id}.jpg"

        try:
            def _download():
                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        path.write_bytes(resp.read())
                        return True
                return False

            ok = await asyncio.get_event_loop().run_in_executor(None, _download)
            if ok:
                logger.info("avatar.saved", account_id=account_id, path=str(path))
                return str(path)
        except Exception as e:
            logger.warning("avatar.download_failed", error=str(e))
        return None

    async def new_page(self):
        """Get a new page from the browser context."""
        if not self._context:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return await self._context.new_page()

    async def close(self):
        """Clean shutdown of browser context and Playwright."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("browser.closed")

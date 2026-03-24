"""Local (macOS/desktop) headed browser login for LinkedIn accounts.

Launches a visible Chromium window so the user can log in manually.
No Xvfb or VNC required — works natively on macOS and desktop Linux.

After successful login the full browser profile is saved so the scheduler
can reuse it without any manual cookie pasting.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import async_playwright, BrowserContext, Playwright

logger = structlog.get_logger()

# How often to poll for a valid li_at cookie (seconds)
_POLL_INTERVAL = 2

# Timeout waiting for the user to log in (seconds). 10 minutes.
_LOGIN_TIMEOUT = 600


class LocalLoginSession:
    """
    Headed browser login flow for local (non-server) use.

    Usage:
        session = LocalLoginSession()
        result = await session.run(account_id, account_name)
        # result = {"li_at": "...", "profile_dir": Path(...)}
    """

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._temp_dir: Optional[str] = None

    async def run(
        self,
        account_id: str,
        account_name: str,
        proxy_country: Optional[str] = None,
    ) -> dict:
        """
        Open a headed Chromium window, wait for the user to log in to LinkedIn,
        then save the full browser profile and return the extracted cookies.

        proxy_country is used only for deterministic UA selection (same UA as the
        scheduler will use), NOT for routing — local runs always use the local IP.
        """
        from linauto.linkedin.browser import _deterministic_ua

        ua = _deterministic_ua(account_id, proxy_country)
        self._temp_dir = tempfile.mkdtemp(prefix=f"linauto_locallogin_{account_id}_")

        try:
            self._playwright = await async_playwright().start()
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self._temp_dir,
                headless=False,
                viewport={"width": 1280, "height": 800},
                user_agent=ua,
                locale="en-US",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )

            page = await self._context.new_page()
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            logger.info("local_login.browser_opened", account=account_name)

            # Poll for li_at cookie — appears once the user completes login
            li_at = await self._wait_for_login(page, account_name)

            if not li_at:
                return {"li_at": None, "profile_dir": None, "error": "timeout"}

            # Give the user time to accept any cookie consent popups before
            # we extract cookies and close the browser.
            logger.info("local_login.waiting_for_consent", account=account_name)
            print("\nLogin detected — accept any cookie popups now. Closing in 10 seconds...")
            await asyncio.sleep(10)

            # Extract all cookies before closing
            all_cookies = await self._context.cookies(["https://www.linkedin.com"])
            logger.info(
                "local_login.cookies_extracted",
                total=len(all_cookies),
                has_li_at=True,
            )

            # Close context so Chromium flushes profile to disk
            await self._context.close()
            self._context = None

            # Copy profile to the account's persistent browser_data directory
            dest = Path("data/browser_data") / account_id
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(self._temp_dir, str(dest))
            logger.info("local_login.profile_saved", dest=str(dest))

            return {"li_at": li_at, "profile_dir": dest, "error": None}

        finally:
            await self._cleanup()

    async def _wait_for_login(self, page, account_name: str) -> Optional[str]:
        """Poll until li_at appears in the cookie jar or timeout is reached."""
        elapsed = 0
        while elapsed < _LOGIN_TIMEOUT:
            try:
                cookies = await self._context.cookies(["https://www.linkedin.com"])
                for c in cookies:
                    if c["name"] == "li_at" and c["value"]:
                        # Extra check: make sure we landed on the feed, not still on login
                        try:
                            current_url = page.url
                        except Exception:
                            current_url = ""
                        if any(p in current_url for p in ["/login", "/uas/", "/signup", "/checkpoint"]):
                            # Still on auth pages — wait for redirect to feed
                            pass
                        else:
                            logger.info("local_login.login_detected", account=account_name)
                            return c["value"]
            except Exception:
                pass
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        return None

    async def _cleanup(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        if self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass
            self._temp_dir = None

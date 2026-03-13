"""Playwright browser lifecycle management per LinkedIn account."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import async_playwright, BrowserContext, Playwright

from linauto.config import get_settings
from linauto.linkedin.selectors import FEED_URL, LOGIN_URL_PATTERNS, NAV_AVATAR

logger = structlog.get_logger()

# User agents must match the actual Playwright Chromium binary version.
# Playwright 1.58.0 ships Chrome 145 — mismatching causes navigator.userAgentData
# to diverge from navigator.userAgent, which is a strong bot fingerprint.
# To update: run `chromium --version` inside the container and set to that major version.
_CHROMIUM_MAJOR = 145
_USER_AGENTS = [
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROMIUM_MAJOR}.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROMIUM_MAJOR}.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROMIUM_MAJOR}.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROMIUM_MAJOR}.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROMIUM_MAJOR}.0.0.0 Safari/537.36",
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROMIUM_MAJOR}.0.0.0 Safari/537.36",
]

# Matching Sec-CH-UA headers per platform (must stay in sync with _CHROMIUM_MAJOR)
_SEC_CH_UA_WINDOWS = f'"Chromium";v="{_CHROMIUM_MAJOR}", "Google Chrome";v="{_CHROMIUM_MAJOR}", "Not-A.Brand";v="99"'
_SEC_CH_UA_MAC = f'"Chromium";v="{_CHROMIUM_MAJOR}", "Google Chrome";v="{_CHROMIUM_MAJOR}", "Not-A.Brand";v="99"'

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


def _deterministic_ua(account_id: str, proxy_country: Optional[str] = None) -> str:
    """Pick a stable User-Agent per account using a hash-based index.

    Mac UAs are only selected for English-speaking markets (us, ca, gb, au, nz, ie).
    All other markets use Windows-only UAs to match regional browser distribution.
    This ensures the same account always presents the same browser fingerprint.
    """
    mac_markets = {"us", "ca", "gb", "au", "nz", "ie"}
    country_base = (proxy_country or "").split("-")[0].lower()
    if country_base in mac_markets:
        pool = _USER_AGENTS
    else:
        pool = [ua for ua in _USER_AGENTS if "Windows" in ua] or _USER_AGENTS
    idx = int(hashlib.sha256(account_id.encode()).hexdigest()[:8], 16) % len(pool)
    return pool[idx]


def _build_proxy_url(account_id: str, proxy_country: str) -> Optional[str]:
    """Auto-generate an IPRoyal proxy URL for an account.

    Uses the global proxy credentials from settings and the account's
    proxy_country.  Each account gets a deterministic sticky session ID
    derived from its account_id so it always lands on the same residential IP
    within a given week.

    Session IDs rotate every Monday (ISO week boundary) so that a dead or
    slow residential IP is abandoned automatically rather than being stuck
    indefinitely.  The 168h lifetime covers the full week.

    The proxy_country format is "{country}" or "{country}-{city}", e.g.
    "ca", "ca-montreal", "de-berlin", "es".
    """
    from datetime import date as _date
    settings = get_settings()
    if not settings.proxy_username or not settings.proxy_password:
        return None

    # Weekly-rotating session ID: same IP all week, fresh IP each Monday.
    year, week, _ = _date.today().isocalendar()
    session_id = hashlib.sha256(f"{account_id}-{year}-w{week}".encode()).hexdigest()[:12]

    # Parse country and optional city from proxy_country
    parts = proxy_country.split("-", 1)
    country_code = parts[0]
    city = parts[1] if len(parts) > 1 else None

    # Build password with IPRoyal parameters
    password_parts = [
        settings.proxy_password,
        f"country-{country_code}",
    ]
    if city:
        password_parts.append(f"city-{city}")
    password_parts.append(f"session-{session_id}")
    password_parts.append(f"lifetime-{settings.proxy_lifetime}")
    password = "_".join(password_parts)

    return (
        f"http://{settings.proxy_username}:{password}"
        f"@{settings.proxy_hostname}:{settings.proxy_port}"
    )


class LinkedInBrowser:
    """Manages Playwright browser instances per LinkedIn account."""

    def __init__(self, pool_managed: bool = False):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._pool_managed = pool_managed

    async def launch(
        self,
        account_id: str,
        li_at_cookie: str,
        user_agent: Optional[str] = None,
        proxy_url: Optional[str] = None,
        proxy_country: Optional[str] = None,
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

        ua = user_agent or _deterministic_ua(account_id, proxy_country)
        tz = timezone or settings.default_timezone
        locale = _timezone_to_locale(tz)

        # Deterministic viewport — consistent fingerprint per account, realistic sizes
        _VIEWPORTS = [(1366, 768), (1440, 900), (1920, 1080), (1280, 800), (1536, 864)]
        vp_idx = int(hashlib.sha256(account_id.encode()).hexdigest()[8:16], 16) % len(_VIEWPORTS)
        vp_w, vp_h = _VIEWPORTS[vp_idx]

        # Sec-CH-UA must match the UA to avoid Client Hints mismatch detection
        is_mac = "Macintosh" in ua
        sec_ch_ua = _SEC_CH_UA_MAC if is_mac else _SEC_CH_UA_WINDOWS
        sec_ch_ua_platform = '"macOS"' if is_mac else '"Windows"'

        # Build context kwargs
        context_kwargs = dict(
            user_data_dir=str(user_data_dir),
            headless=settings.browser_headless,
            viewport={"width": vp_w, "height": vp_h},
            locale=locale,
            timezone_id=tz,
            user_agent=ua,
            extra_http_headers={
                "Sec-CH-UA": sec_ch_ua,
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": sec_ch_ua_platform,
            },
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        # Auto-generate proxy URL from proxy_country if no explicit proxy_url
        if not proxy_url and proxy_country:
            proxy_url = _build_proxy_url(account_id, proxy_country)

        # Add proxy if available.
        # Playwright/Chromium does NOT parse credentials embedded in the server URL
        # (http://user:pass@host:port) — credentials must be passed in separate fields.
        if proxy_url:
            from urllib.parse import urlparse
            _p = urlparse(proxy_url)
            _server = f"{_p.scheme}://{_p.hostname}:{_p.port}"
            _proxy: dict = {"server": _server}
            if _p.username:
                _proxy["username"] = _p.username
            if _p.password:
                _proxy["password"] = _p.password
            context_kwargs["proxy"] = _proxy
            logger.info("browser.proxy_configured", proxy=f"{_p.hostname}:{_p.port}")

        self._context = await self._playwright.chromium.launch_persistent_context(
            **context_kwargs
        )

        # Block bandwidth-heavy resources that aren't needed for DOM automation.
        # Images, fonts, and media can account for 80-90% of page weight on LinkedIn
        # but are never needed for button/selector interaction.
        async def _abort_heavy_resources(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await self._context.route("**/*", _abort_heavy_resources)
        logger.debug("browser.resource_blocking_enabled")

        # Apply stealth patches
        if settings.stealth_enabled:
            try:
                from playwright_stealth import Stealth
                stealth = Stealth()
                await stealth.apply_stealth_async(self._context)
                logger.info("browser.stealth_applied")
            except ImportError:
                logger.warning("browser.stealth_not_installed", hint="pip install playwright-stealth")

        # Smart cookie injection: only inject li_at if missing or changed.
        # This preserves bcookie, bscookie, JSESSIONID, li_rm etc. that
        # accumulate naturally during browsing and help keep the session alive.
        existing_cookies = await self._context.cookies("https://www.linkedin.com")
        existing_li_at = next(
            (c for c in existing_cookies if c["name"] == "li_at"), None
        )
        if not existing_li_at or existing_li_at["value"] != li_at_cookie:
            if existing_li_at:
                logger.info("browser.cookie_updated", account_id=account_id)
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
        else:
            logger.debug("browser.cookie_preserved", account_id=account_id)

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
            await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=15000)
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
        """Clean shutdown of browser context and Playwright.

        When pool_managed=True, this is a no-op — the pool manages the
        browser lifecycle. This prevents executor code from accidentally
        closing a shared browser.
        """
        if self._pool_managed:
            logger.debug("browser.close_skipped_pool_managed")
            return
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("browser.closed")

    async def force_close(self):
        """Unconditional shutdown, ignoring pool_managed flag. Used by the pool itself."""
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
        logger.info("browser.force_closed")

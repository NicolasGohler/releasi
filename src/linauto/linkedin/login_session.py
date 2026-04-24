"""noVNC-based manual login session for LinkedIn accounts.

Launches a headed Chromium browser accessible via noVNC web client,
allowing the user to manually log into LinkedIn when cookies expire.

Uses a temporary profile directory to avoid locking the automation
browser's profile. Cookies are extracted after login and saved to DB.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import os
import tempfile
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import async_playwright, BrowserContext, Playwright

logger = structlog.get_logger()

DISPLAY = ":99"
VNC_PORT = 5999
NOVNC_PORT = 6080


class LoginSessionManager:
    """Manages a single noVNC login session at a time."""

    _instance: Optional[LoginSessionManager] = None

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._account_id: Optional[str] = None
        self._processes: list[subprocess.Popen] = []
        self._temp_dir: Optional[str] = None

    @classmethod
    def get_instance(cls) -> LoginSessionManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_active(self) -> bool:
        return self._context is not None

    @property
    def active_account_id(self) -> Optional[str]:
        return self._account_id

    def _start_xvfb(self):
        """Start Xvfb virtual display."""
        proc = subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes.append(proc)
        logger.info("login_session.xvfb_started", display=DISPLAY)

    def _start_vnc(self):
        """Start x11vnc to expose the display."""
        proc = subprocess.Popen(
            [
                "x11vnc",
                "-display", DISPLAY,
                "-nopw",
                "-listen", "0.0.0.0",
                "-rfbport", str(VNC_PORT),
                "-forever",
                "-shared",
                "-noxdamage",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes.append(proc)
        logger.info("login_session.vnc_started", port=VNC_PORT)

    def _start_websockify(self):
        """Start websockify to bridge noVNC web client to VNC."""
        novnc_dir = "/usr/share/novnc"
        proc = subprocess.Popen(
            [
                "websockify",
                "--web", novnc_dir,
                str(NOVNC_PORT),
                f"localhost:{VNC_PORT}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes.append(proc)
        logger.info("login_session.websockify_started", port=NOVNC_PORT)

    async def start_session(
        self,
        account_id: str,
        proxy_url: Optional[str] = None,
        li_at_cookie: Optional[str] = None,
        start_url: str = "https://www.linkedin.com/login",
    ) -> str:
        """
        Start a noVNC browser session for the given account.

        Uses a temporary profile directory so it doesn't conflict with the
        automation browser that may be using the account's main profile.
        Uses the same deterministic User-Agent as the pool browser to
        maintain a consistent fingerprint.

        proxy_url should be the account's residential proxy so LinkedIn sees the
        correct country IP (not the datacenter IP).

        li_at_cookie — when provided the cookie is injected before navigation so
        the user lands on an authenticated LinkedIn page (browse mode). When
        omitted the browser starts at the login page (login mode).

        Returns the noVNC URL path for the user to access.
        """
        if self.is_active:
            raise RuntimeError(
                f"A login session is already active for account {self._account_id}. "
                "Finish it before starting a new one."
            )

        self._account_id = account_id

        # Create a temporary profile directory for the login browser
        self._temp_dir = tempfile.mkdtemp(prefix=f"linauto_login_{account_id}_")
        logger.info("login_session.temp_dir_created", path=self._temp_dir)

        # Start display stack
        os.environ["DISPLAY"] = DISPLAY
        self._start_xvfb()
        await asyncio.sleep(1)
        self._start_vnc()
        await asyncio.sleep(0.5)
        self._start_websockify()
        await asyncio.sleep(0.5)

        # Use the same deterministic UA as the pool browser
        from linauto.linkedin.browser import _deterministic_ua
        ua = _deterministic_ua(account_id)

        # Parse proxy URL into the dict format Playwright requires.
        # Playwright/Chromium silently ignores credentials embedded in the server URL,
        # so credentials must be passed separately.
        proxy_kwargs: dict = {}
        if proxy_url:
            from urllib.parse import urlparse
            _p = urlparse(proxy_url)
            _server = f"{_p.scheme}://{_p.hostname}:{_p.port}"
            _proxy: dict = {"server": _server}
            if _p.username:
                _proxy["username"] = _p.username
            if _p.password:
                _proxy["password"] = _p.password
            proxy_kwargs["proxy"] = _proxy
            logger.info("login_session.proxy_configured", proxy=f"{_p.hostname}:{_p.port}")

        # Launch headed Chromium with the temporary profile
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._temp_dir,
            headless=False,
            viewport={"width": 1200, "height": 750},
            user_agent=ua,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                f"--display={DISPLAY}",
            ],
            **proxy_kwargs,
        )

        # Inject stored cookie (browse mode) or navigate to login page (login mode)
        page = await self._context.new_page()
        if li_at_cookie:
            await self._context.add_cookies([{
                "name": "li_at",
                "value": li_at_cookie,
                "domain": ".linkedin.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }])
        await page.goto(start_url, wait_until="domcontentloaded")

        logger.info("login_session.started", account_id=account_id, browse_mode=bool(li_at_cookie))

        return f"/vnc.html?autoconnect=true&resize=scale"

    async def finish_session(self) -> dict:
        """
        Extract cookies from the browser, copy the full profile to the
        account's persistent browser_data directory, then clean up.

        Returns dict with li_at and li_a cookie values (if found).

        Copying the full profile (not just li_at + li_a) preserves all
        session cookies accumulated during login: bcookie, bscookie,
        JSESSIONID, li_rm, etc.  The pool browser will load them on its
        next launch, resulting in a much more complete session fingerprint.
        """
        if not self.is_active or not self._context:
            raise RuntimeError("No active login session to finish.")

        result = {"li_at": None, "li_a": None, "account_id": self._account_id}

        try:
            cookies = await self._context.cookies(["https://www.linkedin.com"])
            for cookie in cookies:
                if cookie["name"] == "li_at":
                    result["li_at"] = cookie["value"]
                elif cookie["name"] == "li_a":
                    result["li_a"] = cookie["value"]

            logger.info(
                "login_session.cookies_extracted",
                has_li_at=result["li_at"] is not None,
                has_li_a=result["li_a"] is not None,
                total_cookies=len(cookies),
            )
        except Exception as e:
            logger.error("login_session.cookie_extraction_failed", error=str(e))

        # Copy the full browser profile to the account's persistent data dir.
        # Close the context first so all cookies are flushed to disk.
        if self._temp_dir and self._account_id and result["li_at"]:
            try:
                if self._context:
                    try:
                        await self._context.close()
                    except Exception:
                        pass
                    self._context = None

                dest = Path("data/browser_data") / self._account_id
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(self._temp_dir, str(dest))
                logger.info(
                    "login_session.profile_copied",
                    account_id=self._account_id,
                    dest=str(dest),
                )
            except Exception as e:
                logger.warning("login_session.profile_copy_failed", error=str(e))

        await self._cleanup()

        return result

    async def _cleanup(self):
        """Stop all processes, close browser, and remove temp directory."""
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

        # Kill all spawned processes (Xvfb, x11vnc, websockify)
        for proc in self._processes:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._processes.clear()

        # Remove temporary profile directory
        if self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                logger.info("login_session.temp_dir_removed", path=self._temp_dir)
            except Exception:
                pass
            self._temp_dir = None

        self._account_id = None
        logger.info("login_session.cleaned_up")

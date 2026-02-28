"""noVNC-based manual login session for LinkedIn accounts.

Launches a headed Chromium browser accessible via noVNC web client,
allowing the user to manually log into LinkedIn when cookies expire.
"""
from __future__ import annotations

import asyncio
import subprocess
import signal
import os
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
        # noVNC static files location (installed via apt)
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

    async def start_session(self, account_id: str) -> str:
        """
        Start a noVNC login session for the given account.

        Returns the noVNC URL for the user to access.
        Raises RuntimeError if a session is already active.
        """
        if self.is_active:
            raise RuntimeError(
                f"A login session is already active for account {self._account_id}. "
                "Finish it before starting a new one."
            )

        self._account_id = account_id

        # Start display stack
        os.environ["DISPLAY"] = DISPLAY
        self._start_xvfb()
        await asyncio.sleep(1)  # Wait for Xvfb to be ready
        self._start_vnc()
        await asyncio.sleep(0.5)
        self._start_websockify()
        await asyncio.sleep(0.5)

        # Launch headed Chromium on the Xvfb display
        user_data_dir = Path("data/browser_data") / account_id
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            viewport={"width": 1200, "height": 750},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                f"--display={DISPLAY}",
            ],
        )

        # Navigate to LinkedIn login page
        page = await self._context.new_page()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        logger.info("login_session.started", account_id=account_id)

        # Return noVNC URL (the API server knows the host)
        return f"/vnc.html?autoconnect=true&resize=scale"

    async def finish_session(self) -> dict:
        """
        Extract cookies from the browser, save them, and clean up.

        Returns dict with li_at and li_a cookie values (if found).
        """
        if not self.is_active or not self._context:
            raise RuntimeError("No active login session to finish.")

        result = {"li_at": None, "li_a": None, "account_id": self._account_id}

        try:
            # Extract cookies from the browser context
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
            )
        except Exception as e:
            logger.error("login_session.cookie_extraction_failed", error=str(e))

        # Clean up everything
        await self._cleanup()

        return result

    async def _cleanup(self):
        """Stop all processes and close browser."""
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

        # Kill all spawned processes
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

        self._account_id = None
        logger.info("login_session.cleaned_up")

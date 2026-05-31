"""Scraper cookie management endpoints.

Provides a noVNC browser session flow for refreshing CryptoRank and RootData
cookies, plus a fetch endpoint for the fundraising agent.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.db.models import ScraperCookie
from releasi.db.repository import Repository
from releasi.linkedin.login_session import (
    DISPLAY,
    IDLE_TIMEOUT_SECONDS,
    NOVNC_PORT,
    VNC_PORT,
)

logger = structlog.get_logger()

router = APIRouter(dependencies=[Depends(require_api_key)])

SCRAPER_SITES = {
    "cryptorank": {
        "start_url": "https://cryptorank.io/sign-in",
        "domain": "cryptorank.io",
    },
    "rootdata": {
        "start_url": "https://www.rootdata.com/",
        "domain": "rootdata.com",
    },
}


class ScraperSessionManager:
    """Manages a single noVNC browser session for scraper cookie refresh.

    Only one session (LinkedIn or scraper) can run at a time because all
    sessions share the same Xvfb display and VNC port.
    """

    _instance: Optional[ScraperSessionManager] = None

    def __init__(self):
        self._playwright = None
        self._context = None
        self._site: Optional[str] = None
        self._processes: list = []
        self._temp_dir: Optional[str] = None
        self._token: Optional[str] = None
        self._idle_task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> ScraperSessionManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_active(self) -> bool:
        return self._context is not None

    @property
    def active_site(self) -> Optional[str]:
        return self._site

    def _start_xvfb(self):
        proc = subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes.append(proc)

    def _start_vnc(self):
        proc = subprocess.Popen(
            [
                "x11vnc",
                "-display", DISPLAY,
                "-nopw",
                "-listen", "localhost",
                "-rfbport", str(VNC_PORT),
                "-forever",
                "-shared",
                "-noxdamage",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes.append(proc)

    def _start_websockify(self, token_file: str):
        novnc_dir = "/usr/share/novnc"
        log_fd = open("/tmp/releasi_websockify_scraper.log", "ab")
        proc = subprocess.Popen(
            [
                "websockify",
                "--web", novnc_dir,
                "--token-plugin", "TokenFile",
                "--token-source", token_file,
                str(NOVNC_PORT),
            ],
            stdout=log_fd,
            stderr=log_fd,
        )
        self._processes.append(proc)

    async def _idle_timeout_task(self):
        await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        if self.is_active:
            logger.info("scraper_session.idle_timeout", site=self._site)
            await self._cleanup()

    async def start_session(self, site: str, start_url: str) -> str:
        """Launch a headed Chromium navigated to start_url. Returns noVNC path."""
        from releasi.linkedin.login_session import LoginSessionManager
        if LoginSessionManager.get_instance().is_active:
            raise RuntimeError(
                "A LinkedIn login session is active. Finish it before starting a scraper session."
            )

        if self.is_active:
            raise RuntimeError(f"A scraper session is already active for '{self._site}'.")

        self._site = site
        self._temp_dir = tempfile.mkdtemp(prefix=f"releasi_scraper_{site}_")

        self._token = secrets.token_urlsafe(24)
        token_file = os.path.join(self._temp_dir, "tokens.cfg")
        with open(token_file, "w") as f:
            f.write(f"{self._token}: localhost:{VNC_PORT}\n")

        os.environ["DISPLAY"] = DISPLAY
        self._start_xvfb()
        await asyncio.sleep(1)
        self._start_vnc()
        await asyncio.sleep(0.5)
        self._start_websockify(token_file)
        await asyncio.sleep(0.5)

        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._temp_dir,
            headless=False,
            viewport={"width": 1280, "height": 800},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                f"--display={DISPLAY}",
            ],
        )

        page = await self._context.new_page()
        await page.goto(start_url, wait_until="domcontentloaded")

        self._idle_task = asyncio.create_task(self._idle_timeout_task())
        logger.info("scraper_session.started", site=site, url=start_url)

        encoded_path = f"websockify%3Ftoken%3D{self._token}"
        return f"/vnc.html?path={encoded_path}&autoconnect=true&resize=scale&quality=3&compression=9"

    async def finish_session(self, domain: str) -> list:
        """Extract all cookies matching domain, clean up, return cookie list."""
        if not self.is_active or not self._context:
            raise RuntimeError("No active scraper session to finish.")

        cookies = []
        try:
            all_cookies = await self._context.cookies()
            cookies = [c for c in all_cookies if domain in c.get("domain", "")]
            logger.info(
                "scraper_session.cookies_extracted",
                site=self._site,
                domain=domain,
                count=len(cookies),
            )
        except Exception as e:
            logger.error("scraper_session.cookie_extraction_failed", error=str(e))

        await self._cleanup()
        return cookies

    async def _cleanup(self):
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
        self._idle_task = None

        if self._context:
            try:
                await asyncio.wait_for(self._context.close(), timeout=5)
            except Exception:
                pass
            self._context = None

        if self._playwright:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout=5)
            except Exception:
                pass
            self._playwright = None

        loop = asyncio.get_event_loop()
        for proc in self._processes:
            try:
                proc.terminate()
                await asyncio.wait_for(loop.run_in_executor(None, proc.wait), timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._processes.clear()

        if self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass
            self._temp_dir = None

        self._site = None
        self._token = None
        logger.info("scraper_session.cleaned_up")


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/scrapers")
async def list_scrapers(repo: Repository = Depends(get_repo)):
    """Return cookie freshness status for all scraper sites."""
    now = datetime.utcnow()
    result = []
    for site in SCRAPER_SITES:
        row = await repo.get_scraper_cookie(site)
        if row:
            age_hours = (now - row.captured_at.replace(tzinfo=None)).total_seconds() / 3600
            result.append({
                "site": site,
                "has_cookies": True,
                "captured_at": row.captured_at.isoformat(),
                "age_hours": round(age_hours, 1),
            })
        else:
            result.append({
                "site": site,
                "has_cookies": False,
                "captured_at": None,
                "age_hours": None,
            })
    return result


@router.post("/scrapers/{site}/login-session")
async def start_scraper_login_session(site: str):
    """Start a noVNC browser session for the given scraper site."""
    if site not in SCRAPER_SITES:
        raise HTTPException(status_code=404, detail=f"Unknown scraper site: {site}")

    manager = ScraperSessionManager.get_instance()
    if manager.is_active:
        await manager._cleanup()

    try:
        novnc_path = await manager.start_session(site, SCRAPER_SITES[site]["start_url"])
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start session: {e}")

    return {"novnc_url": novnc_path, "session_id": manager._token}


@router.post("/scrapers/{site}/login-session/finish")
async def finish_scraper_login_session(
    site: str, repo: Repository = Depends(get_repo)
):
    """Extract cookies from the active browser session and save to DB."""
    if site not in SCRAPER_SITES:
        raise HTTPException(status_code=404, detail=f"Unknown scraper site: {site}")

    manager = ScraperSessionManager.get_instance()
    if not manager.is_active or manager.active_site != site:
        raise HTTPException(status_code=400, detail="No active session for this site.")

    domain = SCRAPER_SITES[site]["domain"]
    cookies = await manager.finish_session(domain)
    now = datetime.utcnow()

    await repo.upsert_scraper_cookie(site, json.dumps(cookies), now)

    return {
        "success": True,
        "cookie_count": len(cookies),
        "captured_at": now.isoformat(),
    }


@router.post("/scrapers/{site}/login-session/cancel")
async def cancel_scraper_login_session(site: str):
    """Cancel an active scraper login session without saving cookies."""
    if site not in SCRAPER_SITES:
        raise HTTPException(status_code=404, detail=f"Unknown scraper site: {site}")

    manager = ScraperSessionManager.get_instance()
    if manager.is_active and manager.active_site == site:
        await manager._cleanup()
    return {"success": True}


@router.get("/scrapers/{site}/cookies")
async def get_scraper_cookies(site: str, repo: Repository = Depends(get_repo)):
    """Return stored cookies for a site (used by the fundraising agent at runtime)."""
    if site not in SCRAPER_SITES:
        raise HTTPException(status_code=404, detail=f"Unknown scraper site: {site}")

    row = await repo.get_scraper_cookie(site)
    if not row:
        raise HTTPException(status_code=404, detail=f"No cookies stored for '{site}'")

    await repo.touch_scraper_cookie(site)

    return {
        "site": site,
        "cookies": json.loads(row.cookies_json),
        "captured_at": row.captured_at.isoformat(),
    }

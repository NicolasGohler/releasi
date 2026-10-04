"""noVNC-based manual login session for LinkedIn accounts.

Launches a headed Chromium browser accessible via noVNC web client,
allowing the user to manually log into LinkedIn when cookies expire.

Uses a temporary profile directory to avoid locking the automation
browser's profile. Cookies are extracted after login and saved to DB.
"""
from __future__ import annotations

import asyncio
import secrets
import shutil
import subprocess
import os
import tempfile
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import async_playwright, BrowserContext, Playwright

from releasi.linkedin.pool import BrowserPool, ExternalBrowserLease, get_browser_pool

logger = structlog.get_logger()

DISPLAY = ":99"
VNC_PORT = 5999
NOVNC_PORT = 6080
IDLE_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _replace_profile(source: Path, destination: Path) -> None:
    """Stage a flushed profile before replacing the saved one, with rollback."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".login_profile_", dir=str(destination.parent)))
    backup = staging / "previous"
    staged_profile = staging / "profile"
    try:
        shutil.copytree(
            str(source), str(staged_profile),
            ignore=shutil.ignore_patterns("Singleton*", "tokens.cfg"),
        )
        # The copy is owned by the running user. Keep profile access private,
        # even if a prior deployment left the destination owned by root.
        staged_profile.chmod(0o700)
        if destination.exists():
            destination.rename(backup)
        try:
            staged_profile.rename(destination)
        except BaseException:
            if backup.exists():
                backup.rename(destination)
            raise
    finally:
        # On a rollback failure preserve the backup for recovery, rather than
        # deleting the only remaining copy of the original saved profile.
        if backup.exists() and not destination.exists():
            logger.error("login_session.profile_rollback_failed", backup=str(backup))
        else:
            try:
                shutil.rmtree(staging)
            except OSError as exc:
                logger.warning("login_session.profile_staging_cleanup_failed", error_type=type(exc).__name__)


class LoginSessionManager:
    """Manages a single noVNC login session at a time."""

    _instance: Optional[LoginSessionManager] = None

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._account_id: Optional[str] = None
        self._processes: list[subprocess.Popen] = []
        self._temp_dir: Optional[str] = None
        self._token: Optional[str] = None
        self._idle_task: Optional[asyncio.Task] = None
        self._lease: Optional[ExternalBrowserLease] = None
        self._lifecycle_lock = asyncio.Lock()

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
        logger.info("login_session.vnc_started", port=VNC_PORT)

    def _start_websockify(self, token_file: str):
        """Start websockify with token auth so each session requires a secret token.

        token_file must be a text file with lines: <token>: <host>:<port>
        websockify 0.12 ships the plugin as 'TokenFile' (not 'FileTokenPlugin').
        """
        novnc_dir = "/usr/share/novnc"
        log_path = "/tmp/linauto_websockify.log"
        log_fd = open(log_path, "ab")
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
        logger.info("login_session.websockify_started", port=NOVNC_PORT, log=log_path)

    async def _idle_timeout_task(self):
        """Auto-terminate the session after IDLE_TIMEOUT_SECONDS."""
        await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        if self.is_active:
            logger.info("login_session.idle_timeout", account_id=self._account_id)
            await self._cleanup()

    async def start_session(
        self,
        account_id: str,
        proxy_url: Optional[str] = None,
        li_at_cookie: Optional[str] = None,
        start_url: str = "https://www.linkedin.com/login",
        browser_pool: Optional[BrowserPool] = None,
    ) -> str:
        """Reserve the account before opening a headed browser.

        The manager owns the reservation until cleanup. The optional pool is
        for API-only callers/tests; normally the shared singleton is used.
        """
        async with self._lifecycle_lock:
            if self.is_active or self._lease is not None:
                raise RuntimeError("A login session is already active; finish it first.")
            try:
                if browser_pool is None:
                    try:
                        browser_pool = get_browser_pool()
                    except RuntimeError:
                        pass  # No scheduler/pool in standalone API or CLI mode.
                if browser_pool is not None:
                    self._lease = await browser_pool.reserve_external(account_id)
                return await self._start_session(account_id, proxy_url, li_at_cookie, start_url)
            except BaseException:
                await self._run_cleanup()
                raise

    async def _start_session(
        self,
        account_id: str,
        proxy_url: Optional[str],
        li_at_cookie: Optional[str],
        start_url: str,
    ) -> str:
        """
        Start a noVNC browser session for the given account.

        Uses a temporary profile directory while holding exclusive ownership
        of the account, with its automation browser closed.
        Uses the same deterministic User-Agent as the pool browser to
        maintain a consistent fingerprint.

        proxy_url should be the account's residential proxy so LinkedIn sees the
        correct country IP (not the datacenter IP).

        li_at_cookie — when provided the cookie is injected before navigation so
        the user lands on an authenticated LinkedIn page (browse mode). When
        omitted the browser starts at the login page (login mode).

        Returns the noVNC URL path (including secret token) for the user to access.
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

        # Generate a cryptographically random token for this session.
        # websockify FileTokenPlugin maps the token to the VNC target so only
        # a client that knows the token can connect — port 6080 alone is not enough.
        self._token = secrets.token_urlsafe(24)
        # websockify TokenFile plugin expects a text file with lines:
        #   <token>: <host>:<port>
        # Passing a directory as --token-source causes it to try reading the
        # directory entry as a text file, producing a "Syntax error on line 1".
        token_file = os.path.join(self._temp_dir, "tokens.cfg")
        with open(token_file, "w") as f:
            f.write(f"{self._token}: localhost:{VNC_PORT}\n")

        # Start display stack
        os.environ["DISPLAY"] = DISPLAY
        self._start_xvfb()
        await asyncio.sleep(1)
        self._start_vnc()
        await asyncio.sleep(0.5)
        self._start_websockify(token_file)
        await asyncio.sleep(0.5)

        # Use the same deterministic UA as the pool browser
        from releasi.linkedin.browser import _deterministic_ua
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

        # Login mode only: auto-tick "Keep me logged in" so LinkedIn issues the
        # long-lived li_at (~1y) and the li_rm remember-me token instead of a
        # short session cookie. This is the single biggest lever for session
        # longevity. Best-effort: an init script installs a MutationObserver that
        # ticks any remember-me checkbox whenever it appears across the multi-step
        # login form. Fails open (never blocks login if the selector changes).
        # NOTE: "Keep me logged in" is unavailable when 2FA is enabled — if a
        # future account uses 2FA, no li_rm will be issued regardless.
        if not li_at_cookie:
            try:
                await self._context.add_init_script(
                    """
                    (() => {
                      function tick() {
                        for (const el of document.querySelectorAll('input[type=checkbox]')) {
                          const id = (el.id || '').toLowerCase();
                          const nm = (el.name || '').toLowerCase();
                          const lbl = (el.closest('label') ? el.closest('label').innerText : '').toLowerCase();
                          if (id.includes('remember') || nm.includes('remember') ||
                              lbl.includes('keep me logged in')) {
                            if (!el.checked) {
                              el.checked = true;
                              el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                          }
                        }
                      }
                      document.addEventListener('DOMContentLoaded', tick);
                      try { new MutationObserver(tick).observe(document.documentElement,
                            { childList: true, subtree: true }); } catch (e) {}
                      setInterval(tick, 1000);
                    })();
                    """
                )
                logger.info("login_session.keep_logged_in_autotick_installed")
            except Exception as e:
                logger.warning("login_session.keep_logged_in_autotick_failed", error=str(e))

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

        # Schedule automatic teardown after the idle timeout
        self._idle_task = asyncio.create_task(self._idle_timeout_task())

        logger.info("login_session.started", account_id=account_id, browse_mode=bool(li_at_cookie))

        # The token is part of the WebSocket path that noVNC connects to.
        # Without the correct token the WebSocket handshake is rejected by websockify.
        # The websockify TokenFile plugin reads the token from the ?token= query
        # parameter on the WebSocket request, not from the URL path. Embed it
        # in the noVNC `path` value (URL-encode the `?` and `=` so they survive
        # the outer query string parsing and get passed verbatim to noVNC, which
        # then uses the value as the WebSocket path).
        encoded_path = f"websockify%3Ftoken%3D{self._token}"
        return f"/vnc.html?path={encoded_path}&autoconnect=true&resize=scale&quality=3&compression=9"

    async def finish_session(self, *, release_lease: bool = True) -> dict:
        """Extract and save the profile, reporting whether persistence succeeded.

        API routes can pass ``release_lease=False`` and call ``release_lease()``
        in finally after saving credentials to the DB. This prevents automation
        from relaunching with old credentials between profile and DB writes.
        """
        async with self._lifecycle_lock:
            try:
                result = await self._finish_session()
            except BaseException:
                await self._run_cleanup()
                raise
            await self._run_cleanup(release_lease=release_lease)
            return result

    async def _finish_session(self) -> dict:
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

        result = {
            "li_at": None,
            "li_a": None,
            "li_rm": None,
            "cookies_json": None,
            "li_at_expires_at": None,  # ISO string (UTC) or None
            "account_id": self._account_id,
            "profile_saved": False,
            "profile_error": None,
        }

        try:
            import json as _json
            from datetime import datetime as _dt

            cookies = await self._context.cookies(["https://www.linkedin.com"])
            for cookie in cookies:
                name = cookie.get("name")
                if name == "li_at":
                    result["li_at"] = cookie["value"]
                    exp = cookie.get("expires")
                    if exp and exp > 0:
                        try:
                            result["li_at_expires_at"] = _dt.utcfromtimestamp(exp).isoformat()
                        except Exception:
                            pass
                elif name == "li_a":
                    result["li_a"] = cookie["value"]
                elif name == "li_rm":
                    result["li_rm"] = cookie["value"]

            # Persist the full jar so a fresh browser profile can be fully
            # restored later (bcookie, bscookie, JSESSIONID, li_rm, …) rather
            # than re-seeded from li_at alone.
            try:
                result["cookies_json"] = _json.dumps(cookies)
            except Exception:
                result["cookies_json"] = None

            logger.info(
                "login_session.cookies_extracted",
                has_li_at=result["li_at"] is not None,
                has_li_a=result["li_a"] is not None,
                has_li_rm=result["li_rm"] is not None,
                li_at_expires_at=result["li_at_expires_at"],
                total_cookies=len(cookies),
            )
        except Exception as e:
            logger.error("login_session.cookie_extraction_failed", error=str(e))

        # Copy the full browser profile to the account's persistent data dir.
        # Close the context first so all cookies are flushed to disk.
        if self._temp_dir and self._account_id and result["li_at"]:
            try:
                if self._context:
                    await asyncio.wait_for(self._context.close(), timeout=5)
                    self._context = None

                dest = Path("data/browser_data") / self._account_id
                _replace_profile(Path(self._temp_dir), dest)
                result["profile_saved"] = True
                logger.info(
                    "login_session.profile_copied",
                    account_id=self._account_id,
                    dest=str(dest),
                )
            except Exception as e:
                result["profile_error"] = "Browser profile could not be saved; the previous profile was retained."
                logger.warning("login_session.profile_copy_failed", error_type=type(e).__name__)

        return result

    def release_lease(self) -> None:
        """Release a deferred reservation after the browser and DB save finish."""
        if self._context is not None or self._playwright is not None:
            raise RuntimeError("Close the login browser before releasing its lease")
        if self._lease is not None:
            self._lease.release()
            self._lease = None

    async def _cleanup(self, *, release_lease: bool = True):
        async with self._lifecycle_lock:
            await self._run_cleanup(release_lease=release_lease)

    async def _run_cleanup(self, *, release_lease: bool = True):
        caller = asyncio.current_task()
        cleanup = asyncio.create_task(self._cleanup_resources(caller, release_lease))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            # A cancelled finish cannot leave its deferred lease behind.
            self.release_lease()
            raise

    async def _cleanup_resources(self, caller, release_lease: bool):
        """Stop all processes, close browser, remove temp directory, and cancel idle timer."""
        # Cancel the idle timeout task if it's still running
        if self._idle_task and self._idle_task is not caller and not self._idle_task.done():
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

        # Kill all spawned processes (Xvfb, x11vnc, websockify)
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

        # Remove temporary profile directory (includes token dir)
        if self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                logger.info("login_session.temp_dir_removed", path=self._temp_dir)
            except Exception:
                pass
            self._temp_dir = None

        self._account_id = None
        self._token = None
        if release_lease:
            self.release_lease()
        logger.info("login_session.cleaned_up")

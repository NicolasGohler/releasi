"""Persistent browser pool for long-lived LinkedIn sessions.

Keeps one Chromium instance per account alive across scheduler jobs,
preserving cookies (bcookie, bscookie, JSESSIONID, li_rm, etc.) that
accumulate naturally during browsing.  A periodic keep-alive job
prevents sessions from expiring due to inactivity.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import structlog
from playwright.async_api import BrowserContext

from releasi.config import get_settings
from releasi.db.models import Account
from releasi.linkedin.browser import LinkedInBrowser

logger = structlog.get_logger()

# Minimum seconds between session validations on acquire
_VALIDATION_COOLDOWN = 30 * 60  # 30 minutes


@dataclass
class PoolSlot:
    """One browser slot per LinkedIn account."""

    account_id: str
    browser: LinkedInBrowser
    context: BrowserContext
    in_use: bool = False
    last_activity: float = field(default_factory=time.monotonic)
    last_validated: float = field(default_factory=time.monotonic)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserPool:
    """Manages persistent browser instances for active accounts.

    - One slot per account, up to ``pool_max_browsers``.
    - Per-slot ``asyncio.Lock`` for concurrency (APScheduler jobs are async
      on the same event loop).
    - Global lock only for slot creation / deletion.
    """

    def __init__(self, max_browsers: int = 3):
        self._slots: Dict[str, PoolSlot] = {}
        self._max_browsers = max_browsers
        self._global_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, accounts: list) -> None:
        """Pre-warm browsers for the given active accounts."""
        to_warm = accounts[: self._max_browsers]
        for account in to_warm:
            try:
                await self._create_slot(account)
                logger.info("pool.prewarm_ok", account=account.name)
            except Exception as e:
                logger.error("pool.prewarm_failed", account=account.name, error=str(e))
        logger.info("pool.started", slots=len(self._slots), max=self._max_browsers)

    async def shutdown(self) -> None:
        """Close all browser slots."""
        async with self._global_lock:
            for account_id, slot in list(self._slots.items()):
                try:
                    await slot.browser.force_close()
                except Exception:
                    pass
            self._slots.clear()
        logger.info("pool.shutdown")

    # ------------------------------------------------------------------
    # Acquire / Release
    # ------------------------------------------------------------------

    async def acquire(self, account: Account) -> BrowserContext:
        """Get (or create) a browser context for an account.

        Performs a health check if the slot hasn't been validated recently.
        Blocks if the slot is already in use by another job.
        """
        async with self._global_lock:
            slot = self._slots.get(account.id)
            if not slot:
                slot = await self._create_slot(account)

        # Per-slot lock — blocks concurrent access for the same account
        await slot._lock.acquire()
        slot.in_use = True
        slot.last_activity = time.monotonic()

        # Health-check: skip if recently validated
        now = time.monotonic()
        if now - slot.last_validated > _VALIDATION_COOLDOWN:
            healthy = await self._health_check(slot)
            if not healthy:
                logger.warning("pool.recreating_unhealthy_slot", account=account.name)
                await slot.browser.force_close()
                async with self._global_lock:
                    self._slots.pop(account.id, None)
                slot = await self._create_slot(account)
                await slot._lock.acquire()
                slot.in_use = True
                slot.last_activity = time.monotonic()
            slot.last_validated = time.monotonic()

        logger.debug("pool.acquired", account=account.name)
        return slot.context

    async def release_idle(self, account_id: str) -> None:
        """Navigate to about:blank to stop background JS, then release slot.

        LinkedIn's JavaScript makes continuous background requests (notification
        polling, WebSocket, feed updates) even when idle.  Navigating to
        about:blank before releasing stops all network activity and saves
        proxy bandwidth (~1-2 GB/day for a persistent context).
        """
        slot = self._slots.get(account_id)
        if slot and slot.context:
            try:
                pages = slot.context.pages
                if pages:
                    await pages[0].goto("about:blank", timeout=5000)
            except Exception:
                pass  # Best-effort; don't block release
        self.release(account_id)

    def release(self, account_id: str) -> None:
        """Mark a slot as no longer in use and release the per-slot lock."""
        slot = self._slots.get(account_id)
        if not slot:
            return
        slot.in_use = False
        slot.last_activity = time.monotonic()
        try:
            slot._lock.release()
        except RuntimeError:
            pass  # Already released
        logger.debug("pool.released", account_id=account_id)

    def is_busy(self, account_id: str) -> bool:
        """Check if an account's browser is currently in use."""
        slot = self._slots.get(account_id)
        return slot.in_use if slot else False

    def time_since_validated(self, account_id: str) -> float:
        """Seconds since the slot's session was last confirmed healthy.

        Returns infinity if the slot doesn't exist (forces a check).
        """
        slot = self._slots.get(account_id)
        if not slot:
            return float("inf")
        return time.monotonic() - slot.last_validated

    def confirm_session(self, account_id: str) -> None:
        """Mark the session as confirmed healthy right now.

        Called after a successful dispatch so the next cycle doesn't
        re-navigate to the feed unnecessarily.
        """
        slot = self._slots.get(account_id)
        if slot:
            slot.last_validated = time.monotonic()

    async def evict(self, account_id: str) -> None:
        """Close and remove a slot (e.g. when an account is removed)."""
        async with self._global_lock:
            slot = self._slots.pop(account_id, None)
        if slot:
            await slot.browser.force_close()
            logger.info("pool.evicted", account_id=account_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _create_slot(self, account: Account) -> PoolSlot:
        """Launch a new browser and register it as a pool slot.

        No HTTP pre-check — check_cookie_health() already validates at
        scheduler startup (routed through the account's proxy).  A bare
        httpx request from the server IP is a session-invalidation trigger
        and must never be sent.
        """
        browser = LinkedInBrowser(pool_managed=True)
        context = await browser.launch(
            account_id=account.id,
            li_at_cookie=account.li_at_cookie,
            user_agent=account.user_agent,
            proxy_url=account.proxy_url,
            proxy_country=account.proxy_country,
            timezone=account.timezone,
        )
        # validate_session() is skipped — check_cookie_health() already ran.
        # which is slow (15s) and breaks when the proxy blocks LinkedIn.

        slot = PoolSlot(
            account_id=account.id,
            browser=browser,
            context=context,
        )
        self._slots[account.id] = slot
        logger.info("pool.slot_created", account=account.name)
        return slot

    async def _health_check(self, slot: PoolSlot) -> bool:
        """Quick health check: try opening and closing a page."""
        try:
            page = await slot.context.new_page()
            await page.close()
            return True
        except Exception as e:
            logger.warning("pool.health_check_failed", account=slot.account_id, error=str(e))
            return False


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_pool: Optional[BrowserPool] = None


def get_browser_pool() -> BrowserPool:
    """Return the global BrowserPool instance (must be initialized first)."""
    if _pool is None:
        raise RuntimeError("BrowserPool not initialized. Call init_pool() first.")
    return _pool


async def init_pool(accounts: list) -> BrowserPool:
    """Create and pre-warm the global BrowserPool."""
    global _pool
    settings = get_settings()
    _pool = BrowserPool(max_browsers=settings.pool_max_browsers)
    await _pool.start(accounts)
    return _pool


async def shutdown_pool() -> None:
    """Shut down the global BrowserPool if initialized."""
    global _pool
    if _pool:
        await _pool.shutdown()
        _pool = None

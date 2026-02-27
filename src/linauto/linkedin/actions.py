"""Atomic LinkedIn actions: send connection request, send message, etc."""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import Page, Locator, TimeoutError as PlaywrightTimeout

from linauto.linkedin.navigator import LinkedInNavigator
from linauto.linkedin.detector import LimitDetector, DetectionResult, DetectionType
from linauto.linkedin import selectors
from linauto.safety.delays import DelayGenerator

logger = structlog.get_logger()

SCREENSHOT_DIR = Path("data/debug_screenshots")


class ActionStatus(str, enum.Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    LIMIT_REACHED = "limit_reached"
    CAPTCHA = "captcha"
    SESSION_EXPIRED = "session_expired"
    ERROR = "error"


@dataclass
class ActionResult:
    status: ActionStatus
    reason: Optional[str] = None
    details: dict = field(default_factory=dict)


class LinkedInActions:
    """Each method performs ONE atomic LinkedIn action."""

    def __init__(self, page: Page):
        self.page = page
        self.navigator = LinkedInNavigator(page)
        self.detector = LimitDetector()
        self.delay = DelayGenerator()

    # ── Element finding: multi-strategy approach ──────────────────────────

    async def _find_element(self, selector_list: list, timeout_ms: int = 5000):
        """Try each CSS selector in order. Return the first visible match."""
        per_selector_timeout = max(timeout_ms // len(selector_list), 1000)
        for sel in selector_list:
            try:
                locator = self.page.locator(sel).first
                await locator.wait_for(state="visible", timeout=per_selector_timeout)
                logger.debug("element.found", selector=sel)
                return locator
            except (PlaywrightTimeout, Exception):
                continue
        return None

    async def _try_locator(self, locator: Locator, timeout_ms: int = 3000):
        """Check if a Playwright Locator matches a visible element."""
        try:
            if await locator.count() > 0:
                await locator.first.wait_for(state="visible", timeout=timeout_ms)
                return locator.first
        except (PlaywrightTimeout, Exception):
            pass
        return None

    async def _find_button_by_js(self, text: str) -> Optional[Locator]:
        """
        Nuclear fallback: find a button by visible text using JavaScript.
        Adds a temporary data attribute to the found element so we can
        locate it reliably with a Playwright locator.
        """
        found = await self.page.evaluate("""
            (text) => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const style = window.getComputedStyle(btn);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (btn.offsetParent === null && style.position !== 'fixed') continue;
                    const btnText = btn.innerText.trim();
                    if (btnText === text) {
                        btn.setAttribute('data-linauto-found', 'true');
                        return true;
                    }
                }
                return false;
            }
        """, text)

        if found:
            locator = self.page.locator('button[data-linauto-found="true"]').first
            logger.info("element.found_by_js", text=text)
            return locator
        return None

    async def _find_dropdown_item_by_js(self, text: str) -> Optional[Locator]:
        """Find a dropdown menu item by visible text using JavaScript."""
        found = await self.page.evaluate("""
            (text) => {
                const candidates = document.querySelectorAll(
                    '[role="menuitem"], [role="button"], .artdeco-dropdown__item, li'
                );
                for (const el of candidates) {
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const elText = el.innerText.trim();
                    if (elText === text) {
                        el.setAttribute('data-linauto-found', 'dropdown-item');
                        return true;
                    }
                }
                return false;
            }
        """, text)

        if found:
            locator = self.page.locator('[data-linauto-found="dropdown-item"]').first
            logger.info("element.dropdown_item_found_by_js", text=text)
            return locator
        return None

    async def _debug_screenshot(self, label: str):
        """Save a debug screenshot for diagnosing selector failures."""
        try:
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            path = SCREENSHOT_DIR / f"{label}_{ts}.png"
            await self.page.screenshot(path=str(path), full_page=False)
            logger.info("debug.screenshot_saved", path=str(path))
        except Exception as e:
            logger.warning("debug.screenshot_failed", error=str(e))

    async def _dump_buttons_debug(self):
        """Dump all visible button text on the page for debugging."""
        try:
            buttons = await self.page.evaluate("""
                () => {
                    const result = [];
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const style = window.getComputedStyle(btn);
                        const visible = style.display !== 'none' && style.visibility !== 'hidden';
                        const text = btn.innerText.trim().substring(0, 50);
                        if (text && visible) {
                            result.push({
                                text: text,
                                tag: btn.tagName,
                                classes: btn.className.substring(0, 80),
                                ariaLabel: btn.getAttribute('aria-label') || '',
                                role: btn.getAttribute('role') || '',
                            });
                        }
                    }
                    return result;
                }
            """)
            logger.info("debug.visible_buttons", buttons=buttons)
        except Exception as e:
            logger.warning("debug.dump_failed", error=str(e))

    # ── Connect button finding: the critical path ─────────────────────────

    async def _find_connect_button(self, profile_url: str):
        """
        Find the Connect button on a profile page.

        Handles both layouts:
          - Connect as primary action button
          - Connect inside More dropdown (when Follow is primary)

        Uses multiple strategies in order of reliability:
          1. Scoped CSS selectors (profile actions area only)
          2. More dropdown → Connect (with get_by_role, CSS, JS fallbacks)
        """
        # ── Strategy 1: Direct Connect button (profile actions area only) ──
        # IMPORTANT: Do NOT use get_by_role("button", name="Invite.*connect")
        # here — sidebar "People you may know" Connect buttons share the same
        # aria-label pattern and would match instead of the profile's button.
        # Only use class-scoped CSS selectors that target the profile actions area.
        connect_by_css = await self._find_element(
            selectors.CONNECT_BUTTON_PRIMARY, timeout_ms=2000,
        )
        if connect_by_css:
            logger.info("action.connect_found", method="css_scoped", url=profile_url)
            return connect_by_css

        # ── Strategy 2: More dropdown → Connect ──
        logger.info("action.trying_more_dropdown", url=profile_url)

        more_btn = None

        # 2a. The More button has aria-label="More actions" (confirmed by diagnostic)
        more_btn = await self._try_locator(
            self.page.get_by_role("button", name="More actions", exact=True),
            timeout_ms=3000,
        )
        if more_btn:
            logger.info("action.more_found", method="get_by_role_more_actions")

        # 2b. Fallback: try just "More"
        if not more_btn:
            more_btn = await self._try_locator(
                self.page.get_by_role("button", name="More", exact=True),
                timeout_ms=2000,
            )
            if more_btn:
                logger.info("action.more_found", method="get_by_role_exact")

        # 2c. CSS selectors
        if not more_btn:
            more_btn = await self._find_element(
                selectors.CONNECT_BUTTON_MORE_DROPDOWN, timeout_ms=3000,
            )
            if more_btn:
                logger.info("action.more_found", method="css")

        # 2d. JavaScript fallback
        if not more_btn:
            more_btn = await self._find_button_by_js("More")
            if more_btn:
                logger.info("action.more_found", method="javascript")

        if not more_btn:
            logger.warning("action.more_button_not_found", url=profile_url)
            await self._dump_buttons_debug()
            await self._debug_screenshot("more_btn_missing")
            return None

        # Click More to open dropdown
        await more_btn.click()
        await self.delay.micro_delay(0.5, 1.5)

        # Find Connect in the dropdown
        connect_btn = None

        # Role-based: try menuitem and listitem roles
        for role in ["menuitem", "listitem"]:
            connect_btn = await self._try_locator(
                self.page.get_by_role(role, name=re.compile(r"Connect", re.IGNORECASE)),
                timeout_ms=2000,
            )
            if connect_btn:
                logger.info("action.connect_in_dropdown_found", method=f"get_by_role_{role}")
                return connect_btn

        # get_by_text for "Connect" — after clicking More, the dropdown Connect
        # is visible and the sidebar ones exist too, but dropdown is rendered last
        connect_btn = await self._try_locator(
            self.page.locator('.artdeco-dropdown__content').get_by_text("Connect", exact=True),
            timeout_ms=2000,
        )
        if connect_btn:
            logger.info("action.connect_in_dropdown_found", method="dropdown_get_by_text")
            return connect_btn

        # CSS selectors
        connect_btn = await self._find_element(
            selectors.CONNECT_IN_DROPDOWN, timeout_ms=3000,
        )
        if connect_btn:
            logger.info("action.connect_in_dropdown_found", method="css")
            return connect_btn

        # JavaScript fallback for dropdown item
        connect_btn = await self._find_dropdown_item_by_js("Connect")
        if connect_btn:
            logger.info("action.connect_in_dropdown_found", method="javascript")
            return connect_btn

        logger.warning("action.connect_not_in_dropdown", url=profile_url)
        await self._debug_screenshot("connect_in_dropdown_missing")
        return None

    # ── Main actions ──────────────────────────────────────────────────────

    async def send_connection_request(
        self, profile_url: str, message: Optional[str] = None, filters=None
    ) -> ActionResult:
        """
        Navigate to a profile and send a connection request.
        Optionally includes a personalized note.
        """
        # 1. Navigate to profile
        nav = await self.navigator.go_to_profile(profile_url)
        if not nav.success:
            return ActionResult(ActionStatus.ERROR, reason=f"Navigation failed: {nav.error}")
        if not nav.session_valid:
            return ActionResult(ActionStatus.SESSION_EXPIRED)

        # 1.5 Profile filter check (before any interaction)
        if filters and filters.any_enabled:
            from linauto.linkedin.profile_filter import ProfileFilter
            skip_reason = await ProfileFilter().check(self.page, filters)
            if skip_reason:
                return ActionResult(ActionStatus.SKIPPED, reason=skip_reason)

        # 2. Check if already connected
        already_connected = await self._find_element(
            selectors.ALREADY_CONNECTED_INDICATORS, timeout_ms=2000
        )
        if already_connected:
            return ActionResult(ActionStatus.SKIPPED, reason="already_connected")

        # 3. Check if request is pending (try both CSS and role-based)
        pending = await self._find_element(
            selectors.PENDING_CONNECTION_INDICATORS, timeout_ms=2000
        )
        if not pending:
            pending = await self._try_locator(
                self.page.get_by_role("button", name=re.compile(r"Pending", re.IGNORECASE)),
                timeout_ms=1000,
            )
        if pending:
            return ActionResult(ActionStatus.SKIPPED, reason="pending_request")

        # 4. Find Connect button (multi-strategy)
        connect_btn = await self._find_connect_button(profile_url)
        if not connect_btn:
            return ActionResult(
                ActionStatus.SKIPPED,
                reason="no_connect_button",
                details={"url": profile_url},
            )

        # 5. Click Connect
        await connect_btn.click()
        await self.delay.micro_delay(0.5, 1.5)

        # 6. Handle the "Add a note to your invitation?" modal
        if message:
            add_note_btn = await self._find_element(selectors.ADD_NOTE_BUTTON, timeout_ms=3000)
            if add_note_btn:
                await add_note_btn.click()
                await self.delay.micro_delay(0.3, 0.8)

                note_field = await self._find_element(selectors.NOTE_TEXTAREA, timeout_ms=3000)
                if note_field:
                    await note_field.click()
                    await self.delay.micro_delay(0.2, 0.5)
                    await self.delay.type_text(note_field, message)
                    await self.delay.micro_delay(0.5, 1.0)
                else:
                    logger.warning("action.note_field_not_found", url=profile_url)

            send_btn = await self._find_element(selectors.SEND_INVITATION_BUTTON, timeout_ms=5000)
            if not send_btn:
                send_btn = await self._try_locator(
                    self.page.get_by_role("button", name=re.compile(r"Send", re.IGNORECASE)),
                    timeout_ms=2000,
                )
        else:
            send_btn = await self._find_element(selectors.SEND_WITHOUT_NOTE, timeout_ms=3000)
            if not send_btn:
                send_btn = await self._find_element(selectors.SEND_INVITATION_BUTTON, timeout_ms=3000)
            if not send_btn:
                # Role-based fallback: look for Send button in the modal dialog
                send_btn = await self._try_locator(
                    self.page.locator('[role="dialog"]').get_by_role("button", name=re.compile(r"Send", re.IGNORECASE)),
                    timeout_ms=2000,
                )

        # 7. Click Send (check if button is enabled first)
        if send_btn:
            if await send_btn.is_disabled():
                logger.info("action.send_button_disabled", url=profile_url)
                await self._debug_screenshot("send_btn_disabled")
                return ActionResult(
                    ActionStatus.SKIPPED,
                    reason="send_button_disabled",
                    details={"url": profile_url},
                )
            await send_btn.click()
            await self.delay.micro_delay(1.0, 2.0)
        else:
            await self._debug_screenshot("send_btn_missing")
            return ActionResult(
                ActionStatus.ERROR,
                reason="send_button_not_found",
                details={"url": profile_url},
            )

        # 8. Check for limit/safety signals AFTER send was clicked.
        # IMPORTANT: At this point the connection request has already been sent.
        # If we detect a CAPTCHA here, still return SUCCESS so the lead is
        # correctly marked as sent. Only rate limits and session expiry should
        # override, since those may indicate the request didn't go through.
        detection = await self.detector.check_after_action(self.page)
        if detection.requires_cooldown:
            # Rate limit may mean the request was blocked — report as limit reached
            return ActionResult(
                ActionStatus.LIMIT_REACHED,
                reason=detection.detected.value,
                details={"detection": detection.details},
            )
        if detection.detected == DetectionType.CAPTCHA:
            # CAPTCHA after send click — request likely went through already.
            # Log warning but treat as success.
            logger.warning(
                "action.captcha_after_send",
                url=profile_url,
                note="Request likely sent before CAPTCHA appeared",
            )
        if detection.detected == DetectionType.SESSION_EXPIRED:
            return ActionResult(ActionStatus.SESSION_EXPIRED)

        logger.info("action.connection_request_sent", url=profile_url, with_note=bool(message))
        return ActionResult(ActionStatus.SUCCESS)

    async def send_message(self, profile_url: str, message: str) -> ActionResult:
        """Navigate to a profile and send a direct message."""
        nav = await self.navigator.go_to_profile(profile_url)
        if not nav.success:
            return ActionResult(ActionStatus.ERROR, reason=f"Navigation failed: {nav.error}")
        if not nav.session_valid:
            return ActionResult(ActionStatus.SESSION_EXPIRED)

        msg_btn = await self._find_element(selectors.MESSAGE_BUTTON, timeout_ms=5000)
        if not msg_btn:
            return ActionResult(ActionStatus.ERROR, reason="message_button_not_found")

        await msg_btn.click()
        await self.delay.micro_delay(1.0, 2.0)

        msg_input = await self._find_element(selectors.MESSAGE_INPUT, timeout_ms=5000)
        if not msg_input:
            return ActionResult(ActionStatus.ERROR, reason="message_input_not_found")

        await msg_input.click()
        await self.delay.micro_delay(0.2, 0.5)
        await self.delay.type_text(msg_input, message)
        await self.delay.micro_delay(0.5, 1.0)

        send_btn = await self._find_element(selectors.MESSAGE_SEND_BUTTON, timeout_ms=3000)
        if not send_btn:
            return ActionResult(ActionStatus.ERROR, reason="message_send_button_not_found")

        await send_btn.click()
        await self.delay.micro_delay(1.0, 2.0)

        detection = await self.detector.check_after_action(self.page)
        if not detection.is_clear:
            return ActionResult(
                ActionStatus.ERROR,
                reason=detection.detected.value,
                details={"detection": detection.details},
            )

        logger.info("action.message_sent", url=profile_url)
        return ActionResult(ActionStatus.SUCCESS)

    async def check_connection_status(self, profile_url: str) -> str:
        """
        Visit a profile and check connection status.
        Returns: 'connected', 'pending', 'not_connected', 'unknown'
        """
        nav = await self.navigator.go_to_profile(profile_url)
        if not nav.success:
            return "unknown"

        if await self._find_element(selectors.ALREADY_CONNECTED_INDICATORS, timeout_ms=3000):
            return "connected"
        if await self._find_element(selectors.PENDING_CONNECTION_INDICATORS, timeout_ms=2000):
            return "pending"
        if await self._find_element(selectors.CONNECT_BUTTON_PRIMARY, timeout_ms=2000):
            return "not_connected"

        return "unknown"

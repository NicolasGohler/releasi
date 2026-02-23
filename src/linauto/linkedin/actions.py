"""Atomic LinkedIn actions: send connection request, send message, etc."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

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

    async def _find_element(self, selector_list: list, timeout_ms: int = 5000):
        """Try each selector in order. Return the first visible match."""
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

    async def _find_connect_button(self, profile_url: str):
        """
        Find the Connect button on a profile page.
        Handles both layouts:
          - Connect as primary action button
          - Connect inside More dropdown (when Follow is primary)
        """
        # Strategy 1: Direct Connect button
        connect_btn = await self._find_element(
            selectors.CONNECT_BUTTON_PRIMARY, timeout_ms=3000
        )
        if connect_btn:
            logger.info("action.connect_found_primary", url=profile_url)
            return connect_btn

        # Strategy 2: More dropdown → Connect
        logger.info("action.trying_more_dropdown", url=profile_url)
        more_btn = await self._find_element(
            selectors.CONNECT_BUTTON_MORE_DROPDOWN, timeout_ms=5000
        )
        if not more_btn:
            logger.warning("action.more_button_not_found", url=profile_url)
            await self._debug_screenshot("more_btn_missing")
            return None

        await more_btn.click()
        await self.delay.micro_delay(0.5, 1.5)

        connect_btn = await self._find_element(
            selectors.CONNECT_IN_DROPDOWN, timeout_ms=5000
        )
        if not connect_btn:
            logger.warning("action.connect_not_in_dropdown", url=profile_url)
            await self._debug_screenshot("connect_in_dropdown_missing")
            return None

        logger.info("action.connect_found_in_dropdown", url=profile_url)
        return connect_btn

    async def send_connection_request(
        self, profile_url: str, message: Optional[str] = None, filters=None
    ) -> ActionResult:
        """
        Navigate to a profile and send a connection request.
        Optionally includes a personalized note.
        If filters are provided, checks profile quality before proceeding.
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

        # 3. Check if request is pending
        pending = await self._find_element(
            selectors.PENDING_CONNECTION_INDICATORS, timeout_ms=2000
        )
        if pending:
            return ActionResult(ActionStatus.SKIPPED, reason="pending_request")

        # 4. Find Connect button (primary or via More dropdown)
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
            # Click "Add a note" to open the note field
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

            # After adding note, click "Send invitation" / "Send"
            send_btn = await self._find_element(selectors.SEND_INVITATION_BUTTON, timeout_ms=3000)
        else:
            # No message — click "Send without a note" directly
            send_btn = await self._find_element(selectors.SEND_WITHOUT_NOTE, timeout_ms=3000)
            if not send_btn:
                # Fallback: some modals just have a "Send" button
                send_btn = await self._find_element(selectors.SEND_INVITATION_BUTTON, timeout_ms=2000)

        # 7. Click Send
        if send_btn:
            await send_btn.click()
            await self.delay.micro_delay(1.0, 2.0)
        else:
            await self._debug_screenshot("send_btn_missing")
            return ActionResult(
                ActionStatus.ERROR,
                reason="send_button_not_found",
                details={"url": profile_url},
            )

        # 8. Check for limit/safety signals
        detection = await self.detector.check_after_action(self.page)
        if detection.requires_cooldown:
            return ActionResult(
                ActionStatus.LIMIT_REACHED,
                reason=detection.detected.value,
                details={"detection": detection.details},
            )
        if detection.detected == DetectionType.CAPTCHA:
            return ActionResult(ActionStatus.CAPTCHA, reason="captcha_detected")
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

        # Find and click Message button
        msg_btn = await self._find_element(selectors.MESSAGE_BUTTON, timeout_ms=5000)
        if not msg_btn:
            return ActionResult(ActionStatus.ERROR, reason="message_button_not_found")

        await msg_btn.click()
        await self.delay.micro_delay(1.0, 2.0)

        # Type message
        msg_input = await self._find_element(selectors.MESSAGE_INPUT, timeout_ms=5000)
        if not msg_input:
            return ActionResult(ActionStatus.ERROR, reason="message_input_not_found")

        await msg_input.click()
        await self.delay.micro_delay(0.2, 0.5)
        await self.delay.type_text(msg_input, message)
        await self.delay.micro_delay(0.5, 1.0)

        # Send
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

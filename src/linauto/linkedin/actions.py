"""Atomic LinkedIn actions: send connection request, send message, etc."""
from __future__ import annotations

import asyncio
import enum
import random
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


@dataclass
class InvitationSnapshot:
    success: bool
    session_valid: bool
    urls: list  # profile URLs of all pending sent invitations


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

    async def _hover_and_click(self, locator, timeout: int = 5000):
        """Hover over a button with a short pause before clicking (mimics human behaviour)."""
        try:
            await locator.hover(timeout=timeout)
            await self.delay.micro_delay(0.08, 0.35)
            await locator.click(timeout=timeout)
        except Exception:
            await locator.click(timeout=timeout)

    async def _find_button_by_js(self, text: str) -> Optional[Locator]:
        """
        Nuclear fallback: find a button by visible text using JavaScript.
        Uses index-based locator to avoid mutating the DOM.
        """
        idx = await self.page.evaluate("""(text) => {
            const buttons = Array.from(document.querySelectorAll('button'));
            return buttons.findIndex(btn => {
                const style = window.getComputedStyle(btn);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (btn.offsetParent === null && style.position !== 'fixed') return false;
                return btn.innerText.trim() === text;
            });
        }""", text)
        if idx >= 0:
            logger.info("element.found_by_js", text=text)
            return self.page.locator('button').nth(idx)
        return None

    async def _find_dropdown_item_by_js(self, text: str) -> Optional[Locator]:
        """Find a dropdown menu item by visible text using JavaScript."""
        idx = await self.page.evaluate("""(text) => {
            const candidates = Array.from(document.querySelectorAll(
                '[role="menuitem"], [role="button"], .artdeco-dropdown__item, li'
            ));
            return candidates.findIndex(el => {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                return el.innerText.trim() === text;
            });
        }""", text)
        if idx >= 0:
            logger.info("element.dropdown_item_found_by_js", text=text)
            return self.page.locator('[role="menuitem"], [role="button"], .artdeco-dropdown__item, li').nth(idx)
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

    # ── Vanity name extraction ─────────────────────────────────────────────

    async def _extract_vanity_name(self, connect_btn, profile_url: str) -> Optional[str]:
        """Extract the vanity name for the preload custom-invite URL.

        Tries in order:
          1. The Connect anchor's href (/preload/custom-invite/?vanityName=...)
          2. The current page URL (/in/<vanity>/)
        """
        # Try anchor href first (most reliable)
        try:
            href = await connect_btn.get_attribute("href")
            if href and "vanityName=" in href:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(href).query)
                vanity = qs.get("vanityName", [None])[0]
                if vanity:
                    logger.info("action.vanity_from_anchor", vanity=vanity)
                    return vanity
        except Exception:
            pass

        # Fallback: extract from profile URL
        url = self.page.url
        # Handle both /in/name/ and /in/name
        if "/in/" in url:
            parts = url.split("/in/")[1].rstrip("/").split("?")[0].split("#")[0]
            if parts:
                logger.info("action.vanity_from_url", vanity=parts)
                return parts

        # Also try the original profile_url argument
        if "/in/" in profile_url:
            parts = profile_url.split("/in/")[1].rstrip("/").split("?")[0].split("#")[0]
            if parts:
                logger.info("action.vanity_from_original_url", vanity=parts)
                return parts

        logger.error("action.vanity_extraction_failed", url=profile_url)
        return None

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

        # ── Strategy 1b: Anchor retry with longer timeout ──
        # The navigator may have resolved (via "More" button) and called window.stop()
        # before LinkedIn's JS finished making the Connect anchor visible. Give it up
        # to 3 more seconds — this covers the async rendering window without slowing
        # down profiles that genuinely have no Connect anchor.
        # Note: must use wait_for() directly, not _try_locator(), because _try_locator
        # checks count() first (no timeout) and exits immediately if the element isn't
        # in the DOM yet. wait_for(state="visible") genuinely waits.
        try:
            anchor_loc = self.page.locator(
                'main a[aria-label^="Invite"][aria-label$="to connect"]'
            ).first
            await anchor_loc.wait_for(state="visible", timeout=3000)
            logger.info("action.connect_found", method="anchor_retry", url=profile_url)
            return anchor_loc
        except Exception:
            pass

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
        # Scroll into center of viewport first to avoid sticky nav bar interception
        await more_btn.scroll_into_view_if_needed()
        await self.delay.micro_delay(0.2, 0.5)
        try:
            await self._hover_and_click(more_btn, timeout=5000)
        except PlaywrightTimeout:
            # Sticky nav bar may intercept pointer events — use JS click as fallback
            logger.info("action.more_click_intercepted_using_js", url=profile_url)
            await more_btn.evaluate("el => el.click()")
        await self.delay.micro_delay(0.5, 1.5)

        # Wait explicitly for the dropdown container to become visible before
        # searching for items — LinkedIn uses a CSS transition that can cause
        # count() to return 0 during the animation (~200ms).
        try:
            await self.page.locator(".artdeco-dropdown__content").first.wait_for(
                state="visible", timeout=3000
            )
        except PlaywrightTimeout:
            pass  # dropdown may use a different container class — continue anyway

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

        # ── Strategy 3: Connect is a primary button that Strategy 1 missed ──
        # The More dropdown was opened but Connect was not inside it — this means
        # Connect is a top-level button whose container class didn't match Strategy 1.
        # Try a direct 'main button:has-text("Connect")' search now that the page
        # has had extra time to settle. Profile card comes before sidebar in DOM
        # order so .first is safe.
        connect_btn = await self._try_locator(
            self.page.locator("main button:has-text('Connect')").first,
            timeout_ms=2000,
        )
        if connect_btn:
            logger.info("action.connect_found", method="main_fallback", url=profile_url)
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

        # 1.5 Simulate reading the profile before connecting (human-like behaviour)
        try:
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await self.page.mouse.wheel(0, random.randint(200, 500))   # scroll down
            await asyncio.sleep(random.uniform(1.5, 3.0))
            if random.random() < 0.15:  # 15% chance to scroll back up slightly
                await self.page.mouse.wheel(0, random.randint(-300, -100))
                await asyncio.sleep(random.uniform(0.5, 1.0))
        except Exception:
            pass  # Best-effort — never block the connect action

        # 1.6 Profile filter check (before any interaction)
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

        # 4. Find Connect button/anchor to confirm profile is connectable
        connect_btn = await self._find_connect_button(profile_url)
        if not connect_btn:
            # Use ERROR (not SKIPPED) so the lead re-enters retry logic tomorrow.
            # SKIPPED is permanent; a missing button is often a transient DOM issue.
            return ActionResult(
                ActionStatus.ERROR,
                reason="no_connect_button",
                details={"url": profile_url},
            )

        # 5. Navigate to the preload custom-invite page instead of clicking the
        #    Connect anchor directly. The anchor click relies on LinkedIn's SPA
        #    router to open a modal, which fails when stylesheets are blocked
        #    (resource blocking). Navigating to the preload URL directly gives us
        #    a full page with the Send button, which works reliably.
        vanity_name = await self._extract_vanity_name(connect_btn, profile_url)
        if not vanity_name:
            return ActionResult(
                ActionStatus.ERROR,
                reason="no_vanity_name",
                details={"url": profile_url},
            )

        preload_url = f"https://www.linkedin.com/preload/custom-invite/?vanityName={vanity_name}"
        logger.info("action.navigating_to_preload", url=preload_url)
        try:
            await self.page.goto(preload_url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            logger.error("action.preload_navigation_failed", url=preload_url, error=str(e))
            return ActionResult(
                ActionStatus.ERROR,
                reason="preload_navigation_failed",
                details={"url": profile_url},
            )
        await self.delay.micro_delay(1.5, 3.0)

        # 6. Handle the custom-invite page
        if message:
            add_note_btn = await self._find_element(selectors.ADD_NOTE_BUTTON, timeout_ms=3000)
            if add_note_btn:
                await self._hover_and_click(add_note_btn)
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
                send_btn = await self._try_locator(
                    self.page.get_by_role("button", name=re.compile(r"Send", re.IGNORECASE)),
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
            await self._hover_and_click(send_btn)
            await self.delay.micro_delay(1.0, 2.0)
        else:
            # Check if the weekly invitation limit popup appeared instead
            if await self.detector.is_weekly_limit_reached(self.page):
                logger.warning("action.weekly_limit_instead_of_send", url=profile_url)
                await self._debug_screenshot("send_btn_missing")
                return ActionResult(
                    ActionStatus.LIMIT_REACHED,
                    reason="weekly_invitation_limit",
                    details={"url": profile_url},
                )
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

        await self._hover_and_click(msg_btn)
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

        await self._hover_and_click(send_btn)
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

        Logic: if the profile loads successfully and no 1st-degree badge is found,
        the person is not connected. 'unknown' is only returned when navigation fails
        (network/proxy error), to avoid masking actual declined invitations.
        """
        nav = await self.navigator.go_to_profile(profile_url)
        if not nav.success:
            return "unknown"

        if await self._find_element(selectors.ALREADY_CONNECTED_INDICATORS, timeout_ms=3000):
            return "connected"
        if await self._find_element(selectors.PENDING_CONNECTION_INDICATORS, timeout_ms=2000):
            return "pending"

        # Profile loaded successfully — if no 1st-degree badge and no "Pending" button,
        # the invitation was declined or expired (profile may show Follow/Connect/Other).
        return "not_connected"

    async def get_pending_invitation_count(self) -> int:
        """Navigate to invitation manager and get the pending invitation count."""
        nav = await self.navigator.go_to_invitation_manager()
        if not nav.success or not nav.session_valid:
            return -1

        # Try to parse count from page header (e.g. "123 Sent")
        count_el = await self._find_element(selectors.INVITATION_PENDING_COUNT, timeout_ms=5000)
        if count_el:
            text = await count_el.text_content()
            import re
            match = re.search(r'(\d[\d,]*)', text or "")
            if match:
                return int(match.group(1).replace(",", ""))

        # Fallback: count visible cards
        for sel in selectors.INVITATION_CARDS:
            count = await self.page.locator(sel).count()
            if count > 0:
                return count

        return 0

    async def _extract_invitation_urls(self) -> list:
        """Extract all /in/ profile URLs from the currently loaded invitation manager page."""
        return await self.page.evaluate("""() => {
            const seen = new Set();
            const urls = [];

            // Strategy 1: known card container selectors
            const cardContainerSelectors = [
                'li.invitation-card', '.mn-invitation-list li',
                '[data-view-name="invitation-card"]', '.invitation-card',
                '.mn-invitation-card', 'li[class*="invitation"]',
                '[class*="invitation-card"]', '[data-view-name*="invitation"]',
            ];
            let cards = [];
            for (const sel of cardContainerSelectors) {
                cards = Array.from(document.querySelectorAll(sel));
                if (cards.length > 0) break;
            }
            if (cards.length > 0) {
                for (const card of cards) {
                    const link = card.querySelector('a[href*="/in/"]');
                    if (link && link.href) {
                        const base = link.href.split('?')[0].split('#')[0];
                        if (!seen.has(base)) { seen.add(base); urls.push(link.href); }
                    }
                }
                if (urls.length > 0) return urls;
            }

            // Strategy 2: fallback — all /in/ links in main content, excluding nav/header
            const mainEl = document.querySelector(
                'main, [role="main"], #main, #main-content, .scaffold-layout__main'
            ) || document.body;
            for (const link of mainEl.querySelectorAll('a[href*="/in/"]')) {
                const href = link.href;
                if (!href) continue;
                const m = href.match(/\/in\/([^\/?#\s]+)/);
                if (!m || m[1].length < 2) continue;
                if (link.closest('nav, header, .global-nav, #global-nav')) continue;
                const base = href.split('?')[0].split('#')[0];
                if (!seen.has(base)) { seen.add(base); urls.push(href); }
            }
            return urls;
        }""")

    async def get_sent_invitation_urls(
        self,
        stop_when_found: Optional[set] = None,
    ) -> InvitationSnapshot:
        """
        Navigate to the invitation manager and return all pending sent invitation URLs.
        Uses a single page load + Show More loop, then extracts all profile URLs via JS.

        Args:
            stop_when_found: Optional set of normalized slugs ('/in/slug'). When all slugs
                in this set appear in the currently loaded page, pagination stops early.
                Pass the set of tracked DB leads to avoid loading hundreds of old invitations.

        Extraction strategy:
          1. Try known invitation card container selectors (fast path).
          2. Fallback: collect all /in/ profile links from the main content area,
             excluding nav/header elements. This survives LinkedIn DOM changes.
        """
        import re as _re

        def _norm(url: str) -> str:
            m = _re.search(r'/in/([^/?#\s]+)', url)
            return f"/in/{m.group(1).rstrip('/')}" if m else ""

        def _extract_slugs_js_sync(raw_urls: list) -> set:
            return {n for u in raw_urls if (n := _norm(u))}

        try:
            nav = await self.navigator.go_to_invitation_manager()
            if not nav.success:
                return InvitationSnapshot(success=False, session_valid=True, urls=[])
            if not nav.session_valid:
                return InvitationSnapshot(success=True, session_valid=False, urls=[])

            # Click "Load more" until all tracked leads are visible or no more pages
            for _ in range(50):
                load_more = await self._find_element(selectors.INVITATION_LOAD_MORE, timeout_ms=2000)
                if not load_more:
                    break

                # Early exit: if we already see all tracked leads, no need to load more
                if stop_when_found:
                    current_urls = await self._extract_invitation_urls()
                    current_slugs = _extract_slugs_js_sync(current_urls)
                    if stop_when_found.issubset(current_slugs):
                        logger.debug(
                            "action.invitation_manager_early_exit",
                            pages_loaded=_,
                            tracked=len(stop_when_found),
                        )
                        return InvitationSnapshot(success=True, session_valid=True, urls=current_urls)

                await load_more.click()
                await self.delay.micro_delay(1.0, 2.0)

            # Extract all profile URLs via JS after loading all pages
            urls = await self._extract_invitation_urls()

            if not urls:
                logger.warning(
                    "action.invitation_manager_empty",
                    note="JS extracted 0 URLs — LinkedIn DOM may have changed selectors",
                    url=self.page.url,
                )
                await self._debug_screenshot("invitation_manager_empty")

            return InvitationSnapshot(success=True, session_valid=True, urls=urls or [])
        except Exception as e:
            logger.warning("action.get_sent_invitation_urls_failed", error=str(e))
            return InvitationSnapshot(success=False, session_valid=True, urls=[])

    async def withdraw_oldest_invitations(self, count: int, already_on_page: bool = False) -> list:
        """
        Withdraw the oldest N invitations from the sent invitations page.
        Returns list of profile URLs that were withdrawn.

        When already_on_page=True, skips navigation and initial load-more loop
        (caller already loaded the page via get_sent_invitation_urls).
        """
        if not already_on_page:
            nav = await self.navigator.go_to_invitation_manager()
            if not nav.success or not nav.session_valid:
                return []

        withdrawn_urls = []

        if not already_on_page:
            # Scroll to load more older invitations
            for _ in range(5):
                load_more = await self._find_element(selectors.INVITATION_LOAD_MORE, timeout_ms=2000)
                if load_more:
                    await load_more.click()
                    await self.delay.micro_delay(1.0, 2.0)
                else:
                    break

        # Find all invitation cards
        cards = None
        card_count = 0
        for sel in selectors.INVITATION_CARDS:
            loc = self.page.locator(sel)
            c = await loc.count()
            if c > 0:
                cards = loc
                card_count = c
                break

        if not cards or card_count == 0:
            return []

        # Process from the last card (oldest) upward
        for i in range(card_count - 1, max(card_count - 1 - count, -1), -1):
            if len(withdrawn_urls) >= count:
                break

            card = cards.nth(i)

            # Extract profile URL
            href = None
            for link_sel in selectors.INVITATION_CARD_PROFILE_LINK:
                link = card.locator(link_sel).first
                if await link.count() > 0:
                    href = await link.get_attribute("href")
                    break

            # Click Withdraw button
            withdraw_btn = None
            for btn_sel in selectors.INVITATION_WITHDRAW_BUTTON:
                btn = card.locator(btn_sel).first
                if await btn.count() > 0:
                    withdraw_btn = btn
                    break

            if not withdraw_btn:
                continue

            await self._hover_and_click(withdraw_btn)
            await self.delay.micro_delay(0.5, 1.0)

            # Confirm in modal
            confirm = await self._find_element(selectors.INVITATION_WITHDRAW_CONFIRM, timeout_ms=3000)
            if confirm:
                await self._hover_and_click(confirm)
                await self.delay.micro_delay(1.0, 2.0)
                if href:
                    withdrawn_urls.append(href)
                logger.info("action.invitation_withdrawn", url=href)

        return withdrawn_urls

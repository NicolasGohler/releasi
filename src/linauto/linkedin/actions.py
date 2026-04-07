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
    INVALID = "invalid"   # Lead data is permanently bad (404, deleted profile)
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


@dataclass
class RecentConnectionsSnapshot:
    success: bool
    session_valid: bool
    # Normalized profile slugs (e.g. "/in/john-doe") of connections made within
    # the cutoff window. Ordered newest-first as returned by the page.
    slugs: list
    # True if the page scroll hit the age cutoff cleanly (vs. hitting load-more
    # exhaustion or an error mid-scroll). Used for logging only.
    hit_cutoff: bool = False


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
        """Find a dropdown menu item by visible text using JavaScript.
        Uses case-insensitive substring match to handle variants like
        'Connect' vs 'Invite X to connect'."""
        idx = await self.page.evaluate("""(text) => {
            const lower = text.toLowerCase();
            const candidates = Array.from(document.querySelectorAll(
                '[role="menuitem"], [role="button"], .artdeco-dropdown__item, li'
            ));
            return candidates.findIndex(el => {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                return el.innerText.trim().toLowerCase().includes(lower);
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

        # Extra settle delay: dropdown items render lazily inside the container.
        # Container becoming visible does not mean all items are in the DOM yet.
        # 400ms covers the typical async render window without adding noticeable delay.
        await asyncio.sleep(0.4)

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

        # 1.25 Check for LinkedIn 404 page.
        # LinkedIn redirects deleted/renamed profiles to /404/ — this is the
        # most reliable signal. Also check page title and body text as fallback.
        current_url = self.page.url
        if "/404" in current_url:
            logger.info("action.profile_not_found", url=profile_url, redirect_url=current_url)
            return ActionResult(ActionStatus.INVALID, reason="profile_not_found")
        page_title = await self.page.title()
        if "Page Not Found" in page_title or "doesn't exist" in page_title.lower():
            logger.info("action.profile_not_found", url=profile_url)
            return ActionResult(ActionStatus.INVALID, reason="profile_not_found")
        not_found_el = await self._try_locator(
            self.page.locator("text=This page doesn't exist").first, timeout_ms=500
        )
        if not_found_el:
            logger.info("action.profile_not_found", url=profile_url)
            return ActionResult(ActionStatus.INVALID, reason="profile_not_found")

        # 1.4 Check for authwall / page-level error overlay.
        # These appear without a URL redirect, so the URL-based session check passes
        # but all profile action buttons are absent or replaced by sign-in elements.
        authwall = await self._find_element(selectors.AUTHWALL_INDICATORS, timeout_ms=800)
        if authwall:
            logger.warning("action.authwall_detected", url=profile_url)
            await self._debug_screenshot("authwall_detected")
            return ActionResult(ActionStatus.SESSION_EXPIRED, reason="authwall_overlay")

        page_error = await self._find_element(selectors.PROFILE_ERROR_PAGE_INDICATORS, timeout_ms=800)
        if page_error:
            logger.warning("action.profile_error_page", url=profile_url)
            await self._debug_screenshot("profile_error_page")
            return ActionResult(ActionStatus.ERROR, reason="profile_error_page", details={"url": profile_url})

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

        # 2. Negative-signal check: "2nd" / "3rd" degree badge means
        #    definitively NOT a 1st-degree connection. Check this first so
        #    later steps can't produce a false-positive "already_connected".
        is_non_first = await self.page.evaluate(r"""() => {
            const main = document.querySelector('main');
            if (!main) return false;
            // Scan the profile header (first <section>) for 2nd/3rd badge
            const header = main.querySelector('section') || main;
            const walker = document.createTreeWalker(header, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const t = node.textContent.trim();
                if (t.length < 15 && /\b(2nd|3rd)\b/.test(t)) return true;
            }
            return false;
        }""")

        # 3. Check if request is pending — must run BEFORE the 1st-degree
        #    check so a pending request is never misclassified as connected.
        pending = await self._find_element(
            selectors.PENDING_CONNECTION_INDICATORS, timeout_ms=3000
        )
        if not pending:
            pending = await self._try_locator(
                self.page.get_by_role("button", name=re.compile(r"Pending", re.IGNORECASE)),
                timeout_ms=1000,
            )
        if pending:
            return ActionResult(ActionStatus.SKIPPED, reason="pending_request")

        # 4. Check if already connected (1st-degree).
        #
        # Only runs when neither a 2nd/3rd badge nor a Pending button was
        # found — both of which are definitive proof the lead is NOT a
        # 1st-degree connection.
        #
        # Primary: JS scan of the profile header section (first <section>)
        # for short text containing "\b1st\b". Restricted to the header to
        # avoid false positives from body content like "1st Team All-American".
        # Secondary: CSS selectors as fallback.
        is_first_degree = False
        if not is_non_first:
            is_first_degree = await self.page.evaluate(r"""() => {
                const main = document.querySelector('main');
                if (!main) return false;
                // Only scan the profile header section, not the full page body
                const header = main.querySelector('section') || main;
                const walker = document.createTreeWalker(header, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    if (t.length < 15 && /\b1st\b/.test(t)) return true;
                }
                return false;
            }""")
            if not is_first_degree:
                css_match = await self._find_element(
                    selectors.ALREADY_CONNECTED_INDICATORS, timeout_ms=1000
                )
                if css_match:
                    is_first_degree = True
        if is_first_degree:
            logger.info("action.already_connected_1st_degree", url=profile_url)
            return ActionResult(ActionStatus.SKIPPED, reason="already_connected")

        # 5. Find Connect button/anchor to confirm profile is connectable
        connect_btn = await self._find_connect_button(profile_url)
        if not connect_btn:
            # Last-resort already-connected check: Message present + Follow absent
            # + Connect absent → 1st-degree connection.
            #
            # Why all three conditions matter:
            #   - Creator profiles show Follow + Message (not connected)
            #   - Open profiles may show Message without Follow (but JS step 2
            #     should have caught genuine 1st-degree already)
            #   - Only 1st-degree shows Message with no Follow and no Connect
            #
            # This is deliberately conservative — if Follow is present we fall
            # through to ERROR so the lead retries tomorrow rather than being
            # permanently marked connected incorrectly.
            has_message = await self._try_locator(
                self.page.locator("main").get_by_role("button", name=re.compile(r"^Message$", re.IGNORECASE)),
                timeout_ms=1500,
            )
            if not has_message:
                has_message = await self._find_element(selectors.MESSAGE_BUTTON, timeout_ms=1000)

            has_follow = await self._try_locator(
                self.page.locator("main").get_by_role("button", name=re.compile(r"^Follow$", re.IGNORECASE)),
                timeout_ms=500,
            )

            if has_message and not has_follow:
                logger.info("action.already_connected_msg_no_follow", url=profile_url)
                return ActionResult(ActionStatus.SKIPPED, reason="already_connected")

            # Use ERROR (not SKIPPED) so the lead re-enters retry logic tomorrow.
            # SKIPPED is permanent; a missing button is often a transient DOM issue.
            return ActionResult(
                ActionStatus.ERROR,
                reason="no_connect_button",
                details={"url": profile_url},
            )

        # 5. Click Connect.
        #
        # The Connect button is now an <a> with href="/preload/custom-invite/?vanityName=..."
        # We MUST NOT navigate to that URL directly — LinkedIn redirects it to /login
        # unless the full SPA session state is present (CSRF tokens, etc.), causing
        # ERR_TOO_MANY_REDIRECTS which corrupts the browser context for the rest of the cycle.
        #
        # Instead: neutralize the href on anchor elements so the browser cannot navigate
        # even if LinkedIn's JS doesn't call e.preventDefault(), then fire el.click().
        # LinkedIn's click handler opens the invite modal overlay on the current page.
        await connect_btn.scroll_into_view_if_needed()
        await self.delay.micro_delay(0.3, 0.7)
        await connect_btn.evaluate("""el => {
            if (el.tagName === 'A') { el.setAttribute('href', 'javascript:void(0)'); }
            el.click();
        }""")
        logger.info("action.connect_clicked", url=profile_url)
        # Modal renders asynchronously — wait for it
        await self.delay.micro_delay(2.0, 3.5)

        # If modal not visible yet, retry once with a dispatched MouseEvent
        # (different low-level path from el.click(), catches edge cases)
        dialog_check = await self._try_locator(
            self.page.locator('[role="dialog"]'), timeout_ms=1000
        )
        if not dialog_check:
            logger.info("action.connect_modal_retry", url=profile_url)
            await connect_btn.evaluate("""el => {
                el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
            }""")
            await self.delay.micro_delay(2.0, 3.0)

        # 6. Handle the invite modal
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
                # Check if disabled because LinkedIn requires email verification
                email_field = await self._try_locator(
                    self.page.locator('input[placeholder*="email" i], input[type="email"]'),
                    timeout_ms=500,
                )
                if not email_field:
                    # Also check for the explanatory text
                    email_field = await self._try_locator(
                        self.page.locator('text=enter their email'),
                        timeout_ms=500,
                    )
                if email_field:
                    logger.info("action.email_verification_required", url=profile_url)
                    await self._debug_screenshot("email_required")
                    return ActionResult(
                        ActionStatus.SKIPPED,
                        reason="email_required",
                        details={"url": profile_url},
                    )
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
            await self._dump_buttons_debug()
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

    @staticmethod
    def _parse_connection_age_hours(text: str) -> Optional[float]:
        """
        Parse LinkedIn's connection timestamp text into hours since connection.

        LinkedIn renders connection dates as:
          "Connected on April 7, 2026"  → hours since that date
          "Connected on April 6, 2026"  → hours since that date

        Legacy relative formats (kept for robustness):
          "just now" / "moments ago"    → 0
          "1 hour ago" / "3 hours ago"  → 1 / 3
          "1 day ago" / "2 days ago"    → 24 / 48
          "1 week ago" / "2 weeks ago"  → 168 / 336
          "January 2025" / month-only   → very large (treat as old, stop)

        Returns None if the text cannot be parsed (treated as very old → stop).
        """
        from datetime import datetime as _dt
        if not text:
            return None
        t = text.strip()

        # Primary format: "Connected on April 7, 2026"
        if t.lower().startswith("connected on "):
            date_str = t[len("connected on "):].strip()
            try:
                conn_date = _dt.strptime(date_str, "%B %d, %Y")
                return (_dt.now() - conn_date).total_seconds() / 3600
            except ValueError:
                pass

        t_lower = t.lower()
        # "just now" / "moments ago"
        if "just now" in t_lower or "moment" in t_lower:
            return 0.0
        # Hours: "1 hour ago", "3 hours ago"
        m = re.search(r'(\d+)\s+hour', t_lower)
        if m:
            return float(m.group(1))
        # Days: "1 day ago", "2 days ago"
        m = re.search(r'(\d+)\s+day', t_lower)
        if m:
            return float(m.group(1)) * 24
        # Weeks: "1 week ago", "2 weeks ago"
        m = re.search(r'(\d+)\s+week', t_lower)
        if m:
            return float(m.group(1)) * 168
        # Months / years / unparseable absolute dates → treat as very old
        return None

    async def get_recent_connections(
        self,
        cutoff_hours: float = 30.0,
        max_scrolls: int = 20,
    ) -> RecentConnectionsSnapshot:
        """
        Scroll the connections page (sorted by recently added) and return profile
        slugs of connections made within the last `cutoff_hours` hours.

        Stops scrolling as soon as it encounters a card older than the cutoff,
        so the number of page interactions scales with new connections only —
        not total connection count.

        Args:
            cutoff_hours: Stop when a connection is older than this. Default 30h
                gives a safe overlap with the daily run cadence (runs at 10 AM,
                cutoff 30h covers yesterday's 10 AM run + 6h buffer).
            max_scrolls: Safety cap on "Show more" clicks to avoid infinite loops.

        Returns RecentConnectionsSnapshot with slugs of recent connections.
        """
        def _norm(url: str) -> Optional[str]:
            m = re.search(r'/in/([^/?#\s]+)', url)
            return f"/in/{m.group(1).rstrip('/')}" if m else None

        try:
            nav = await self.navigator.go_to_connections()
            if not nav.success:
                logger.warning("action.connections_page_failed", error=nav.error)
                return RecentConnectionsSnapshot(success=False, session_valid=True, slugs=[])
            if not nav.session_valid:
                return RecentConnectionsSnapshot(success=False, session_valid=False, slugs=[])

            await self.delay.micro_delay(1.5, 3.0)

            collected_slugs: list = []
            hit_cutoff = False

            seen_slugs: set = set()

            for scroll_n in range(max_scrolls):
                # Extract all currently visible connection cards via JS.
                # Strategy: anchor on <p> elements whose text starts with
                # "Connected on" (LinkedIn's current format), then walk up to
                # find the nearest ancestor containing a /in/ profile link.
                # This is resilient to LinkedIn's obfuscated CSS class names.
                cards_data = await self.page.evaluate("""
                    () => {
                        const results = [];
                        const seenHrefs = new Set();

                        // Find all "Connected on" text nodes in <p> elements
                        const paras = document.querySelectorAll('p, span, div');
                        for (const el of paras) {
                            const text = (el.innerText || el.textContent || '').trim();
                            if (!text.toLowerCase().startsWith('connected on ')) continue;

                            // Walk up up to 6 levels to find a container with a /in/ link
                            let container = el.parentElement;
                            for (let i = 0; i < 6; i++) {
                                if (!container) break;
                                const link = container.querySelector('a[href*="/in/"]');
                                if (link) {
                                    const href = link.getAttribute('href');
                                    if (href && !seenHrefs.has(href)) {
                                        seenHrefs.add(href);
                                        results.push({ href, timeText: text });
                                    }
                                    break;
                                }
                                container = container.parentElement;
                            }
                        }
                        return results;
                    }
                """)

                if not cards_data:
                    logger.warning(
                        "action.connections_page_no_cards",
                        scroll_n=scroll_n,
                    )
                    await self._debug_screenshot("connections_page_no_cards")
                    break

                # Process cards in page order (newest first).
                # Once we see a card older than cutoff, stop — everything after
                # is guaranteed older since the list is sorted newest-first.
                prev_count = len(collected_slugs)
                for card in cards_data:
                    href = card.get("href") or ""
                    slug = _norm(href)
                    if not slug or slug in seen_slugs:
                        continue
                    seen_slugs.add(slug)

                    time_text = card.get("timeText") or ""
                    age_hours = self._parse_connection_age_hours(time_text)

                    if age_hours is None or age_hours > cutoff_hours:
                        # Hit a card that's too old (or unparseable) — we're done
                        hit_cutoff = True
                        logger.debug(
                            "action.connections_cutoff_reached",
                            slug=slug,
                            time_text=time_text,
                            age_hours=age_hours,
                            collected=len(collected_slugs),
                        )
                        break

                    collected_slugs.append(slug)

                if hit_cutoff:
                    break

                # Scroll down to trigger infinite-scroll loading of more cards
                prev_height = await self.page.evaluate("document.body.scrollHeight")
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.delay.micro_delay(2.0, 3.5)
                new_height = await self.page.evaluate("document.body.scrollHeight")

                if new_height == prev_height:
                    # Page height didn't grow — no more content to load
                    logger.debug(
                        "action.connections_page_exhausted",
                        collected=len(collected_slugs),
                        scrolls=scroll_n,
                    )
                    break

                # Also stop if this scroll yielded no new slugs (stale loop guard)
                if len(collected_slugs) == prev_count and scroll_n > 0:
                    logger.debug(
                        "action.connections_no_new_cards",
                        scroll_n=scroll_n,
                        collected=len(collected_slugs),
                    )
                    break

            logger.info(
                "action.connections_scraped",
                collected=len(collected_slugs),
                hit_cutoff=hit_cutoff,
                cutoff_hours=cutoff_hours,
            )
            return RecentConnectionsSnapshot(
                success=True,
                session_valid=True,
                slugs=collected_slugs,
                hit_cutoff=hit_cutoff,
            )

        except Exception as e:
            logger.warning("action.get_recent_connections_failed", error=str(e))
            return RecentConnectionsSnapshot(success=False, session_valid=True, slugs=[])

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

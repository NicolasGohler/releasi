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

from releasi.linkedin.navigator import LinkedInNavigator
from releasi.linkedin.detector import LimitDetector, DetectionResult, DetectionType
from releasi.linkedin import selectors
from releasi.safety.delays import DelayGenerator

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
        Uses word-boundary matching: item text must equal the query exactly,
        start with it, or end with it — but NOT just contain it as a substring
        (e.g. 'Remove connection' must NOT match 'Connect')."""
        idx = await self.page.evaluate("""(text) => {
            const lower = text.toLowerCase();
            const candidates = Array.from(document.querySelectorAll(
                '[role="menuitem"], [role="button"], .artdeco-dropdown__item, li'
            ));
            return candidates.findIndex(el => {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const t = el.innerText.trim().toLowerCase();
                // Exact match, starts-with, or ends-with — but not arbitrary substring.
                // This prevents 'Remove connection' matching a search for 'Connect'.
                return t === lower || t.startsWith(lower + ' ') || t.endsWith(' ' + lower);
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

        # ── Guard: if 'Remove connection' is visible, this profile is already
        # connected — return a sentinel value so the caller knows to skip.
        # Check this BEFORE searching for Connect to avoid misclassifying
        # 'Remove connection' as the Connect target (both contain "connect").
        remove_conn = await self._try_locator(
            self.page.get_by_role("menuitem", name=re.compile(r"Remove connection", re.IGNORECASE)),
            timeout_ms=500,
        )
        if not remove_conn:
            remove_conn = await self.page.evaluate("""() => {
                return Array.from(document.querySelectorAll('[role="menuitem"], .artdeco-dropdown__item'))
                    .some(el => el.innerText.trim().toLowerCase().startsWith('remove connection'));
            }""")
        if remove_conn:
            logger.info("action.remove_connection_in_dropdown_already_connected", url=profile_url)
            return "already_connected"  # sentinel — caller checks for this string

        # Find Connect in the dropdown
        connect_btn = None

        # Role-based: try menuitem and listitem roles.
        # Pattern: "Connect" exactly, "Connect " prefix, or " connect" suffix —
        # avoids matching "Remove connection" which also contains "connect".
        for role in ["menuitem", "listitem"]:
            connect_btn = await self._try_locator(
                self.page.get_by_role(role, name=re.compile(r"^Connect(\s|$)|to connect$", re.IGNORECASE)),
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
            # Per-profile authwall: LinkedIn restricts viewing this profile for
            # accounts with few connections. Session is valid; skip this lead.
            if nav.error == "authwall_per_profile":
                logger.info("action.authwall_per_profile", url=profile_url)
                return ActionResult(ActionStatus.SKIPPED, reason="authwall_per_profile")
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
        # The navigator already verified via feed-ping that per-profile autchwalls are
        # returned as SKIPPED; if we still see an authwall here the session is truly dead.
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
            from releasi.linkedin.profile_filter import ProfileFilter
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
        if connect_btn == "already_connected":
            logger.info("action.already_connected_remove_in_dropdown", url=profile_url)
            return ActionResult(ActionStatus.SKIPPED, reason="already_connected")
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
                # Fallback: on the current LinkedIn UI the profile Message CTA is an
                # <a> pointing at /messaging/compose/?recipient=<URN>. Its presence
                # is a reliable 1st-degree signal.
                has_message = await self._try_locator(
                    self.page.locator('a[href*="/messaging/compose/"][href*="recipient="]'),
                    timeout_ms=1000,
                )

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
        # Two cases depending on where the button came from:
        #
        # a) Primary anchor (<a href="/preload/custom-invite/?vanityName=...">):
        #    MUST NOT navigate to that URL directly — LinkedIn redirects to /login unless
        #    the full SPA session state is present, causing ERR_TOO_MANY_REDIRECTS that
        #    corrupts the browser context. Neutralize the href then fire el.click() so
        #    LinkedIn's own click handler opens the invite modal in-place.
        #    scroll_into_view_if_needed() is safe here because the anchor is in the main DOM.
        #
        # b) Dropdown menuitem (role="menuitem", typically a <button> or <li>):
        #    Must NOT call scroll_into_view_if_needed() — scrolling the page closes LinkedIn's
        #    More dropdown before the click fires, so the invite modal never opens (the element
        #    is already in the viewport since we just clicked it open). Use Playwright's native
        #    hover+click instead, which dispatches full pointer events without scrolling.
        is_anchor = await connect_btn.evaluate("el => el.tagName === 'A'")
        if is_anchor:
            await connect_btn.scroll_into_view_if_needed()
            await self.delay.micro_delay(0.3, 0.7)
            await connect_btn.evaluate("""el => {
                el.setAttribute('href', 'javascript:void(0)');
                el.click();
            }""")
        else:
            await self.delay.micro_delay(0.3, 0.7)
            await self._hover_and_click(connect_btn)
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
            try:
                await connect_btn.evaluate("""el => {
                    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                }""")
            except Exception as retry_err:
                # connect_btn may be stale (e.g. More dropdown closed, menuitem removed from DOM).
                # Log and fall through — the send_btn search below will either find the modal or
                # reach connect_modal_not_opened, which is the correct outcome for both cases.
                logger.debug("action.connect_modal_retry_stale", error=str(retry_err)[:120])
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
            # Check if the open dialog is a "Remove connection" confirmation —
            # this means we accidentally clicked "Remove connection" in the More
            # dropdown instead of "Connect" (misclassified as Connect button).
            # Treat as already connected so lead is marked SKIPPED, not ERROR.
            is_removal_dialog = await self.page.evaluate("""() => {
                const d = document.querySelector('[role="dialog"]');
                if (!d) return false;
                const t = d.innerText.toLowerCase();
                return t.includes('remove') && t.includes('connection');
            }""")
            if is_removal_dialog:
                logger.warning("action.removal_dialog_instead_of_invite", url=profile_url)
                # Close the dialog to leave the page clean
                try:
                    close_btn = await self._try_locator(
                        self.page.locator('[role="dialog"] button[aria-label*="Dismiss" i], [role="dialog"] button[aria-label*="Cancel" i], [role="dialog"] button:has-text("Cancel")'),
                        timeout_ms=1000,
                    )
                    if close_btn:
                        await close_btn.click()
                except Exception:
                    pass
                return ActionResult(ActionStatus.SKIPPED, reason="already_connected")

            # Check for LinkedIn's "How do you know [Person]?" dialog — shown for
            # high-profile / creator-mode accounts. LinkedIn requires selecting a
            # relationship type (Colleague, Classmate, etc.) before allowing an
            # invitation. We don't automate relationship selection, so skip cleanly.
            is_how_do_you_know = await self.page.evaluate("""() => {
                const d = document.querySelector('[role="dialog"]');
                if (!d) return false;
                const t = d.innerText.toLowerCase();
                return (
                    t.includes('how do you know') ||
                    t.includes('how do you two know') ||
                    (t.includes('colleague') && t.includes('classmate')) ||
                    t.includes('tell them how you know')
                );
            }""")
            if is_how_do_you_know:
                logger.info("action.how_do_you_know_dialog", url=profile_url)
                await self._debug_screenshot("how_do_you_know")
                try:
                    close_btn = await self._try_locator(
                        self.page.locator('[role="dialog"] button[aria-label*="Dismiss" i], [role="dialog"] button[aria-label*="Close" i]'),
                        timeout_ms=1000,
                    )
                    if close_btn:
                        await close_btn.click()
                except Exception:
                    pass
                return ActionResult(
                    ActionStatus.SKIPPED,
                    reason="how_do_you_know_required",
                    details={"url": profile_url},
                )

            # Before giving up, check if the invitation was sent via LinkedIn's
            # direct-send flow (no modal — the request went out immediately on
            # clicking Connect). If the profile now shows "Pending", the send
            # actually succeeded.
            for sel in selectors.PENDING_CONNECTION_INDICATORS:
                try:
                    pending_el = await self.page.locator(sel).first.element_handle(timeout=1000)
                    if pending_el:
                        logger.info("action.direct_send_detected", url=profile_url)
                        return ActionResult(
                            ActionStatus.SUCCESS,
                            details={"url": profile_url, "via": "direct_send"},
                        )
                except Exception:
                    pass

            await self._dump_buttons_debug()
            await self._debug_screenshot("send_btn_missing")
            # The Connect click fired but no modal appeared and the profile is
            # still in the unconnected state (no Pending badge). This happens
            # reliably for Follow-primary profiles where LinkedIn does not open
            # the invite modal from the More-dropdown Connect option. Treat as
            # SKIPPED (not ERROR) so the lead doesn't pollute error stats and
            # the session counter is unaffected.
            return ActionResult(
                ActionStatus.SKIPPED,
                reason="connect_modal_not_opened",
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

    async def send_message(self, profile_url: str, message: str, skip_prior_conversation_check: bool = False) -> ActionResult:
        """
        Send a direct message to a 1st-degree connection.

        Flow (rev. 2026-04, updated 2026-04-25):
          1. Navigate to the profile, extract the recipient URN from any
             `a[href*="/messaging/compose/"][href*="recipient="]` anchor.
          2. Navigate directly to `/messaging/compose/?recipient=<URN>`.
             This avoids clicking the profile Message CTA (which on the
             current LinkedIn UI is an <a> that depends on JS hydration
             that doesn't always fire under our resource blocker).
          3. Type into the `msg-form__contenteditable` input.
          4. Click the Send button (msg-form__send-button) — LinkedIn's compose
             page renders a Send button that is disabled until text is typed.
             Pressing Enter creates a new line, NOT a send.
          5. Verify by checking navigation away from /messaging/compose/.
        """
        # ── 1. Visit profile, extract recipient URN ──
        nav = await self.navigator.go_to_profile(profile_url)
        if not nav.success:
            return ActionResult(ActionStatus.ERROR, reason=f"Navigation failed: {nav.error}")
        if not nav.session_valid:
            return ActionResult(ActionStatus.SESSION_EXPIRED)

        recipient_urn = await self.page.evaluate("""
() => {
    const a = document.querySelector('a[href*="/messaging/compose/"][href*="recipient="]');
    if (!a) return null;
    try {
        const url = new URL(a.href, location.origin);
        return url.searchParams.get('recipient');
    } catch (e) { return null; }
}
""")
        if not recipient_urn:
            # No compose link means not a 1st-degree connection (or LinkedIn
            # changed the DOM). Either way, we can't message this person.
            return ActionResult(ActionStatus.ERROR, reason="recipient_urn_not_found")

        # ── 2. Direct navigation to the compose page ──
        import urllib.parse
        compose_url = (
            "https://www.linkedin.com/messaging/compose/?recipient="
            + urllib.parse.quote(recipient_urn, safe="")
        )
        try:
            await self.page.goto(compose_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            return ActionResult(ActionStatus.ERROR, reason=f"compose_nav_failed: {e}")

        # Wait for the messaging app to hydrate (it's a JS-heavy widget)
        await self.delay.micro_delay(3.5, 5.0)

        # ── 3a. Guard: skip if a prior conversation already exists ──
        # LinkedIn renders existing message bubbles in the thread area below the
        # compose form.  If any are present we have already exchanged messages
        # with this person (inside or outside our system) and should not send
        # another automated one.
        # skip_prior_conversation_check=True when this is not the first message
        # in a multi-message sequence — the bubbles are ones we just sent.
        if not skip_prior_conversation_check:
            try:
                has_prior_messages = await self.page.evaluate("""
() => {
    const bubbles = document.querySelectorAll(
        '.msg-s-event-listitem, [class*="msg-s-event-listitem"]'
    );
    return bubbles.length > 0;
}
""")
                if has_prior_messages:
                    logger.info(
                        "action.message_skipped_existing_conversation",
                        url=profile_url,
                    )
                    return ActionResult(
                        ActionStatus.SKIPPED,
                        reason="existing_conversation",
                    )
            except Exception:
                pass  # Fail open — don't block the send if the check errors

        # ── 3. Find message input ──
        msg_input = await self._find_element(selectors.MESSAGE_INPUT, timeout_ms=8000)
        if not msg_input:
            return ActionResult(ActionStatus.ERROR, reason="message_input_not_found")

        await msg_input.click()
        await self.delay.micro_delay(0.3, 0.6)
        await self.delay.type_text(msg_input, message)
        await self.delay.micro_delay(0.6, 1.2)

        # ── 4. Send the message ──
        # LinkedIn has two compose modes that change which send mechanism works:
        #
        #   "Click to Send" mode  — renders button.msg-form__send-button (disabled
        #     until text is typed).  Enter inserts a newline.
        #   "Press Enter to Send" mode — no dedicated send button visible.
        #     The msg-form__hint-text reads "Press Enter to Send".
        #     Enter submits the message.  The toggle button (msg-form__send-toggle)
        #     switches between modes.
        #
        # LinkedIn sometimes changes the per-account default.  We try the button
        # first (short timeout), then fall back to pressing Enter.
        send_btn = await self._find_element(selectors.MESSAGE_SEND_BUTTON, timeout_ms=3000)
        if send_btn:
            await self._hover_and_click(send_btn)
            logger.debug("action.send_via_button", url=profile_url)
        else:
            # No dedicated send button — check we're in "Press Enter to Send" mode
            # before committing to pressing Enter (guards against selector rot).
            in_enter_mode = await self.page.evaluate("""() => {
                const hint = document.querySelector('.msg-form__hint-text');
                if (!hint) return false;
                const txt = (hint.innerText || hint.textContent || '').toLowerCase();
                return txt.includes('enter') && txt.includes('send');
            }""")
            if not in_enter_mode:
                logger.error("action.send_btn_not_found", url=profile_url)
                return ActionResult(ActionStatus.ERROR, reason="message_send_btn_not_found")
            await self.delay.micro_delay(0.3, 0.6)
            await self.page.keyboard.press("Enter")
            logger.debug("action.send_via_enter", url=profile_url)

        # ── 5. Verify submission ──
        # PRIMARY signal: LinkedIn navigates from /messaging/compose/ → /messaging/thread/<id>/
        # after a successful send. Wait up to 8 s for that navigation.
        sent_ok = False
        input_cleared = False
        bubble_found = False
        try:
            await self.page.wait_for_url(
                lambda url: "messaging/compose" not in url,
                timeout=8000,
            )
            sent_ok = True
            logger.debug("action.message_sent_navigated_to_thread", url=profile_url)
        except Exception:
            pass

        if not sent_ok:
            # Page stayed on compose — check if input cleared (some UI variants
            # send without navigating away).
            await self.delay.micro_delay(1.0, 2.0)
            try:
                input_cleared = await msg_input.evaluate(
                    "el => (el.innerText || '').trim() === ''"
                )
                if input_cleared:
                    sent_ok = True
            except Exception:
                # StaleElementReferenceError = element detached = page navigated.
                # That also means the send went through.
                sent_ok = True
                input_cleared = True

        if not sent_ok:
            # Last-resort: scan for the message text in DOM bubbles
            try:
                msg_literal = (
                    message.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
                )
                bubble_found = await self.page.evaluate(
                    "(() => { const txt = '" + msg_literal + "';"
                    " const bubbles = document.querySelectorAll('.msg-s-event-listitem, [class*=\"msg-s-event\"]');"
                    " for (const b of bubbles) { if ((b.innerText || '').includes(txt)) return true; }"
                    " return false; })()"
                )
                if bubble_found:
                    sent_ok = True
            except Exception:
                pass

        if not sent_ok:
            logger.warning(
                "action.message_send_unverified",
                url=profile_url, page_url=self.page.url,
            )
            return ActionResult(
                ActionStatus.ERROR,
                reason="message_send_unverified",
                details={"page_url": self.page.url},
            )

        # ── 6. Post-send detection check ──
        # IMPORTANT: At this point the message has already been delivered.
        # Mirror the same logic as send_connection_request — CAPTCHA after a
        # confirmed send means the message went through; treat as success.
        # Only rate-limit signals and session expiry should override.
        detection = await self.detector.check_after_action(self.page)
        if detection.requires_cooldown:
            # Rate limit signal — may indicate the send was blocked.
            return ActionResult(
                ActionStatus.LIMIT_REACHED,
                reason=detection.detected.value,
                details={"detection": detection.details},
            )
        if detection.detected == DetectionType.CAPTCHA:
            # CAPTCHA appeared on the thread/redirect page AFTER the message
            # was delivered. Log a warning but treat as success — retrying
            # would send a duplicate message.
            logger.warning(
                "action.captcha_after_message_send",
                url=profile_url,
                note="Message delivered; CAPTCHA appeared after send",
            )
        elif detection.detected == DetectionType.SESSION_EXPIRED:
            return ActionResult(ActionStatus.SESSION_EXPIRED)
        elif not detection.is_clear:
            return ActionResult(
                ActionStatus.ERROR,
                reason=detection.detected.value,
                details={"detection": detection.details},
            )

        logger.info(
            "action.message_sent",
            url=profile_url,
            input_cleared=input_cleared,
            bubble_found=bubble_found,
        )
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
        """Navigate to invitation manager and return the People count from the filter pill.

        go_to_invitation_manager() already clicks the People pill and waits 3s,
        so the pill text is available immediately after navigation returns.
        """
        nav = await self.navigator.go_to_invitation_manager()
        if not nav.success or not nav.session_valid:
            return -1

        count = await self.page.evaluate(selectors.INVITATION_PENDING_COUNT_JS)
        if count is not None:
            return count

        # Fallback: count visible withdraw anchors on the first loaded page
        n = await self.page.locator(selectors.INVITATION_WITHDRAW_ANCHOR).count()
        return n if n > 0 else 0

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

        Relative formats (kept for robustness — LinkedIn has rendered these
        historically and may again, especially during A/B tests):
          "just now" / "moments ago"          → 0
          "a few seconds ago" / "30 seconds"  → 0
          "a minute ago" / "5 minutes ago"    → 1/60 / 5/60
          "an hour ago" / "8 hours ago"       → 1 / 8
          "yesterday"                         → 24
          "a day ago" / "3 days ago"          → 24 / 72
          "a week ago" / "2 weeks ago"        → 168 / 336
          "January 2025" / month-only         → None (treat as old, stop)

        Returns None if the text cannot be parsed (treated as very old → stop).
        """
        from datetime import datetime as _dt
        if not text:
            return None
        t = text.strip()
        t_lower = t.lower()

        # Primary format: "Connected on April 7, 2026"
        if t_lower.startswith("connected on "):
            date_str = t[len("connected on "):].strip()
            try:
                conn_date = _dt.strptime(date_str, "%B %d, %Y")
                return (_dt.now() - conn_date).total_seconds() / 3600
            except ValueError:
                pass

        # "just now" / "moments ago" / "a few seconds ago"
        if "just now" in t_lower or "moment" in t_lower or "second" in t_lower:
            return 0.0

        # "yesterday" — treat as 24h
        if "yesterday" in t_lower:
            return 24.0

        # Normalize "a/an <unit>" → "1 <unit>" so the numeric regexes match.
        # Word boundaries prevent matching inside other words.
        t_norm = re.sub(r'\b(a|an)\s+(minute|hour|day|week)\b', r'1 \2', t_lower)

        # Minutes: "5 minutes ago", "1 minute ago"
        m = re.search(r'(\d+)\s+minute', t_norm)
        if m:
            return float(m.group(1)) / 60.0
        # Hours: "1 hour ago", "3 hours ago"
        m = re.search(r'(\d+)\s+hour', t_norm)
        if m:
            return float(m.group(1))
        # Days: "1 day ago", "2 days ago"
        m = re.search(r'(\d+)\s+day', t_norm)
        if m:
            return float(m.group(1)) * 24
        # Weeks: "1 week ago", "2 weeks ago"
        m = re.search(r'(\d+)\s+week', t_norm)
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

                        // Match either the current absolute format
                        // ("Connected on April 7, 2026") or any relative
                        // phrasing LinkedIn may render ("5 minutes ago",
                        // "8 hours ago", "yesterday", "3 days ago", etc.).
                        // Keeping both patterns makes the scraper robust to
                        // A/B tests and UI changes.
                        const RELATIVE_RX = /(just now|moments? ago|yesterday|\\b(?:a|an|\\d+)\\s+(?:second|minute|hour|day|week)s?\\s+ago)/i;
                        const isTimeText = (s) => {
                            const lower = s.toLowerCase();
                            if (lower.startsWith('connected on ')) return true;
                            return RELATIVE_RX.test(lower);
                        };

                        const paras = document.querySelectorAll('p, span, div, time');
                        for (const el of paras) {
                            const text = (el.innerText || el.textContent || '').trim();
                            if (!text || text.length > 80) continue;
                            if (!isTimeText(text)) continue;

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

                    if age_hours is None:
                        # Unparseable date — likely a non-English LinkedIn locale
                        # (e.g. "Connecté le 22 avril 2026").  Skip this card
                        # rather than treating it as "very old" and stopping the
                        # whole scrape; the next card might have a parseable date.
                        logger.info(
                            "action.connections_unparseable_date",
                            slug=slug,
                            time_text=time_text,
                        )
                        continue

                    if age_hours > cutoff_hours:
                        # First card older than the cutoff — list is sorted newest-
                        # first, so everything after this is older too.  Stop.
                        hit_cutoff = True
                        logger.info(
                            "action.connections_cutoff_reached",
                            slug=slug,
                            time_text=time_text,
                            age_hours=round(age_hours, 1),
                            cutoff_hours=cutoff_hours,
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

            # Click "Load more" until all tracked leads are visible or no more pages.
            # After each click, scroll to bottom to trigger lazy rendering, then
            # wait up to 6s for the next button — LinkedIn re-renders it after the
            # new batch loads, which can take 3-5s on a slow proxy.
            prev_url_count = 0
            for _ in range(50):
                # Scroll to bottom to ensure the Load more button is in view / rendered
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.delay.micro_delay(0.5, 1.0)

                load_more = await self._find_element(selectors.INVITATION_LOAD_MORE, timeout_ms=6000)
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
                await self.delay.micro_delay(2.0, 3.5)

                # Stale-page guard: if URL count hasn't grown after a click, stop
                current_count = len(await self._extract_invitation_urls())
                if current_count == prev_url_count and _ > 0:
                    logger.debug("action.invitation_manager_stale", page=_, count=current_count)
                    break
                prev_url_count = current_count

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
        """Backwards-compat wrapper — delegates to withdraw_invitations(order='oldest')."""
        return await self.withdraw_invitations(count, order="oldest", already_on_page=already_on_page)

    async def withdraw_invitations(
        self,
        count: int,
        order: str = "oldest",
        already_on_page: bool = False,
    ) -> list:
        """
        Withdraw N invitations via LinkedIn's SDUI pagination API.
        order='oldest' → targets oldest sent invitations first (highest startIndex).
        order='newest' → targets newest sent invitations first (startIndex=0).
        Returns list of profile URLs that were withdrawn (for DB lead marking).

        Uses SDUI pagination (POST /flagship-web/rsc-action/actions/pagination) so
        we never load thousands of DOM nodes — avoids the OOM kill that the old
        DOM-scroll approach caused on the 3 GB host.
        """
        if not already_on_page:
            nav = await self.navigator.go_to_invitation_manager()
            if not nav.success or not nav.session_valid:
                return []

        # ── Extract CSRF token ────────────────────────────────────────────────
        csrf = ""
        for ck in await self.page.context.cookies():
            if ck["name"] == "JSESSIONID":
                csrf = ck["value"].strip('"')
                break
        if not csrf:
            logger.warning("action.withdraw_no_csrf")
            return []

        # ── Read pending count from DOM (People pill) ────────────────────────
        dom_total: int = await self.page.evaluate("""() => {
            for (const el of document.querySelectorAll('a,li,span,button,[role="tab"]')) {
                const m = (el.textContent||'').match(/^\\s*People\\s*\\((\\d[\\d,]*)\\)\\s*$/i);
                if (m) return parseInt(m[1].replace(/,/g,''), 10);
            }
            return 0;
        }""")
        logger.debug("action.withdraw_dom_total", total=dom_total)

        if dom_total == 0:
            logger.warning("action.withdraw_zero_total")
            return []

        # ── Build SDUI pagination payload ────────────────────────────────────
        PAGINATION_URL = (
            "/flagship-web/rsc-action/actions/pagination"
            "?sduiid=com.linkedin.sdui.pagers.mynetwork.invitationsList"
        )
        WITHDRAW_URL = (
            "/flagship-web/rsc-action/actions/server-request"
            "?sduiid=com.linkedin.sdui.requests.mynetwork.addaWithdrawInvitation"
        )
        SDUI_HEADERS = {
            "csrf-token": csrf,
            "content-type": "application/json",
            "accept": "*/*",
        }

        def _build_pag_payload(start_idx: int) -> dict:
            """Build a fresh pagination payload for the given startIndex."""
            payload_inner = {
                "startIndex": start_idx,
                "invitationTypeEnum": ["GenericInvitationType_CONNECTION"],
                "invitationClassificationTypes": [],
                "filterCriteriaEnum": "FilterCriteria_UNKNOWN",
                "invitationDirectionEnum": "PendingInvitationDirection_SENT",
            }
            inner: dict = {
                "$type": "proto.sdui.actions.requests.RequestedArguments",
                "payload": payload_inner,
                "requestedStateKeys": [],
                "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                "states": [],
                "screenId": "com.linkedin.sdui.flagshipnav.mynetwork.invitations.InvitationSentWithType",
            }
            return {
                "pagerId": "com.linkedin.sdui.pagers.mynetwork.invitationsList",
                "clientArguments": inner,
                "paginationRequest": {
                    "$type": "proto.sdui.actions.requests.PaginationRequest",
                    "pagerId": "com.linkedin.sdui.pagers.mynetwork.invitationsList",
                    "requestedArguments": {
                        "$type": "proto.sdui.actions.requests.RequestedArguments",
                        "payload": payload_inner,
                        "requestedStateKeys": [],
                        "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                    },
                    "trigger": {
                        "$case": "itemDistanceTrigger",
                        "itemDistanceTrigger": {
                            "$type": "proto.sdui.actions.requests.ItemDistanceTrigger",
                            "preloadDistance": 3,
                            "preloadLength": 250,
                        },
                    },
                    "retryCount": 2,
                },
            }

        # Fetch multiple pages until we have `count` IDs.
        # LinkedIn stores invitations newest-first: startIndex=0 → newest,
        # startIndex=(total-page_size) → oldest.
        # For "oldest" order we start from the far end and work backwards.
        # For "newest" we start from 0 and work forwards.
        # Within each page the RSC response is in LinkedIn's natural order
        # (newest-first); for "oldest" we reverse within each page.
        page_size = 10
        all_pairs: list[tuple] = []
        seen_ids: set = set()
        pages_needed = (count + page_size - 1) // page_size  # ceil

        for page_num in range(pages_needed):
            if len(all_pairs) >= count:
                break

            if order == "newest":
                start_idx = page_num * page_size
            else:  # oldest
                start_idx = max(0, dom_total - page_size - (page_num * page_size))

            pag_result = await self.page.evaluate(
                """async ([url, hdrs, body]) => {
                    try {
                        const r = await fetch(url, {
                            method: "POST", headers: hdrs,
                            credentials: "include", body: JSON.stringify(body),
                        });
                        return {status: r.status, body: await r.text()};
                    } catch (e) { return {error: e.message}; }
                }""",
                [PAGINATION_URL, SDUI_HEADERS, _build_pag_payload(start_idx)],
            )

            if pag_result.get("error") or pag_result.get("status") != 200:
                if page_num == 0:
                    # SDUI endpoint unavailable on first page — fall back to DOM.
                    # Cap at 20 to avoid OOM on large invitation lists.
                    logger.warning(
                        "action.withdraw_pagination_failed",
                        status=pag_result.get("status"),
                        error=pag_result.get("error"),
                    )
                    logger.info("action.withdraw_sdui_fallback_dom", count=count)
                    return await self._withdraw_invitations_dom(min(count, 20), order=order)
                else:
                    logger.warning(
                        "action.withdraw_pagination_page_failed",
                        page=page_num, status=pag_result.get("status"),
                        error=pag_result.get("error"), collected=len(all_pairs),
                    )
                    break

            rsc_body = pag_result.get("body", "")

            # Extract invitation IDs and profile slugs from RSC body.
            # Both appear in document order matching the pagination page.
            # The RSC response packs all IDs in modelStates first, then card
            # components (with profile /in/ URLs) follow — same count, same order.
            inv_ids = list(dict.fromkeys(
                re.findall(r'InvitationUrn\(invitationId=(\d+)\)', rsc_body)
            ))
            slugs = list(dict.fromkeys(
                re.findall(r'"https://www\.linkedin\.com/in/([^/"\\]+)', rsc_body)
            ))

            if not inv_ids:
                if page_num == 0:
                    logger.warning("action.withdraw_no_ids", body_len=len(rsc_body))
                    return []
                else:
                    logger.debug("action.withdraw_page_empty", page=page_num)
                    break

            # Pair IDs with slugs by position; fall back to no-slug if counts differ.
            if len(inv_ids) == len(slugs):
                page_pairs = list(zip(inv_ids, slugs))
            else:
                logger.warning(
                    "action.withdraw_pairing_mismatch",
                    page=page_num, n_ids=len(inv_ids), n_slugs=len(slugs),
                )
                page_pairs = [(iid, "") for iid in inv_ids]

            # For oldest order the RSC returns newest-at-bottom within each page;
            # reverse so the oldest items from this page come first.
            if order == "oldest":
                page_pairs = list(reversed(page_pairs))

            # Deduplicate across pages (RSC responses can overlap near page boundaries).
            for pair in page_pairs:
                if pair[0] not in seen_ids:
                    seen_ids.add(pair[0])
                    all_pairs.append(pair)

            logger.debug(
                "action.withdraw_page_fetched",
                page=page_num, start_idx=start_idx,
                page_ids=len(inv_ids), total_collected=len(all_pairs),
            )

            # Brief delay between pagination requests to avoid rate-limiting.
            if page_num < pages_needed - 1 and len(all_pairs) < count:
                await asyncio.sleep(1.5)

        if not all_pairs:
            logger.warning("action.withdraw_no_ids_after_pages", pages_fetched=pages_needed)
            return []

        pairs = all_pairs[:count]

        # ── Withdraw each invitation via SDUI server-request ─────────────────
        withdrawn_urls: list[str] = []
        for inv_id, slug in pairs:
            wd_payload = {
                "requestId": "com.linkedin.sdui.requests.mynetwork.addaWithdrawInvitation",
                "serverRequest": {
                    "requestId": "com.linkedin.sdui.requests.mynetwork.addaWithdrawInvitation",
                    "requestedArguments": {
                        "$type": "proto.sdui.actions.requests.RequestedArguments",
                        "payload": {
                            "inviterActionType": "InviterActionType_WITHDRAW",
                            "invitationType": "GenericInvitationType_CONNECTION",
                            "invitationUrn": {"invitationId": inv_id},
                        },
                        "requestedStateKeys": [],
                        "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                        "states": [],
                        "screenId": "com.linkedin.sdui.flagshipnav.mynetwork.invitations.WithdrawConfirmationDialog",
                    },
                },
                "states": [],
                "requestedArguments": {
                    "$type": "proto.sdui.actions.requests.RequestedArguments",
                    "payload": {
                        "inviterActionType": "InviterActionType_WITHDRAW",
                        "invitationType": "GenericInvitationType_CONNECTION",
                        "invitationUrn": {"invitationId": inv_id},
                    },
                    "requestedStateKeys": [],
                    "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                    "states": [],
                    "screenId": "com.linkedin.sdui.flagshipnav.mynetwork.invitations.WithdrawConfirmationDialog",
                },
            }

            wd_result = await self.page.evaluate(
                """async ([url, hdrs, body]) => {
                    try {
                        const r = await fetch(url, {
                            method: "POST", headers: hdrs,
                            credentials: "include", body: JSON.stringify(body),
                        });
                        return {status: r.status, body: await r.text()};
                    } catch (e) { return {error: e.message}; }
                }""",
                [WITHDRAW_URL, SDUI_HEADERS, wd_payload],
            )

            status = wd_result.get("status")
            err = wd_result.get("error")
            profile_url = f"https://www.linkedin.com/in/{slug}/" if slug else ""

            if err:
                logger.warning("action.withdraw_api_error", inv_id=inv_id, error=err)
            elif status == 200:
                withdrawn_urls.append(profile_url)
                logger.info("action.invitation_withdrawn", inv_id=inv_id, slug=slug, order=order)
            else:
                body_snip = (wd_result.get("body") or "")[:200]
                logger.warning(
                    "action.withdraw_api_fail",
                    inv_id=inv_id, status=status, body=body_snip,
                )

            await self.delay.micro_delay(1.5, 2.5)

        return withdrawn_urls

    async def _withdraw_invitations_dom(
        self,
        count: int,
        order: str = "oldest",
    ) -> list:
        """
        DOM-scroll fallback for withdraw_invitations. Used only when the SDUI
        pagination endpoint is unavailable. IMPORTANT: never call with count > 20
        — loading all invitation cards into the DOM can OOM the 3 GB host.

        Assumes the caller has already navigated to the invitation manager page.
        """
        # For oldest order, scroll to load cards so the oldest (bottom) are visible.
        if order == "oldest":
            prev_count = 0
            for _ in range(60):
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.delay.micro_delay(1.0, 2.0)
                cur_count = await self.page.locator(selectors.INVITATION_WITHDRAW_ANCHOR).count()
                await self.page.evaluate("""
                () => {
                    const btn = Array.from(document.querySelectorAll('button'))
                        .find(b => b.textContent.includes('Show more') || b.textContent.includes('Load more'));
                    if (btn) btn.click();
                }
                """)
                if cur_count == prev_count:
                    logger.debug("action.withdraw_dom_scroll_stale", count=cur_count, scrolls=_)
                    break
                prev_count = cur_count

        total = await self.page.locator(selectors.INVITATION_WITHDRAW_ANCHOR).count()
        if total == 0:
            logger.warning("action.withdraw_dom_no_anchors")
            return []

        withdrawn_urls: list[str] = []

        for _ in range(count):
            if len(withdrawn_urls) >= count:
                break

            cur_total = await self.page.locator(selectors.INVITATION_WITHDRAW_ANCHOR).count()
            if cur_total == 0:
                break

            i = cur_total - 1 if order == "oldest" else 0

            anchor = self.page.locator(selectors.INVITATION_WITHDRAW_ANCHOR).nth(i)
            try:
                await anchor.scroll_into_view_if_needed(timeout=8000)
            except Exception:
                if order == "oldest" and cur_total >= 2:
                    i -= 1
                    anchor = self.page.locator(selectors.INVITATION_WITHDRAW_ANCHOR).nth(i)
                    try:
                        await anchor.scroll_into_view_if_needed(timeout=8000)
                    except Exception:
                        logger.warning("action.withdraw_dom_scroll_failed", index=i)
                        break
                else:
                    logger.warning("action.withdraw_dom_scroll_failed", index=i)
                    break
            await self.delay.micro_delay(0.3, 0.8)

            href = await self.page.evaluate("""
            (idx) => {
                const anchors = Array.from(document.querySelectorAll('a[aria-label^="Withdraw invitation"]'));
                if (idx >= anchors.length) return null;
                let el = anchors[idx];
                for (let j = 0; j < 10; j++) {
                    el = el.parentElement;
                    if (!el) break;
                    const link = el.querySelector('a[href*="/in/"]');
                    if (link) return link.href;
                }
                return null;
            }
            """, i)

            try:
                await anchor.click(timeout=5000)
            except Exception as e:
                logger.warning("action.withdraw_dom_anchor_click_failed", index=i, error=str(e))
                continue

            await self.delay.micro_delay(1.0, 1.5)

            confirmed = await self.page.evaluate("""
            () => {
                const btn = Array.from(document.querySelectorAll('button'))
                    .find(b => b.innerText.trim() === 'Withdraw' && b.offsetParent !== null);
                if (btn) { btn.click(); return true; }
                return false;
            }
            """)

            if confirmed:
                await self.delay.micro_delay(1.0, 2.0)
                if href:
                    withdrawn_urls.append(href)
                logger.info("action.withdraw_dom_withdrawn", url=href, order=order, index=i)
            else:
                logger.warning("action.withdraw_dom_confirm_not_found", index=i, href=href)

        return withdrawn_urls

    async def get_contact_info(self, slug: str) -> dict:
        """
        Extract email and phone for a 1st-degree LinkedIn connection.

        Strategy:
          1. Navigate to /in/{slug}/ (the profile page)
          2. Find and click the "Contact info" link in the profile header
          3. The SPA opens a modal; intercept the Voyager network response
             that fires in parallel to populate the modal
          4. Parse email + phone from the JSON payload

        Intercepts the network response rather than reading the DOM, which is
        more reliable across LinkedIn DOM changes. Falls back to DOM extraction
        (mailto: link) if the network intercept misses the response.

        Returns {"email": str|None, "phone": str|None}. Fail-open.
        """
        from releasi.linkedin.selectors import LOGIN_URL_PATTERNS

        slug = slug.lstrip("/").replace("in/", "").strip("/")
        result: dict = {"email": None, "phone": None}

        profile_url = f"https://www.linkedin.com/in/{slug}/"

        try:
            # ── Step 1: load profile ──────────────────────────────────────
            await asyncio.wait_for(
                self.page.goto(profile_url, wait_until="domcontentloaded", timeout=15000),
                timeout=20.0,
            )
        except Exception as e:
            logger.warning("contact_info.nav_failed", slug=slug, error=str(e))
            return result

        if any(p in self.page.url for p in LOGIN_URL_PATTERNS):
            logger.warning("contact_info.session_expired", slug=slug)
            return result

        await asyncio.sleep(1.0)

        # ── Step 2: find Contact Info link and click with native Playwright ─
        # LinkedIn uses React synthetic events; JS element.click() from evaluate()
        # does NOT reliably trigger SPA navigation. Use Playwright's native click
        # which simulates a real mouse event and fires React handlers correctly.
        contact_link = None
        # Try by ID first (most stable when present)
        try:
            id_loc = self.page.locator("#top-card-text-details-contact-info")
            if await id_loc.count() > 0 and await id_loc.first.is_visible():
                await id_loc.first.scroll_into_view_if_needed()
                await id_loc.first.click()
                contact_link = "clicked-by-id"
        except Exception:
            pass

        if not contact_link:
            # Fallback: find by visible text "Contact info"
            try:
                text_loc = self.page.get_by_text("Contact info", exact=True).first
                if await text_loc.is_visible():
                    await text_loc.scroll_into_view_if_needed()
                    await text_loc.click()
                    contact_link = "clicked-by-text"
            except Exception:
                pass

        if not contact_link:
            logger.debug("contact_info.no_contact_link", slug=slug)
            return result

        # ── Step 3: wait for overlay URL then wait for content to render ───
        # LinkedIn uses SPA routing: clicking "Contact info" changes the URL to
        # /overlay/contact-info/ and renders email/phone inline.
        # The URL changes BEFORE content is fully rendered, so we wait for the
        # mailto: link to appear in the DOM rather than using a fixed sleep.
        try:
            await self.page.wait_for_url(
                lambda u: "overlay/contact-info" in u,
                timeout=5000,
            )
        except PlaywrightTimeout:
            # Some profiles render contact info without a URL change
            pass

        # Wait for the overlay content (mailto link) to render.
        # Fixed 0.8s sleep is not enough — content can lag the URL change.
        try:
            await self.page.wait_for_selector(
                'a[href^="mailto:"]',
                timeout=4000,
                state="attached",
            )
        except PlaywrightTimeout:
            # Profile may not have a public email — still attempt phone extraction
            await asyncio.sleep(1.0)

        data = await self.page.evaluate("""
        () => {
            const result = { email: null, phone: null };

            // Email: the most reliable signal is a mailto: link
            const mailtoLink = document.querySelector('a[href^="mailto:"]');
            if (mailtoLink) {
                result.email = mailtoLink.href.replace('mailto:', '').trim();
            }

            // Phone: find a section/div that contains "Phone" as a header,
            // then grab the first text node that looks like a phone number.
            const allEls = document.querySelectorAll('section, div, li');
            for (const el of allEls) {
                const hdr = el.querySelector('h3, h4');
                if (hdr && (hdr.innerText || '').toLowerCase().includes('phone')) {
                    const spans = el.querySelectorAll('span, a');
                    for (const s of spans) {
                        const t = (s.innerText || '').trim();
                        if (/^[+0-9]/.test(t) && t.length >= 6) {
                            result.phone = t;
                            break;
                        }
                    }
                }
                if (result.phone) break;
            }

            return result;
        }
        """)

        if data:
            result["email"] = data.get("email") or None
            result["phone"] = data.get("phone") or None

        logger.info(
            "contact_info.extracted",
            slug=slug,
            method=contact_link,
            has_email=bool(result["email"]),
            has_phone=bool(result["phone"]),
        )
        return result

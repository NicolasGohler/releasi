"""
Centralized LinkedIn page selectors.

When LinkedIn changes their DOM, update THIS FILE ONLY.
Each selector has a primary and fallback list for resilience.

Selector strategy: prefer text-based selectors (:text-is, :has-text) over
class-based selectors, since LinkedIn frequently changes class names but
button labels stay stable.
"""

# ── Profile page: Connect button ──────────────────────────────────────────

# Primary Connect button — only matches when Connect is a top-level action button.
# Ordered: most specific → broadest. _find_element uses .first so the broadest
# selectors are safe: the profile card appears before sidebar in DOM order.
CONNECT_BUTTON_PRIMARY = [
    # New LinkedIn UI (2025+): Connect is rendered as an <a> link, not a <button>.
    # aria-label pattern "Invite X to connect" is unique to the profile's own Connect anchor.
    # Scoped to <main> to skip the duplicate hidden anchor that appears before <main>.
    'main a[aria-label^="Invite"][aria-label$="to connect"]',
    # Class-based (older LinkedIn DOM — kept for compatibility)
    'button.pv-s-profile-actions--connect',
    # Scoped to known profile actions containers
    '.pv-top-card .pvs-profile-actions button:has-text("Connect")',
    '.pv-top-card-v2-ctas button:has-text("Connect")',
    'main .pvs-profile-actions button:has-text("Connect")',
    # Broad button fallback — only safe if anchor selector above missed.
    # WARNING: sidebar "People you may know" also has button:has-text("Connect"),
    # so this is last resort only.
    'main button:has-text("Connect")',
]

# "More" button on profile — the dropdown trigger next to Follow/Message.
# This is the most critical selector — on Follow-primary profiles, Connect
# is hidden behind this dropdown.
CONNECT_BUTTON_MORE_DROPDOWN = [
    # aria-label is the most reliable (confirmed via diagnostic)
    'button[aria-label="More actions"]',
    # Class-based
    '.artdeco-dropdown__trigger:text-is("More")',
    'button:text-is("More")',
]

# Connect option inside the More dropdown menu
CONNECT_IN_DROPDOWN = [
    # Href-based: Connect anchor in dropdown now uses same preload URL as primary button.
    # Most reliable — immune to text changes and class obfuscation.
    '.artdeco-dropdown__content a[href*="custom-invite"]',
    '.artdeco-dropdown__content a[href*="preload"]',
    # aria-label case-insensitive (covers "Invite X to connect")
    '.artdeco-dropdown__content [aria-label*="connect" i]',
    '.artdeco-dropdown__content [data-control-name*="connect" i]',
    # Text-based: ":has-text" is case-sensitive in Playwright, so match "onnect"
    # to catch both "Connect" and "Invite X to connect" (lowercase c).
    '[role="menuitem"]:has-text("onnect")',
    '.artdeco-dropdown__content [role="button"]:has-text("onnect")',
    '.artdeco-dropdown__content li:has-text("onnect")',
    '.artdeco-dropdown__content span:text-is("Connect")',
]

# ── Connection request modal ──────────────────────────────────────────────

ADD_NOTE_BUTTON = [
    'button[aria-label="Add a note"]',
    'button:has-text("Add a note")',
]

NOTE_TEXTAREA = [
    'textarea[name="message"]',
    '#custom-message',
    'textarea.connect-button-send-invite__custom-message',
]

SEND_INVITATION_BUTTON = [
    'button[aria-label="Send invitation"]',
    'button[aria-label="Send now"]',
    '[role="dialog"] button:has-text("Send")',
    'button:has-text("Send")',
]

SEND_WITHOUT_NOTE = [
    'button[aria-label="Send without a note"]',
    'button:has-text("Send without a note")',
    'button.artdeco-button--muted:has-text("Send")',
]

# ── Limit detection ───────────────────────────────────────────────────────

WEEKLY_LIMIT_BANNERS = [
    'text="You\'ve reached the weekly invitation limit"',
    'text="you\'ve reached the weekly invitation limit"',
    ':has-text("weekly invitation limit")',
]

RATE_LIMIT_MODAL = [
    '[role="dialog"]:has-text("invitation limit")',
    '[role="alertdialog"]:has-text("invitation limit")',
]

# ── Authwall / session overlay ────────────────────────────────────────────
# Shown when LinkedIn overlays a sign-in prompt WITHOUT redirecting the URL.
# Detecting this early prevents misreading it as a missing-button error.
AUTHWALL_INDICATORS = [
    # Dialog-modal sign-in prompt overlaying profile content
    '[role="dialog"] button:has-text("Sign in")',
    '[role="dialog"] a:has-text("Sign in")',
    # LinkedIn join-wall container (data-* attributes survive class obfuscation)
    '[data-test-id="join-wall"]',
    '.join-wall',
    '[data-view-name="join-wall-headline"]',
]

# LinkedIn error page rendered inside the logged-in shell (nav stays, content fails).
# "Try again" button with no profile action buttons = page-level error, not session expiry.
PROFILE_ERROR_PAGE_INDICATORS = [
    'button:has-text("Try again")',
    'main :text("Something went wrong")',
    'main :text("Page not available")',
    'main :text("temporarily unavailable")',
]

# ── CAPTCHA ───────────────────────────────────────────────────────────────

CAPTCHA_INDICATORS = [
    '#captcha-challenge',
    'iframe[title*="challenge"]',
    'iframe[src*="captcha"]',
    '#cf-challenge-running',
]

# ── Connection status on profile ──────────────────────────────────────────

ALREADY_CONNECTED_INDICATORS = [
    # New LinkedIn layout (2025+): degree shown as plain text "· 1st" or "• 1st"
    # inside the profile header. Classes are obfuscated hashes — match by text only.
    'main :text("· 1st")',
    'main :text("• 1st")',
    # Legacy selectors (kept as fallback)
    '.distance-badge:has-text("1st")',
    'span.dist-value:has-text("1st")',
    'span:has-text("1st degree connection")',
]

PENDING_CONNECTION_INDICATORS = [
    'button:has-text("Pending")',
    'button[aria-label*="Pending" i]',
    # LinkedIn sometimes wraps the Pending state in an anchor too
    'a[aria-label*="pending" i]',
    # Or as a span inside the profile actions area
    '.pvs-profile-actions :has-text("Pending")',
]

# ── Profile action buttons (used for page-load wait) ─────────────────────

PROFILE_ACTION_BUTTONS = [
    'button:text-is("More")',
    'button:text-is("Connect")',
    # New LinkedIn UI: Connect is an <a> link — wait for it so _find_connect_button
    # doesn't run before the profile card has fully rendered its action elements.
    'a[aria-label$="to connect"]',
    'button:has-text("Follow")',
    'button:has-text("Message")',
]

# ── Message dialog ────────────────────────────────────────────────────────
#
# The follow-up flow navigates directly to /messaging/compose/?recipient=<URN>
# (see actions.send_message).
#
# LinkedIn's compose page (as of 2026-04) renders a Send button that is
# disabled until text is typed.  Pressing Enter creates a new line — it does
# NOT send the message.  We must click the Send button explicitly.

MESSAGE_INPUT = [
    'div.msg-form__contenteditable[role="textbox"]',
    'div[aria-label="Write a message…"]',
    '.msg-form__contenteditable',
]

MESSAGE_SEND_BUTTON = [
    'button.msg-form__send-button',
    'button.msg-form__send-btn',   # older class name — fallback
]

# ── Invitation manager ────────────────────────────────────────────────────

INVITATION_MANAGER_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"

# Withdraw anchors on the invitation manager page (2026+ DOM).
# LinkedIn renders each "Withdraw" action as an <a> with a specific aria-label.
# This is the primary selector — use it to find and click the withdraw trigger.
# After clicking, a confirmation overlay appears with a plain <button>Withdraw</button>.
INVITATION_WITHDRAW_ANCHOR = 'a[aria-label^="Withdraw invitation"]'

# Invitation cards on the sent invitations page.
# The cards are <div> containers found by traversing up from INVITATION_WITHDRAW_ANCHOR.
# These CSS selectors are kept for legacy fallback / URL extraction only.
INVITATION_CARDS = [
    "li:has(button:has-text('Withdraw'))",
    "li.invitation-card",
    ".mn-invitation-list li",
    "[data-view-name='invitation-card']",
    ".invitation-card",
    ".mn-invitation-card",
    "li[class*='invitation']",
    "[class*='invitation-card']",
    "[data-view-name*='invitation']",
]

# Profile link inside an invitation card
INVITATION_CARD_PROFILE_LINK = [
    ".invitation-card__link",
    "a[href*='/in/']",
]

# "Withdraw" button on each invitation card (legacy — superseded by INVITATION_WITHDRAW_ANCHOR)
INVITATION_WITHDRAW_BUTTON = [
    "button:has-text('Withdraw')",
    "button[aria-label*='Withdraw']",
    ".invitation-card__action-btn",
]

# Confirm withdrawal in modal/overlay.
# The overlay does NOT have role="dialog" — it is a plain overlay div.
# The confirm button has innerText "Withdraw" and is visible (offsetParent != null).
# Matched via JS in withdraw_invitations() — these CSS selectors are fallback only.
INVITATION_WITHDRAW_CONFIRM = [
    'button:has-text("Withdraw")',
    '[role="dialog"] button:has-text("Withdraw")',
    'button[aria-label="Withdraw invitation"]',
    '.artdeco-modal button:has-text("Withdraw")',
]

# Pending invitation count — extracted via JS from the "People (N)" filter pill.
# The JS expression is used directly in get_pending_invitation_count(); these
# selectors are kept as a CSS fallback only.
INVITATION_PENDING_COUNT = [
    ".mn-invitation-manager__header h2",
    "header h1",
]

# JS expression that reads the count from the "People (N)" filter pill.
# Returns the integer count, or null if not found.
INVITATION_PENDING_COUNT_JS = """
() => {
    // Search all elements for "People (N)" — the pill can be an <a>, <li>, <span>, or <button>
    const all = document.querySelectorAll('a, li, span, button, [role="tab"]');
    for (const el of all) {
        // Only leaf-ish elements whose own text (not descendants combined) contains the pattern
        const text = el.textContent || '';
        const m = text.match(/^\\s*People\\s*\\((\\d[\\d,]*)\\)\\s*$/i);
        if (m) return parseInt(m[1].replace(/,/g, ''), 10);
    }
    return null;
}
"""

# Load more invitations
INVITATION_LOAD_MORE = [
    'button:has-text("Show more results")',
    'button:has-text("Load more")',
]

# ── Connections page (recent connections scrape) ─────────────────────────
# LinkedIn uses obfuscated/hashed CSS class names that change on deploys.
# Extraction logic in actions.py uses content-based JS (no class selectors):
#   - Anchor on <p> elements whose text starts with "Connected on "
#   - Walk up to find nearest ancestor containing an a[href*="/in/"] link
#
# URL: sortType param is stripped by LinkedIn's router but the page IS sorted
# newest-first when navigating from this URL.
CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/?sortType=RECENTLY_ADDED"

# Timestamp format verified 2026-04-07: "Connected on April 7, 2026"
# Full date (not relative). Parsed in actions.py _parse_connection_age_hours().
# Pagination: infinite scroll (no "Show more" button). Scroll to bottom to load more.

# ── Nav bar avatar (for profile picture scraping) ────────────────────────

NAV_AVATAR = [
    'img.global-nav__me-photo',
    '.global-nav__me img[src*="profile"]',
    'img[alt*="photo"][class*="global-nav"]',
]

# ── Feed & noise ─────────────────────────────────────────────────────────

FEED_LIKE_BUTTON = [
    "button[aria-label*='Like']",
    "button.react-button__trigger",
    "button span.reactions-react-button",
]

FEED_POSTS = [
    "div.feed-shared-update-v2",
    "div[data-urn*='activity']",
    "div[data-id*='urn:li:activity']",
]

MY_NETWORK_URL = "https://www.linkedin.com/mynetwork/"

SUGGESTED_PROFILES = [
    "a[href*='/in/']",
]

# ── Profile filtering ────────────────────────────────────────────────────

PROFILE_PHOTO = [
    'img.pv-top-card-profile-picture__image--show',
    '.pv-top-card__photo img[src*="profile-displayphoto"]',
]

PROFILE_NO_PHOTO = [
    '.pv-top-card-profile-picture__image--ghost',
    'svg[data-test-icon="person-medium"]',
]

PROFILE_CONNECTION_COUNT = [
    # NOTE: LinkedIn now uses obfuscated CSS class names (e.g. "_16deed48")
    # that change with each deploy, making CSS selectors unreliable.
    # profile_filter._get_connection_count() uses JS content-matching as the
    # primary strategy (finds <p>/"connections" label, reads count from parent).
    # These CSS selectors are kept only as a fallback for older cached layouts.
    '.pv-top-card--list-bullet li:has-text("connections") span.t-bold',
    'a[href*="/connections/"] span.t-bold',
    'span:has-text("connections")',
]

# ── People search results ─────────────────────────────────────────────────
# LinkedIn search results use hashed/obfuscated CSS class names that change
# on deploys. All URL extraction is done via JS. These selectors are used
# only for page-load detection (waiting for first result to appear).
SEARCH_RESULTS_LOADED = [
    # Any profile link inside main content — reliable cross-deploy
    'main a[href*="/in/"]',
    # Fallback: search results container scoped link
    '.search-results-container a[href*="/in/"]',
]

# Indicates the search returned no results (end of pagination or empty query)
SEARCH_NO_RESULTS = [
    'main :text("No results found")',
    'main :text("0 results")',
    ':has-text("No results found")',
]

# ── Session validation ────────────────────────────────────────────────────

FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL_PATTERNS = [
    "linkedin.com/login",
    "linkedin.com/uas/login",
    "linkedin.com/checkpoint",
]

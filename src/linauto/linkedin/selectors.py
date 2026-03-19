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
    # Dropdown items use role="menuitem" or are inside artdeco-dropdown__content
    '[role="menuitem"]:has-text("Connect")',
    # LinkedIn sometimes uses role="button" instead of menuitem inside dropdowns
    '.artdeco-dropdown__content [role="button"]:has-text("Connect")',
    '.artdeco-dropdown__content li:has-text("Connect")',
    '.artdeco-dropdown__content span:text-is("Connect")',
    '.artdeco-dropdown__content [aria-label*="connect" i]',
    '.artdeco-dropdown__content [data-control-name*="connect" i]',
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

# ── CAPTCHA ───────────────────────────────────────────────────────────────

CAPTCHA_INDICATORS = [
    '#captcha-challenge',
    'iframe[title*="challenge"]',
    'iframe[src*="captcha"]',
    '#cf-challenge-running',
]

# ── Connection status on profile ──────────────────────────────────────────

ALREADY_CONNECTED_INDICATORS = [
    '.distance-badge:has-text("1st")',
    'span.dist-value:has-text("1st")',
    'span:has-text("1st degree connection")',
]

PENDING_CONNECTION_INDICATORS = [
    'button:has-text("Pending")',
    'button[aria-label*="Pending"]',
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

MESSAGE_BUTTON = [
    'button:has-text("Message")',
    'a:has-text("Message")',
]

MESSAGE_INPUT = [
    'div.msg-form__contenteditable[role="textbox"]',
    'div[aria-label="Write a message…"]',
    '.msg-form__contenteditable',
]

MESSAGE_SEND_BUTTON = [
    'button.msg-form__send-button',
    'button[type="submit"]:has-text("Send")',
]

# ── Invitation manager ────────────────────────────────────────────────────

INVITATION_MANAGER_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"

# Invitation cards on the sent invitations page
INVITATION_CARDS = [
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

# "Withdraw" button on each invitation card
INVITATION_WITHDRAW_BUTTON = [
    "button:has-text('Withdraw')",
    "button[aria-label*='Withdraw']",
    ".invitation-card__action-btn",
]

# Confirm withdrawal in modal dialog
INVITATION_WITHDRAW_CONFIRM = [
    '[role="dialog"] button:has-text("Withdraw")',
    'button[aria-label="Withdraw invitation"]',
    '.artdeco-modal button:has-text("Withdraw")',
]

# Pending invitation count (header area)
INVITATION_PENDING_COUNT = [
    ".mn-invitation-manager__header h2",
    "header h1",
]

# Load more invitations
INVITATION_LOAD_MORE = [
    'button:has-text("Show more results")',
    'button:has-text("Load more")',
]

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
    '.pv-top-card--list-bullet li:has-text("connections") span.t-bold',
    'a[href*="/connections/"] span.t-bold',
    # Broader: any element near top of page with connection count text
    'span:has-text("connections")',
]

# ── Session validation ────────────────────────────────────────────────────

FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL_PATTERNS = [
    "linkedin.com/login",
    "linkedin.com/uas/login",
    "linkedin.com/checkpoint",
]

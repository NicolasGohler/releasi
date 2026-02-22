"""
Centralized LinkedIn page selectors.

When LinkedIn changes their DOM, update THIS FILE ONLY.
Each selector has a primary and fallback list for resilience.
"""

# ── Profile page: Connect button ──────────────────────────────────────────

CONNECT_BUTTON_PRIMARY = [
    'button.pv-s-profile-actions--connect',
    'button:has-text("Connect")',
    '[aria-label*="Invite"][aria-label*="connect"]',
]

CONNECT_BUTTON_MORE_DROPDOWN = [
    'button[aria-label="More actions"]',
    'button.artdeco-dropdown__trigger:has-text("More")',
]

CONNECT_IN_DROPDOWN = [
    '[role="menuitem"]:has-text("Connect")',
    'li-icon[type="connect"] ~ span:has-text("Connect")',
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
    'button:has-text("Send")',
]

SEND_WITHOUT_NOTE = [
    'button:has-text("Send without a note")',
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
]

# ── Session validation ────────────────────────────────────────────────────

FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL_PATTERNS = [
    "linkedin.com/login",
    "linkedin.com/uas/login",
    "linkedin.com/checkpoint",
]

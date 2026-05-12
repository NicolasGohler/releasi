"""Classification helpers for distinguishing session-expiry, network, and
generic errors in LinkedIn action failures.

These helpers are dependency-free (no playwright/sqlalchemy imports) so they
can be unit-tested in isolation. Both the connection-request and follow-up
paths use them to apply consistent consecutive-error heuristics.

Priority chain (caller should check in this order):
  1. _is_session_expired_signal()  → mark account cookie_expired immediately
  2. _is_network_error()            → don't penalise the session (proxy issue)
  3. otherwise                      → generic fatal, count toward 3-strike rule

Critical: ERR_TOO_MANY_REDIRECTS is emitted as `net::ERR_TOO_MANY_REDIRECTS`
but IS NOT a network error — it's the LinkedIn login redirect loop, an
unambiguous cookie-expiry signal. The naive `"net::" in msg` check would
misclassify it, which is exactly the bug that caused 5+ followups to fail
silently in a single day with the cookie already invalid.
"""
from __future__ import annotations


# Substrings indicating the LinkedIn session is expired / invalid. Take
# priority over network signals.
_SESSION_EXPIRED_SIGNALS = (
    "too_many_redirects",
    "err_too_many_redirects",
    "/login",
    "checkpoint",
    "authwall",
    "session_expired",
)

# Substrings indicating a network/proxy failure rather than a LinkedIn
# session problem. Should NOT count toward the cookie-expiry heuristic.
_NETWORK_ERROR_SIGNALS = (
    "timeout",
    "timed out",
    "err_tunnel",
    "err_proxy",
    "err_connection",
    "err_name_not_resolved",
    "proxy",
    "econnreset",
    "econnrefused",
    "enotfound",
)


def is_session_expired_signal(msg: str) -> bool:
    """Return True if the error message looks like cookie expiry / login redirect."""
    low = msg.lower()
    return any(sig in low for sig in _SESSION_EXPIRED_SIGNALS)


def is_network_error(msg: str) -> bool:
    """Return True if the error looks like proxy/network failure, NOT session expiry.

    Session-expiry signals take priority — `net::ERR_TOO_MANY_REDIRECTS`
    counts as session, not network, even though it starts with `net::`.
    """
    low = msg.lower()
    if is_session_expired_signal(low):
        return False
    if "net::" in low:
        return True
    return any(sig in low for sig in _NETWORK_ERROR_SIGNALS)

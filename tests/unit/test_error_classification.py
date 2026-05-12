"""Tests for executor error-classification helpers.

These guard the priority chain that lets the followup dispatcher detect
expired cookies from ERR_TOO_MANY_REDIRECTS instead of misclassifying it as
a generic network error.
"""
from __future__ import annotations

import pytest

from linauto.safety.error_signals import (
    is_network_error as _is_network_error,
    is_session_expired_signal as _is_session_expired_signal,
)


# ── Session-expired signal ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "msg",
    [
        "Navigation failed: Page.goto: net::ERR_TOO_MANY_REDIRECTS",
        "ERR_TOO_MANY_REDIRECTS",
        "too_many_redirects",
        "Redirected to /login",
        "https://www.linkedin.com/checkpoint/challenge",
        "Encountered LinkedIn authwall",
        "session_expired",
    ],
)
def test_session_signals_detected(msg):
    assert _is_session_expired_signal(msg) is True


@pytest.mark.parametrize(
    "msg",
    [
        "Page.goto: net::ERR_PROXY_CONNECTION_FAILED",
        "Timeout 30000ms exceeded",
        "Connection reset",
        "Some generic LinkedIn error",
        "",
    ],
)
def test_non_session_messages_not_flagged(msg):
    assert _is_session_expired_signal(msg) is False


# ── Network error (with priority over net:: for session signals) ────────────

def test_redirect_loop_is_session_not_network():
    """The bug we're fixing: ERR_TOO_MANY_REDIRECTS uses `net::` prefix but is
    actually a session-expiry signal. Must NOT be classified as network."""
    msg = "Page.goto: net::ERR_TOO_MANY_REDIRECTS at https://www.linkedin.com/..."
    assert _is_session_expired_signal(msg) is True
    assert _is_network_error(msg) is False


def test_generic_net_error_is_network():
    """Generic `net::` errors (proxy failures, name resolution) still count as
    network — only the session-signal subset is excluded."""
    msg = "Page.goto: net::ERR_PROXY_CONNECTION_FAILED"
    assert _is_network_error(msg) is True
    assert _is_session_expired_signal(msg) is False


@pytest.mark.parametrize(
    "msg",
    [
        "Timeout 30000ms exceeded",
        "ECONNRESET",
        "ECONNREFUSED",
        "ENOTFOUND linkedin.com",
        "err_tunnel_connection_failed",
        "Proxy authentication required",
    ],
)
def test_network_signals_detected(msg):
    assert _is_network_error(msg) is True
    assert _is_session_expired_signal(msg) is False


def test_unknown_error_neither():
    """Errors with no recognised signal should be neither — caller falls
    through to the generic-fatal branch."""
    msg = "Some unexpected LinkedIn DOM change"
    assert _is_network_error(msg) is False
    assert _is_session_expired_signal(msg) is False


def test_case_insensitive():
    assert _is_session_expired_signal("ERR_TOO_MANY_REDIRECTS") is True
    assert _is_session_expired_signal("err_too_many_redirects") is True
    assert _is_network_error("TIMEOUT") is True
    assert _is_network_error("timeout") is True

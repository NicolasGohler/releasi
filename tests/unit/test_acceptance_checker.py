"""Tests for acceptance checker logic — URL normalization and lead diffing."""
import pytest
from linauto.scheduler.runner import _normalize_li_url
from linauto.linkedin.actions import InvitationSnapshot


# ── _normalize_li_url tests ────────────────────────────────────────────────

def test_normalize_full_url_with_trailing_slash():
    assert _normalize_li_url("https://www.linkedin.com/in/john-doe-12345/") == "/in/john-doe-12345"


def test_normalize_full_url_no_trailing_slash():
    assert _normalize_li_url("https://www.linkedin.com/in/john-doe-12345") == "/in/john-doe-12345"


def test_normalize_without_www():
    assert _normalize_li_url("https://linkedin.com/in/john-doe/") == "/in/john-doe"


def test_normalize_with_query_string():
    assert _normalize_li_url("https://www.linkedin.com/in/john-doe?miniProfileUrn=abc123") == "/in/john-doe"


def test_normalize_with_anchor():
    assert _normalize_li_url("https://www.linkedin.com/in/john-doe#anchor") == "/in/john-doe"


def test_normalize_relative_path():
    assert _normalize_li_url("/in/john-doe/") == "/in/john-doe"


def test_normalize_company_url_returns_empty():
    assert _normalize_li_url("https://www.linkedin.com/company/acme") == ""


def test_normalize_empty_string():
    assert _normalize_li_url("") == ""


# ── Diff logic tests ───────────────────────────────────────────────────────

def test_diff_finds_disappeared():
    """Leads not in pending set should be detected as disappeared."""
    pending_urls = [
        "https://www.linkedin.com/in/alice/",
        "https://www.linkedin.com/in/bob/",
    ]
    pending_normalized = {_normalize_li_url(u) for u in pending_urls if _normalize_li_url(u)}

    # charlie disappeared (accepted or declined)
    db_leads_urls = [
        "https://www.linkedin.com/in/alice",
        "https://www.linkedin.com/in/bob",
        "https://www.linkedin.com/in/charlie",
    ]

    disappeared = [u for u in db_leads_urls if _normalize_li_url(u) not in pending_normalized]
    assert disappeared == ["https://www.linkedin.com/in/charlie"]


def test_diff_all_still_pending():
    """No disappeared leads when all are still in pending set."""
    pending_urls = [
        "https://www.linkedin.com/in/alice/",
        "https://www.linkedin.com/in/bob/",
    ]
    pending_normalized = {_normalize_li_url(u) for u in pending_urls if _normalize_li_url(u)}

    db_leads_urls = [
        "https://www.linkedin.com/in/alice",
        "https://www.linkedin.com/in/bob",
    ]

    disappeared = [u for u in db_leads_urls if _normalize_li_url(u) not in pending_normalized]
    assert disappeared == []


def test_diff_all_disappeared():
    """All leads disappeared (everyone accepted or declined)."""
    pending_urls = []
    pending_normalized = {_normalize_li_url(u) for u in pending_urls if _normalize_li_url(u)}

    db_leads_urls = [
        "https://www.linkedin.com/in/alice",
        "https://www.linkedin.com/in/bob",
    ]

    disappeared = [u for u in db_leads_urls if _normalize_li_url(u) not in pending_normalized]
    assert len(disappeared) == 2


# ── InvitationSnapshot tests ───────────────────────────────────────────────

def test_invitation_snapshot_success():
    snap = InvitationSnapshot(success=True, session_valid=True, urls=["https://www.linkedin.com/in/alice/"])
    assert snap.success
    assert snap.session_valid
    assert len(snap.urls) == 1


def test_invitation_snapshot_session_expired():
    snap = InvitationSnapshot(success=True, session_valid=False, urls=[])
    assert snap.success
    assert not snap.session_valid


def test_invitation_snapshot_network_error():
    snap = InvitationSnapshot(success=False, session_valid=True, urls=[])
    assert not snap.success

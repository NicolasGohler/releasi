"""Tests for lead state machine transitions."""
import pytest
from linauto.db.models import LeadStatus
from linauto.campaign.state_machine import can_transition, validate_transition, InvalidTransition


def test_valid_pending_to_scheduled():
    assert can_transition(LeadStatus.PENDING, LeadStatus.SCHEDULED)


def test_valid_scheduled_to_requested():
    assert can_transition(LeadStatus.SCHEDULED, LeadStatus.CONNECTION_REQUESTED)


def test_valid_requested_to_connected():
    assert can_transition(LeadStatus.CONNECTION_REQUESTED, LeadStatus.CONNECTED)


def test_valid_connected_to_followup_scheduled():
    assert can_transition(LeadStatus.CONNECTED, LeadStatus.FOLLOWUP_SCHEDULED)


def test_valid_connected_to_completed():
    assert can_transition(LeadStatus.CONNECTED, LeadStatus.COMPLETED)


def test_valid_followup_sent_to_completed():
    assert can_transition(LeadStatus.FOLLOWUP_SENT, LeadStatus.COMPLETED)


def test_valid_error_to_pending_retry():
    assert can_transition(LeadStatus.ERROR, LeadStatus.PENDING)


def test_valid_limit_paused_to_scheduled():
    assert can_transition(LeadStatus.LIMIT_PAUSED, LeadStatus.SCHEDULED)


def test_invalid_completed_to_anything():
    assert not can_transition(LeadStatus.COMPLETED, LeadStatus.PENDING)
    assert not can_transition(LeadStatus.COMPLETED, LeadStatus.SCHEDULED)


def test_invalid_skipped_to_anything():
    assert not can_transition(LeadStatus.SKIPPED, LeadStatus.PENDING)


def test_invalid_pending_to_connected():
    assert not can_transition(LeadStatus.PENDING, LeadStatus.CONNECTED)


def test_validate_raises_on_invalid():
    with pytest.raises(InvalidTransition):
        validate_transition(LeadStatus.COMPLETED, LeadStatus.PENDING)


def test_validate_passes_on_valid():
    validate_transition(LeadStatus.PENDING, LeadStatus.SCHEDULED)

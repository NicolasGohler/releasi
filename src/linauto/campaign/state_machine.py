"""Lead lifecycle state machine with validated transitions."""
from __future__ import annotations

from linauto.db.models import LeadStatus


class InvalidTransition(Exception):
    pass


# Valid state transitions
VALID_TRANSITIONS = {
    LeadStatus.PENDING: [
        LeadStatus.SCHEDULED,
        LeadStatus.SKIPPED,
        LeadStatus.ERROR,
    ],
    LeadStatus.SCHEDULED: [
        LeadStatus.CONNECTION_REQUESTED,
        LeadStatus.SKIPPED,
        LeadStatus.ERROR,
        LeadStatus.LIMIT_PAUSED,
    ],
    LeadStatus.CONNECTION_REQUESTED: [
        LeadStatus.CONNECTED,
        LeadStatus.ERROR,
    ],
    LeadStatus.CONNECTED: [
        LeadStatus.FOLLOWUP_SCHEDULED,
        LeadStatus.COMPLETED,  # If no followup template
    ],
    LeadStatus.FOLLOWUP_SCHEDULED: [
        LeadStatus.FOLLOWUP_SENT,
        LeadStatus.ERROR,
    ],
    LeadStatus.FOLLOWUP_SENT: [
        LeadStatus.COMPLETED,
    ],
    LeadStatus.SKIPPED: [],  # Terminal state
    LeadStatus.COMPLETED: [],  # Terminal state
    LeadStatus.ERROR: [
        LeadStatus.PENDING,  # Retry resets to pending
    ],
    LeadStatus.LIMIT_PAUSED: [
        LeadStatus.SCHEDULED,  # Resume after cooldown
    ],
}


def can_transition(current: LeadStatus, target: LeadStatus) -> bool:
    """Check if a transition from current to target is valid."""
    valid = VALID_TRANSITIONS.get(current, [])
    return target in valid


def validate_transition(current: LeadStatus, target: LeadStatus) -> None:
    """Validate a transition. Raises InvalidTransition if not allowed."""
    if not can_transition(current, target):
        raise InvalidTransition(
            f"Cannot transition from {current.value} to {target.value}. "
            f"Valid targets: {[s.value for s in VALID_TRANSITIONS.get(current, [])]}"
        )

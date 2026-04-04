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
        LeadStatus.INVALID,
        LeadStatus.ERROR,
        LeadStatus.REMOVED,
        LeadStatus.CONNECTED,  # Already a 1st-degree connection — skip the request step
    ],
    LeadStatus.SCHEDULED: [
        LeadStatus.CONNECTION_REQUESTED,
        LeadStatus.SKIPPED,
        LeadStatus.INVALID,
        LeadStatus.ERROR,
        LeadStatus.LIMIT_PAUSED,
        LeadStatus.REMOVED,
        LeadStatus.CONNECTED,  # Already a 1st-degree connection — skip the request step
    ],
    LeadStatus.CONNECTION_REQUESTED: [
        LeadStatus.CONNECTED,
        LeadStatus.WITHDRAWN,
        LeadStatus.ERROR,
        LeadStatus.REMOVED,
    ],
    LeadStatus.CONNECTED: [
        LeadStatus.FOLLOWUP_SCHEDULED,
        LeadStatus.FOLLOWUP_SENT,  # Immediate follow-up on acceptance
        LeadStatus.COMPLETED,  # If no followup template
        LeadStatus.ERROR,  # Follow-up attempt failed
        LeadStatus.REMOVED,
    ],
    LeadStatus.FOLLOWUP_SCHEDULED: [
        LeadStatus.FOLLOWUP_SENT,
        LeadStatus.ERROR,
        LeadStatus.REMOVED,
    ],
    LeadStatus.FOLLOWUP_SENT: [
        LeadStatus.COMPLETED,
        LeadStatus.REMOVED,
    ],
    LeadStatus.SKIPPED: [
        LeadStatus.PENDING,  # Re-queue: undo a skip
        LeadStatus.REMOVED,
    ],
    LeadStatus.INVALID: [
        LeadStatus.REMOVED,  # Only valid exit: manual removal
    ],
    LeadStatus.COMPLETED: [
        LeadStatus.REMOVED,
    ],
    LeadStatus.ERROR: [
        LeadStatus.PENDING,  # Retry resets to pending
        LeadStatus.REMOVED,
    ],
    LeadStatus.WITHDRAWN: [
        LeadStatus.PENDING,  # Retry: re-send later
        LeadStatus.REMOVED,
    ],
    LeadStatus.LIMIT_PAUSED: [
        LeadStatus.SCHEDULED,  # Resume after cooldown
        LeadStatus.REMOVED,
    ],
    LeadStatus.REMOVED: [
        LeadStatus.PENDING,  # Restore
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

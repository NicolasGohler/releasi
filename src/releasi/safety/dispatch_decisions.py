"""Pure decision functions for the connection-request and followup
dispatchers.

The dispatchers used to inline ~120 lines of nested if/elif logic to react
to executor results: increment counters, decide whether to mark
cookie_expired, decide whether to burn the lead to ERROR, etc. That
made the control flow effectively untestable without mocking the entire
browser pool + DB.

This module isolates the decision logic. Each classifier takes the result
dict + current consecutive-error counters and returns an intent object
describing what the dispatcher should do. The dispatcher only handles
side-effects (DB writes, Slack pings, breaking the loop).

Testing strategy: pure functions = no mocks needed. Feed in a sequence of
result dicts, assert the intent transitions match the expected dispatcher
behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class LeadAction(str, Enum):
    """What the dispatcher should do to the lead's DB row."""
    NONE = "none"               # Leave the lead untouched (e.g. retry later)
    MARK_SENT = "mark_sent"     # Followup success or "prior conversation" skip
    MARK_ERROR = "mark_error"   # Burn to ERROR (generic failure)


class AccountAction(str, Enum):
    """What the dispatcher should do to the account's status."""
    NONE = "none"
    MARK_COOKIE_EXPIRED = "mark_cookie_expired"


@dataclass
class DispatchIntent:
    """Intent the dispatcher should execute. Pure data — no side effects."""
    lead_action: LeadAction = LeadAction.NONE
    account_action: AccountAction = AccountAction.NONE
    consecutive_session: int = 0
    consecutive_network: int = 0
    stop_account: bool = False
    slack_message: Optional[str] = None
    log_event: Optional[str] = None
    log_reason: Optional[str] = None  # Goes into action_log details["reason"]
    # For the connection-request path: revert SCHEDULED leads to PENDING
    # so the planner re-schedules after cookie renewal.
    reset_scheduled_to_pending: bool = False


# Default 3-strike thresholds. Override in tests if needed.
SESSION_STRIKE_LIMIT = 3
NETWORK_STRIKE_LIMIT = 3


def classify_followup_result(
    result: dict,
    consecutive_session: int,
    consecutive_network: int,
    account_name: str,
    campaign_name: str,
    session_threshold: int = SESSION_STRIKE_LIMIT,
    network_threshold: int = NETWORK_STRIKE_LIMIT,
) -> DispatchIntent:
    """Pure decision function for a followup-sequence result.

    Priority chain:
      1. success / skipped → reset counters, mark sent
      2. explicit session_expired flag → cookie_expired NOW, leave lead alone
      3. network_error flag → increment network counter, leave lead alone,
         bail account at 3-strike
      4. fatal (e.g. CAPTCHA) → stop account, no cookie flag, Slack ping
      5. generic failure → mark ERROR, increment session counter, cookie
         _expired at 3-strike
    """
    if result.get("success"):
        return DispatchIntent(
            lead_action=LeadAction.MARK_SENT,
            consecutive_session=0,
            consecutive_network=0,
        )

    if result.get("skipped"):
        # Prior conversation detected — treat as sent (won't retry).
        return DispatchIntent(
            lead_action=LeadAction.MARK_SENT,
            consecutive_session=0,
            consecutive_network=0,
        )

    if result.get("session_expired"):
        return DispatchIntent(
            lead_action=LeadAction.NONE,  # message never went through; preserve FOLLOWUP_SCHEDULED
            account_action=AccountAction.MARK_COOKIE_EXPIRED,
            consecutive_session=0,
            consecutive_network=0,
            stop_account=True,
            slack_message=(
                f":warning: *Cookie expired* — account *{account_name}* "
                f"(detected by followup dispatcher; lead preserved in FOLLOWUP_SCHEDULED for retry)."
            ),
            log_event="followup.session_expired_detected",
            log_reason="followup_session_expired",
        )

    if result.get("network_error"):
        new_consec = consecutive_network + 1
        if new_consec >= network_threshold:
            return DispatchIntent(
                lead_action=LeadAction.NONE,
                consecutive_session=0,
                consecutive_network=new_consec,
                stop_account=True,
                log_event="followup.proxy_connectivity_issues",
                log_reason="followup_consecutive_network_errors",
            )
        return DispatchIntent(
            lead_action=LeadAction.NONE,  # message never went through
            consecutive_session=0,
            consecutive_network=new_consec,
            log_event="followup.network_error",
        )

    # Fatal (CAPTCHA or other non-session unrecoverable) — bail with Slack
    # but no cookie_expired flag.
    if result.get("fatal"):
        return DispatchIntent(
            lead_action=LeadAction.MARK_ERROR,
            consecutive_session=consecutive_session,
            consecutive_network=consecutive_network,
            stop_account=True,
            slack_message=(
                f":no_entry: *CAPTCHA or fatal followup error* — account *{account_name}* "
                f"on campaign *{campaign_name}* — account paused, manual review needed."
            ),
            log_event="followup.fatal_error",
        )

    # Generic failure: mark lead ERROR (messages aren't idempotent, can't
    # safely retry without risking duplicates) and track for 3-strike rule.
    new_consec = consecutive_session + 1
    if new_consec >= session_threshold:
        return DispatchIntent(
            lead_action=LeadAction.MARK_ERROR,
            account_action=AccountAction.MARK_COOKIE_EXPIRED,
            consecutive_session=new_consec,
            consecutive_network=0,
            stop_account=True,
            slack_message=(
                f":warning: *Cookie expired* — account *{account_name}* "
                f"({new_consec} consecutive followup failures on *{campaign_name}*)."
            ),
            log_event="followup.consecutive_errors_detected",
            log_reason="followup_consecutive_errors",
        )
    return DispatchIntent(
        lead_action=LeadAction.MARK_ERROR,
        consecutive_session=new_consec,
        consecutive_network=0,
        log_event="followup.failed_no_retry",
    )


def classify_connection_result(
    result: dict,
    consecutive_session: int,
    consecutive_network: int,
    account_name: str,
    campaign_name: str,
    session_threshold: int = SESSION_STRIKE_LIMIT,
    network_threshold: int = NETWORK_STRIKE_LIMIT,
) -> DispatchIntent:
    """Pure decision function for a connection-request result.

    Differences vs. followup classifier:
      - On `success`: lead is already in CONNECTION_REQUESTED via executor.
        Dispatcher just resets counters (LeadAction.NONE — no DB write).
      - On `skipped`: lead status set by executor (e.g. already_connected).
        Dispatcher resets counters and tracks a backfill, doesn't write.
      - On session_expired: revert SCHEDULED leads to PENDING (planner
        re-plans after cookie renewal).
    """
    # Success / skipped paths don't need lead writes (executor handled it);
    # only need to reset counters.
    if result.get("success") or result.get("skipped"):
        return DispatchIntent(
            lead_action=LeadAction.NONE,
            consecutive_session=0,
            consecutive_network=0,
        )

    if result.get("session_expired"):
        return DispatchIntent(
            lead_action=LeadAction.NONE,  # executor leaves lead in prior status
            account_action=AccountAction.MARK_COOKIE_EXPIRED,
            consecutive_session=0,
            consecutive_network=0,
            stop_account=True,
            reset_scheduled_to_pending=True,
            slack_message=(
                f":warning: *Cookie expired* — account *{account_name}* "
                f"(detected by connection dispatcher; SCHEDULED leads reverted to PENDING for retry)."
            ),
            log_event="dispatch.session_expired_detected",
            log_reason="connection_session_expired",
        )

    if result.get("network_error"):
        new_consec = consecutive_network + 1
        if new_consec >= network_threshold:
            return DispatchIntent(
                lead_action=LeadAction.NONE,
                consecutive_session=0,
                consecutive_network=new_consec,
                stop_account=True,
                log_event="dispatch.proxy_connectivity_issues",
                log_reason="consecutive_network_errors",
            )
        return DispatchIntent(
            lead_action=LeadAction.NONE,
            consecutive_session=0,
            consecutive_network=new_consec,
        )

    if result.get("fatal"):
        return DispatchIntent(
            lead_action=LeadAction.NONE,  # executor already marked lead ERROR
            consecutive_session=consecutive_session,
            consecutive_network=consecutive_network,
            stop_account=True,
            slack_message=(
                f":no_entry: *CAPTCHA or fatal action error* — account *{account_name}* "
                f"on campaign *{campaign_name}* — account paused, manual review needed."
            ),
            log_event="dispatch.fatal_error",
        )

    if result.get("ui_error"):
        # Send button not found after clicking Connect — LinkedIn showed a
        # direct-send flow or changed the modal UI. The session was valid
        # (navigator loaded the profile), so this must not count toward the
        # session threshold that would mark the account cookie_expired.
        return DispatchIntent(
            lead_action=LeadAction.NONE,
            consecutive_session=consecutive_session,
            consecutive_network=consecutive_network,
            log_event="dispatch.ui_error_not_counted",
        )

    # Generic failure: executor already marked the lead ERROR. Just track
    # for 3-strike.
    new_consec = consecutive_session + 1
    if new_consec >= session_threshold:
        return DispatchIntent(
            lead_action=LeadAction.NONE,
            account_action=AccountAction.MARK_COOKIE_EXPIRED,
            consecutive_session=new_consec,
            consecutive_network=0,
            stop_account=True,
            reset_scheduled_to_pending=True,
            slack_message=(
                f":warning: *Cookie expired* — account *{account_name}* "
                f"({new_consec} consecutive session errors on campaign *{campaign_name}*)."
            ),
            log_event="dispatch.consecutive_errors_detected",
            log_reason="consecutive_navigation_errors",
        )
    return DispatchIntent(
        lead_action=LeadAction.NONE,
        consecutive_session=new_consec,
        consecutive_network=0,
    )

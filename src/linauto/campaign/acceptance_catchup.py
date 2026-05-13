"""One-shot acceptance catchup for a single campaign.

Used when a campaign was paused for longer than the daily acceptance checker's
72h scan window. Triggered explicitly from the dashboard (after user
confirmation) — never runs automatically on resume.

Design notes:
  - Strictly scoped to a SINGLE campaign. The connections-page scroll is
    account-wide, but we only match slugs against this campaign's
    CONNECTION_REQUESTED leads. Other campaigns on the same account get no
    side-effects from this run.
  - The cutoff is computed as the maximum of two ages:
      (a) hours since campaign.paused_at (if set)
      (b) hours since the oldest CONNECTION_REQUESTED lead's updated_at
    plus a 24h buffer, capped at 720h (30 days).
    Rationale: a 30-day pause might still have invites that were sent 35 days
    ago and accepted yesterday — we need to cover the oldest pending lead.
  - Hard precondition checks (cookie_expired, paused_until, archived) before
    acquiring the browser pool slot.
  - Idempotent: only transitions CONNECTION_REQUESTED → CONNECTED. Already
    CONNECTED leads are skipped (validate_transition would reject), and we
    guard increment_daily_stat with a `connection_accepted_at IS NULL` check.
  - Followup scheduling: matched leads with followup_enabled get
    FOLLOWUP_SCHEDULED transition just like the daily checker.
  - On success, clears campaign.paused_at and stamps account.last_catchup_at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

import structlog

from linauto.campaign.state_machine import validate_transition
from linauto.db.models import (
    AccountStatus,
    ActionLogStatus,
    ActionType,
    LeadStatus,
)
from linauto.db.repository import Repository

logger = structlog.get_logger()


# Safety caps
MIN_CUTOFF_HOURS = 72.0
MAX_CUTOFF_HOURS = 720.0  # 30 days — beyond this the scroll cost outweighs value
BUFFER_HOURS = 24.0
MAX_SCROLLS = 100  # iteration cap independent of time cutoff (#2)


@dataclass
class CatchupResult:
    success: bool = False
    accepted_count: int = 0
    scanned_slugs: int = 0
    matched_lead_ids: List[str] = field(default_factory=list)
    cutoff_hours: float = 0.0
    hit_cutoff: bool = False
    hit_iteration_cap: bool = False
    error: Optional[str] = None
    skipped_reason: Optional[str] = None


def _normalize_li_url(url: str) -> str:
    m = re.search(r'/in/([^/?#\s]+)', url)
    return f"/in/{m.group(1).rstrip('/')}" if m else ""


def compute_cutoff_hours(
    paused_at: Optional[datetime],
    oldest_lead_updated_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> float:
    """Compute scan cutoff from pause age and oldest pending lead age.

    Returns max(paused_at_age, oldest_lead_age) + buffer, clamped to
    [MIN_CUTOFF_HOURS, MAX_CUTOFF_HOURS].
    """
    now = now or datetime.utcnow()
    ages = []
    if paused_at is not None:
        ages.append((now - paused_at).total_seconds() / 3600.0)
    if oldest_lead_updated_at is not None:
        ages.append((now - oldest_lead_updated_at).total_seconds() / 3600.0)

    if not ages:
        # Unknown pause window — use the safe default (caller may override)
        return MIN_CUTOFF_HOURS

    raw = max(ages) + BUFFER_HOURS
    return max(MIN_CUTOFF_HOURS, min(MAX_CUTOFF_HOURS, raw))


async def run_acceptance_catchup(
    repo: Repository,
    campaign_id: str,
    cutoff_hours_override: Optional[float] = None,
    rate_limit_seconds: int = 3600,
) -> CatchupResult:
    """Run a one-shot acceptance catchup for a single campaign.

    Args:
        repo: Repository instance.
        campaign_id: Campaign to catch up.
        cutoff_hours_override: If set, use this instead of the computed value.
            Still clamped to [MIN_CUTOFF_HOURS, MAX_CUTOFF_HOURS].
        rate_limit_seconds: Skip if account.last_catchup_at is more recent
            than this. Pass 0 to bypass.

    Returns CatchupResult with outcome details. Never raises for expected
    conditions (paused/archived/expired) — sets `skipped_reason` instead.
    """
    from linauto.linkedin.actions import LinkedInActions
    from linauto.linkedin.pool import get_browser_pool

    result = CatchupResult()

    # ── Preconditions ───────────────────────────────────────────────────
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        result.error = "campaign_not_found"
        return result

    if campaign.archived:
        result.skipped_reason = "campaign_archived"
        return result

    account = await repo.get_account(campaign.account_id)
    if not account:
        result.error = "account_not_found"
        return result

    if account.status != AccountStatus.ACTIVE:
        result.skipped_reason = f"account_status_{account.status.value if hasattr(account.status, 'value') else account.status}"
        return result

    if account.paused_until and account.paused_until > datetime.utcnow():
        result.skipped_reason = "account_paused_until"
        return result

    # Rate-limit guard (#5)
    if rate_limit_seconds and account.last_catchup_at:
        elapsed = (datetime.utcnow() - account.last_catchup_at).total_seconds()
        if elapsed < rate_limit_seconds:
            result.skipped_reason = f"rate_limited_{int(rate_limit_seconds - elapsed)}s_remaining"
            return result

    # ── Compute cutoff ─────────────────────────────────────────────────
    # Phase 2 read cutover: filter via CampaignLeadAssignment. Identical
    # result set to the legacy path while dual-write is active; gives us
    # confidence in the new schema as the source of truth ahead of Phase 3.
    requested_leads = await repo.get_leads_by_status_via_assignments(
        campaign.id, LeadStatus.CONNECTION_REQUESTED
    )
    if not requested_leads:
        # Nothing to scan for — clear paused_at so we don't keep prompting
        await repo.update_campaign(campaign, paused_at=None)
        result.success = True
        result.skipped_reason = "no_pending_leads"
        return result

    oldest_lead = min(requested_leads, key=lambda l: l.updated_at or l.created_at)
    oldest_at = oldest_lead.updated_at or oldest_lead.created_at

    if cutoff_hours_override is not None:
        cutoff_hours = max(
            MIN_CUTOFF_HOURS, min(MAX_CUTOFF_HOURS, cutoff_hours_override)
        )
    else:
        cutoff_hours = compute_cutoff_hours(campaign.paused_at, oldest_at)

    result.cutoff_hours = cutoff_hours

    logger.info(
        "catchup.starting",
        campaign=campaign.name,
        account=account.name,
        cutoff_hours=cutoff_hours,
        pending_leads=len(requested_leads),
        paused_at=campaign.paused_at.isoformat() if campaign.paused_at else None,
    )

    # ── Acquire browser pool ───────────────────────────────────────────
    pool = get_browser_pool()
    try:
        pool_context = await pool.acquire(account)
    except Exception as e:
        logger.error("catchup.pool_acquire_failed", account=account.name, error=str(e))
        result.error = f"pool_acquire_failed: {e}"
        return result

    try:
        page = await pool_context.new_page()
        try:
            actions = LinkedInActions(page)
            scan = await actions.get_recent_connections(
                cutoff_hours=cutoff_hours,
                max_scrolls=MAX_SCROLLS,
            )
        finally:
            await page.close()

        if not scan.session_valid:
            logger.error("catchup.session_expired", account=account.name)
            await repo.update_account(account, status=AccountStatus.COOKIE_EXPIRED)
            result.error = "session_expired"
            return result

        if not scan.success:
            result.error = "scan_failed"
            return result

        pool.confirm_session(account.id)
        recent_slugs = set(scan.slugs)
        result.scanned_slugs = len(recent_slugs)
        result.hit_cutoff = scan.hit_cutoff
        # We hit the iteration cap (#2) when the scroll exited without
        # finding an older-than-cutoff card AND without exhausting the page.
        # `get_recent_connections` doesn't currently surface this distinctly,
        # so infer: if hit_cutoff is False and we scanned a lot, we likely
        # exhausted scrolls.
        result.hit_iteration_cap = not scan.hit_cutoff and len(recent_slugs) >= MAX_SCROLLS * 5

        # ── Match against campaign's pending leads ────────────────────
        newly_connected = []
        for lead in requested_leads:
            slug = _normalize_li_url(lead.linkedin_url)
            if not slug or slug not in recent_slugs:
                continue
            # Idempotency guard (#9): only transition + increment if not
            # already CONNECTED
            if lead.status != LeadStatus.CONNECTION_REQUESTED:
                continue
            try:
                validate_transition(lead.status, LeadStatus.CONNECTED)
            except Exception:
                continue

            already_accepted = lead.connection_accepted_at is not None
            await repo.update_lead(
                lead,
                campaign_id_override=campaign_id,
                status=LeadStatus.CONNECTED,
                connection_accepted_at=datetime.utcnow(),
            )
            if not already_accepted:
                await repo.increment_daily_stat(account.id, "connections_accepted")
            newly_connected.append(lead)
            logger.info("catchup.connected", url=lead.linkedin_url)

        result.accepted_count = len(newly_connected)
        result.matched_lead_ids = [l.id for l in newly_connected]

        # ── Schedule followups (mirrors daily checker) ────────────────
        if campaign.followup_enabled:
            has_messages = any(
                getattr(campaign, f"followup_message_{i}", None) for i in (1, 2, 3)
            )
            if has_messages and campaign.followup_delay_hours:
                followup_time = datetime.utcnow() + timedelta(
                    hours=campaign.followup_delay_hours
                )
                for lead in newly_connected:
                    try:
                        validate_transition(lead.status, LeadStatus.FOLLOWUP_SCHEDULED)
                        await repo.update_lead(
                            lead,
                            campaign_id_override=campaign_id,
                            status=LeadStatus.FOLLOWUP_SCHEDULED,
                            scheduled_at=followup_time,
                        )
                    except Exception as e:
                        logger.warning(
                            "catchup.followup_schedule_failed",
                            url=lead.linkedin_url,
                            error=str(e),
                        )

        # ── Stamp completion: clear paused_at, record last_catchup_at ─
        await repo.update_campaign(campaign, paused_at=None)
        await repo.update_account(account, last_catchup_at=datetime.utcnow())

        await repo.log_action(
            account_id=account.id,
            action_type=ActionType.ACCEPTANCE_CHECK_SUMMARY,
            status=ActionLogStatus.SUCCESS,
            details={
                "source": "catchup",
                "campaign_id": campaign.id,
                "accepted": result.accepted_count,
                "scanned": result.scanned_slugs,
                "cutoff_hours": cutoff_hours,
                "hit_cutoff": result.hit_cutoff,
                "hit_iteration_cap": result.hit_iteration_cap,
            },
        )

        result.success = True
        logger.info(
            "catchup.completed",
            campaign=campaign.name,
            accepted=result.accepted_count,
            scanned=result.scanned_slugs,
            cutoff_hours=cutoff_hours,
        )
        return result

    except Exception as e:
        err_str = str(e)
        logger.error("catchup.failed", campaign=campaign.name, error=err_str)
        result.error = err_str
        # If the exception looks like cookie expiry (e.g. redirect loop on
        # connections page), mark the account so the next dispatch cycle
        # doesn't blindly retry. Same classifier as the dispatchers use.
        from linauto.safety.error_signals import is_session_expired_signal
        if is_session_expired_signal(err_str):
            logger.error("catchup.session_expired_detected", account=account.name)
            try:
                await repo.update_account(account, status=AccountStatus.COOKIE_EXPIRED)
            except Exception as inner:
                logger.warning("catchup.cookie_expired_update_failed", error=str(inner))
        return result
    finally:
        await pool.release_idle(account.id)

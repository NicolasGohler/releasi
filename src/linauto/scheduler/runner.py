"""APScheduler-based scheduler daemon for unattended operation."""
from __future__ import annotations

import asyncio
import random
import signal
from datetime import date, datetime, timedelta
from typing import Optional

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from linauto.config import get_settings
from linauto.db.engine import init_db, get_session_factory, close_db
from linauto.db.models import (
    Account, AccountStatus, Campaign, CampaignStatus,
    Lead, LeadStatus, ActionType, ActionLogStatus,
)
from linauto.db.repository import Repository
from linauto.linkedin.pool import get_browser_pool, init_pool, shutdown_pool
from linauto.scheduler.planner import generate_daily_plan, SlotType
from linauto.safety.cooldown import is_cooldown_expired, calculate_cooldown_resume, push_cooldown_one_day
from linauto.safety import limits as rate_limits
from linauto.campaign.state_machine import validate_transition
from linauto.notifications.slack import notify as slack_notify

logger = structlog.get_logger()

# Per-account session tracking: when the last dispatch session ended (UTC naive).
# Prevents rapid-fire dispatching across 5-min cycles — enforces inter-session gap.
_last_session_end: dict = {}

import re as _re


def _normalize_li_url(url: str) -> str:
    """Extract '/in/slug' from any LinkedIn profile URL variant for comparison."""
    m = _re.search(r'/in/([^/?#\s]+)', url)
    if m:
        return f"/in/{m.group(1).rstrip('/')}"
    return ""


# Keep-alive activity rotation: (name, url) pairs with selection weights
_KEEPALIVE_ACTIVITIES = [
    ("feed", "https://www.linkedin.com/feed/"),
    ("notifications", "https://www.linkedin.com/notifications/"),
    ("network", "https://www.linkedin.com/mynetwork/"),
    ("messaging", "https://www.linkedin.com/messaging/"),
    ("jobs", "https://www.linkedin.com/jobs/"),
    ("learning", "https://www.linkedin.com/learning/"),
]
# Weights for step-2 selection (index 1 onwards): notifications/network/messaging/jobs/learning
_KEEPALIVE_STEP2_WEIGHTS = [3, 2, 2, 1, 1]


async def _http_check_session(
    li_at_cookie: str,
    user_agent: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> bool:
    """Fast HTTP session check routed through the account's proxy, ~1-2s.

    Always uses the proxy when available so LinkedIn sees a consistent IP.
    Sending li_at from a datacenter IP (unproxied) is a session invalidation trigger.
    Returns True if valid, False if expired/redirected. Raises on network errors.
    """
    import httpx
    login_patterns = ["/login", "/uas/login", "/signup", "/checkpoint/"]
    headers = {
        "Cookie": f"li_at={li_at_cookie}",
        "User-Agent": user_agent or "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        client_kwargs = dict(headers=headers, follow_redirects=True, timeout=10.0)
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get("https://www.linkedin.com/feed/")
        final_url = str(resp.url)
        if any(p in final_url for p in login_patterns):
            return False
        return resp.status_code == 200
    except Exception:
        raise


async def _get_repo() -> tuple:
    """Get a Repository + session."""
    session = get_session_factory()()
    return Repository(session), session


async def daily_planning_sweep():
    """
    Generate today's plan for all active accounts/campaigns.

    Runs daily at 06:00. Assigns scheduled_at to pending leads
    based on clustered timing.
    """
    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                logger.info(
                    "planner.account_paused",
                    account=account.name,
                    until=str(account.paused_until),
                )
                continue

            campaigns = await repo.get_active_campaigns(account.id)
            for campaign in campaigns:
                now = datetime.utcnow()

                # Reset any stale past-scheduled leads back to PENDING.
                # This cleans up slots from previous days or missed windows
                # (e.g. multiple restarts, container downtime) so they
                # re-enter the pending pool rather than silently accumulating.
                stale = await repo.reset_stale_scheduled_leads(campaign.id)
                if stale:
                    logger.info(
                        "planner.stale_reset",
                        campaign=campaign.name,
                        count=stale,
                    )

                pending = await repo.get_pending_leads(campaign.id)
                if not pending:
                    continue

                lead_ids = [l.id for l in pending]

                sent_today = await repo.get_daily_requests_sent(account.id)
                # Subtract leads already scheduled in the future — they count
                # against today's budget and must not be double-scheduled on
                # restarts or mid-day replanning calls.
                future_scheduled = await repo.count_future_scheduled_leads(campaign.id)
                remaining_budget = max(0, account.daily_limit - sent_today - future_scheduled)

                if remaining_budget == 0:
                    logger.info(
                        "planner.budget_exhausted_skip",
                        account=account.name,
                        daily_limit=account.daily_limit,
                        sent_today=sent_today,
                        future_scheduled=future_scheduled,
                    )
                    continue

                plan = generate_daily_plan(
                    account_id=account.id,
                    day=date.today(),
                    pending_lead_ids=lead_ids,
                    daily_limit=account.daily_limit,
                    timezone_str=account.timezone,
                    campaign_weekend_enabled=campaign.weekend_enabled,
                    effective_start=now + timedelta(minutes=2),
                    remaining_budget=remaining_budget,
                )

                # Assign scheduled_at to leads for connection_request slots.
                # If a slot's time is already past (e.g. planner ran late in the day
                # or on local startup mid-morning), schedule it for now so the
                # dispatcher picks it up in the next cycle rather than silently
                # dropping it and under-delivering for the day.
                scheduled_count = 0
                for slot in plan:
                    if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                        slot_time = slot.scheduled_at if slot.scheduled_at > now else now
                        await repo.update_lead_schedule(
                            slot.lead_id, slot_time, LeadStatus.SCHEDULED
                        )
                        scheduled_count += 1

                if scheduled_count == 0 and lead_ids:
                    # All generated slots were in the past (edge case: job ran very
                    # late). Fallback: schedule for tomorrow so leads aren't stuck.
                    logger.warning(
                        "planner.all_slots_past_scheduling_tomorrow",
                        account=account.name,
                        campaign=campaign.name,
                    )
                    tomorrow = date.today() + timedelta(days=1)
                    fallback_plan = generate_daily_plan(
                        account_id=account.id,
                        day=tomorrow,
                        pending_lead_ids=lead_ids,
                        daily_limit=account.daily_limit,
                        timezone_str=account.timezone,
                        campaign_weekend_enabled=campaign.weekend_enabled,
                    )
                    for slot in fallback_plan:
                        if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                            await repo.update_lead_schedule(
                                slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED
                            )
                            scheduled_count += 1
                    plan = fallback_plan

                # Log the plan
                await repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    action_type=ActionType.DAILY_PLAN_GENERATED,
                    status=ActionLogStatus.SUCCESS,
                    details={
                        "date": date.today().isoformat(),
                        "total_slots": len(plan),
                        "connection_requests": sum(
                            1 for s in plan if s.slot_type == SlotType.CONNECTION_REQUEST
                        ),
                    },
                )

                logger.info(
                    "planner.campaign_planned",
                    campaign=campaign.name,
                    slots=len(plan),
                )

    except Exception as e:
        logger.error("planner.sweep_failed", error=str(e))
    finally:
        await session.close()


async def dispatch():
    """
    Execute due actions.

    Runs every 5 minutes. Picks up leads where scheduled_at <= now()
    and executes them.
    """
    repo, session = await _get_repo()
    try:
        now = datetime.utcnow()  # planner stores UTC naive; compare with utcnow()
        accounts = await repo.list_active_accounts()

        for account in accounts:
            # Skip paused accounts
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            # Enforce minimum inter-session gap to prevent rapid-fire bursts.
            # After any session that sent ≥1 lead, we wait at least
            # inter_session_delay[0] seconds before starting the next session.
            _min_gap = get_settings().inter_session_delay[0]  # e.g. 2700s = 45 min
            _last_end = _last_session_end.get(account.id)
            if _last_end is not None:
                _elapsed = (now - _last_end).total_seconds()
                if _elapsed < _min_gap:
                    logger.debug(
                        "dispatch.inter_session_gap_enforced",
                        account=account.name,
                        elapsed_min=round(_elapsed / 60, 1),
                        gap_min=round(_min_gap / 60, 1),
                    )
                    continue

            # Check daily limits
            sent_today_count = await repo.get_daily_requests_sent(account.id)
            can_send, remaining = await rate_limits.can_send_today(
                account.id,
                account.daily_limit,
                sent_today_count,
            )

            if not can_send:
                logger.info("dispatch.daily_limit_reached", account=account.name)
                continue

            # Bandwidth guard — stop before opening any browser pages
            bw_used = await repo.get_daily_proxy_mb(account.id)
            bw_limit = get_settings().daily_bandwidth_limit_mb
            if bw_used >= bw_limit:
                logger.warning(
                    "dispatch.bandwidth_limit_reached",
                    account=account.name,
                    used_mb=round(bw_used, 1),
                    limit_mb=bw_limit,
                )
                continue

            # Quick DB pre-check: skip browser entirely if no leads are due.
            # Avoids loading LinkedIn pages (and burning proxy bandwidth) when
            # the scheduler fires outside of the account's work window.
            campaigns_preview = await repo.get_active_campaigns(account.id)
            any_due = False
            for _c in campaigns_preview:
                if await repo.get_scheduled_leads(_c.id, before=now):
                    any_due = True
                    break
            if not any_due:
                continue

            # Acquire pool browser once per account
            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.error("dispatch.pool_acquire_failed", account=account.name, error=str(e))
                continue

            try:
                # Pre-dispatch browser session check: navigate to feed once
                # using the real Playwright browser before touching any leads.
                # Skipped when the session was recently confirmed (< 30 min ago)
                # to avoid loading LinkedIn on every 5-min cycle.
                #   - redirect to /login  → SESSION_EXPIRED  → mark cookie_expired
                #   - navigation timeout  → network/proxy error → skip cycle, retry in 5 min
                from linauto.linkedin.pool import _VALIDATION_COOLDOWN as _VC
                _seconds_since_check = pool.time_since_validated(account.id)
                if _seconds_since_check >= _VC:
                    _pre_page = await pool_context.new_page()
                    try:
                        from linauto.linkedin.navigator import LinkedInNavigator
                        _nav = LinkedInNavigator(_pre_page)
                        _feed = await _nav.go_to_feed()
                    finally:
                        await _pre_page.close()

                    if not _feed.success:
                        logger.warning(
                            "dispatch.pre_check_network_error",
                            account=account.name,
                            error=_feed.error,
                        )
                        # Proxy/network issue — skip this cycle, will retry in 5 min
                        continue
                    if not _feed.session_valid:
                        logger.error("dispatch.pre_check_session_expired", account=account.name)
                        await repo.update_account(account, status="cookie_expired")
                        await slack_notify(
                            f":warning: *Cookie expired* — account *{account.name}* (detected at dispatch pre-check). "
                            "Update the cookie in account settings."
                        )
                        await repo.log_action(
                            account_id=account.id,
                            action_type=ActionType.ERROR,
                            status=ActionLogStatus.FAILED,
                            details={"reason": "pre_dispatch_session_expired"},
                        )
                        continue
                    # Feed navigation confirmed session healthy — stamp validated time
                    pool.confirm_session(account.id)
                    await repo.add_proxy_mb(account.id, 1.0)  # feed pre-check page
                else:
                    logger.debug(
                        "dispatch.pre_check_skipped",
                        account=account.name,
                        seconds_since_check=round(_seconds_since_check),
                    )

                campaigns = await repo.get_active_campaigns(account.id)
                for campaign in campaigns:
                    due_leads = list(await repo.get_scheduled_leads(campaign.id, before=now))
                    if not due_leads:
                        continue

                    # Target = how many *successful* sends we want this cycle.
                    # Cap to actions_per_session[1] so one dispatch cycle never
                    # sends more than one session's worth of leads — this prevents
                    # the "10+ connections in 10 minutes" burst when restarting
                    # mid-day with many slots clustered near now.
                    _max_per_session = get_settings().actions_per_session[1]
                    target = min(
                        remaining if remaining is not None else len(due_leads),
                        _max_per_session,
                    )

                    from linauto.campaign.executor import CampaignExecutor
                    executor = CampaignExecutor(repo, browser_context=pool_context)

                    successful_sends = 0
                    attempts = 0
                    consecutive_session_errors = 0   # non-network failures → cookie suspect
                    consecutive_network_errors = 0   # timeouts/proxy → network suspect
                    max_attempts = target * 4  # Safety cap: allow skips+errors without loop
                    lead_queue = list(due_leads)  # Fixed queue — no mid-session injections
                    stop_account = False
                    backfill_count = 0  # Skipped/errored leads needing future rescheduling

                    while lead_queue and successful_sends < target and attempts < max_attempts:
                        attempts += 1
                        lead = lead_queue.pop(0)
                        result = await executor.execute_single_lead(account, campaign, lead)

                        if result.get("soft_limit_reached"):
                            # Rate-limit modal (soft block) — short 2–4 hour backoff.
                            # SCHEDULED leads stay as-is; they'll be picked up on resume.
                            tz = account.timezone or "UTC"
                            backoff_hours = random.uniform(2, 4)
                            resume = datetime.utcnow() + timedelta(hours=backoff_hours)
                            await repo.update_account(account, paused_until=resume)
                            await repo.log_action(
                                account_id=account.id,
                                campaign_id=campaign.id,
                                action_type=ActionType.COOLDOWN_STARTED,
                                status=ActionLogStatus.SUCCESS,
                                details={"paused_until": str(resume), "reason": "rate_limit_modal", "backoff_hours": round(backoff_hours, 1)},
                            )
                            logger.warning(
                                "dispatch.soft_limit_backoff",
                                account=account.name,
                                resume=str(resume),
                                backoff_hours=round(backoff_hours, 1),
                            )
                            stop_account = True
                            break

                        if result.get("limit_reached"):
                            # Trigger cooldown
                            resume = calculate_cooldown_resume(account.timezone)
                            await repo.update_account(account, paused_until=resume)
                            await repo.bulk_update_lead_status(
                                campaign.id,
                                from_status=LeadStatus.SCHEDULED,
                                to_status=LeadStatus.LIMIT_PAUSED,
                            )
                            await repo.log_action(
                                account_id=account.id,
                                campaign_id=campaign.id,
                                action_type=ActionType.COOLDOWN_STARTED,
                                status=ActionLogStatus.SUCCESS,
                                details={"paused_until": str(resume)},
                            )
                            logger.warning(
                                "dispatch.cooldown_started",
                                account=account.name,
                                resume=str(resume),
                            )
                            stop_account = True
                            break

                        if result.get("fatal"):
                            # CAPTCHA or session expired — stop this account
                            stop_account = True
                            break

                        if result.get("success"):
                            successful_sends += 1
                            consecutive_session_errors = 0
                            consecutive_network_errors = 0
                            await repo.add_proxy_mb(account.id, 2.0)  # ~2 MB per connection request
                        elif result.get("skipped"):
                            # Profile was skipped (already connected, already accepted,
                            # email required, etc.). Never counts against session health.
                            # Don't inject a replacement into this session — schedule it
                            # after the current day's last slot to avoid rapid-fire sends.
                            consecutive_session_errors = 0
                            consecutive_network_errors = 0
                            backfill_count += 1
                        elif result.get("network_error"):
                            # Proxy/timeout failure — don't penalise the session
                            consecutive_network_errors += 1
                            consecutive_session_errors = 0
                            if consecutive_network_errors >= 3:
                                logger.warning(
                                    "dispatch.proxy_connectivity_issues",
                                    account=account.name,
                                    campaign=campaign.name,
                                    consecutive=consecutive_network_errors,
                                )
                                await repo.log_action(
                                    account_id=account.id,
                                    campaign_id=campaign.id,
                                    action_type=ActionType.ERROR,
                                    status=ActionLogStatus.FAILED,
                                    details={"reason": "consecutive_network_errors", "count": consecutive_network_errors},
                                )
                                stop_account = True
                                break
                        else:
                            consecutive_session_errors += 1
                            consecutive_network_errors = 0

                            # 3+ consecutive session errors → cookie likely expired
                            if consecutive_session_errors >= 3:
                                logger.error(
                                    "dispatch.consecutive_errors_detected",
                                    account=account.name,
                                    campaign=campaign.name,
                                    consecutive=consecutive_session_errors,
                                )
                                await repo.update_account(account, status="cookie_expired")
                                await slack_notify(
                                    f":warning: *Cookie expired* — account *{account.name}* "
                                    f"({consecutive_session_errors} consecutive session errors on campaign *{campaign.name}*)."
                                )
                                # Reset SCHEDULED leads back to PENDING so they're
                                # re-planned when the cookie is renewed
                                await repo.bulk_update_lead_status(
                                    campaign.id,
                                    from_status=LeadStatus.SCHEDULED,
                                    to_status=LeadStatus.PENDING,
                                )
                                await repo.log_action(
                                    account_id=account.id,
                                    campaign_id=campaign.id,
                                    action_type=ActionType.ERROR,
                                    status=ActionLogStatus.FAILED,
                                    details={"reason": "consecutive_navigation_errors", "count": consecutive_session_errors},
                                )
                                stop_account = True
                                break

                            # Lead errored — schedule a replacement later in the day
                            backfill_count += 1

                    # Post-session: schedule backfills for any skipped/errored leads.
                    # Anchor the first backfill after the last future scheduled slot +
                    # one inter-session gap, then space each subsequent backfill by
                    # intra_session_delay[0] — so they form a natural future session
                    # instead of firing immediately.
                    # Cap by remaining daily budget to prevent over-scheduling.
                    if backfill_count > 0 and not stop_account:
                        _settings = get_settings()
                        _inter_gap = _settings.inter_session_delay[0]   # seconds (e.g. 2700 = 45 min)
                        _intra_gap = _settings.intra_session_delay[0]   # seconds (e.g. 120 = 2 min)
                        _sent_today = await repo.get_daily_requests_sent(account.id)
                        _future_scheduled = await repo.count_future_scheduled_leads(campaign.id)
                        _available = max(0, account.daily_limit - _sent_today - _future_scheduled)
                        _actual_backfills = min(backfill_count, _available)
                        if _actual_backfills < backfill_count:
                            logger.info(
                                "dispatch.backfills_budget_capped",
                                campaign=campaign.name,
                                requested=backfill_count,
                                scheduled=_actual_backfills,
                                daily_limit=account.daily_limit,
                                sent_today=_sent_today,
                                future_scheduled=_future_scheduled,
                            )
                        _latest_slot = await repo.get_latest_future_scheduled_at(campaign.id)
                        _now_utc = datetime.utcnow()
                        _anchor = max(_latest_slot or _now_utc, _now_utc) + timedelta(seconds=_inter_gap)
                        _scheduled = 0
                        for _i in range(_actual_backfills):
                            _pending = await repo.get_pending_leads(campaign.id, limit=1)
                            if not _pending:
                                break
                            _bl = _pending[0]
                            _slot_time = _anchor + timedelta(seconds=_i * _intra_gap)
                            await repo.update_lead(
                                _bl,
                                status=LeadStatus.SCHEDULED,
                                scheduled_at=_slot_time,
                            )
                            _scheduled += 1
                        if _scheduled:
                            logger.info(
                                "dispatch.backfills_scheduled",
                                campaign=campaign.name,
                                count=_scheduled,
                                first_slot=_anchor.strftime("%H:%M UTC"),
                            )

                    if successful_sends > 0:
                        # At least one lead went through — session is confirmed healthy.
                        # Record session end time so the inter-session gap is enforced
                        # before the next dispatch cycle processes this account.
                        pool.confirm_session(account.id)
                        _last_session_end[account.id] = datetime.utcnow()
                        day_sent = sent_today_count + successful_sends
                        day_target = target + sent_today_count  # target was remaining at cycle start
                        logger.info(
                            "dispatch.cycle_complete",
                            campaign=campaign.name,
                            cycle_sent=successful_sends,
                            day_sent=day_sent,
                            day_target=day_target,
                            day_remaining=max(0, day_target - day_sent),
                        )

                    if stop_account:
                        break
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("dispatch.failed", error=str(e))
    finally:
        await session.close()


async def check_cooldowns():
    """
    Check if paused accounts can resume.

    Runs daily. When cooldown expires, verifies with LinkedIn
    invitation manager. If still blocked, pushes to next day.
    """
    repo, session = await _get_repo()
    try:
        accounts = await repo.list_paused_accounts()
        for account in accounts:
            if not is_cooldown_expired(account.paused_until):
                continue

            logger.info("cooldown.checking", account=account.name)

            # For now, just clear cooldown and let the dispatcher verify
            # A future improvement could check the invitation manager page
            await repo.update_account(account, paused_until=None)
            await repo.bulk_update_lead_status_for_account(
                account.id,
                from_status=LeadStatus.LIMIT_PAUSED,
                to_status=LeadStatus.PENDING,
            )

            await repo.log_action(
                account_id=account.id,
                action_type=ActionType.COOLDOWN_ENDED,
                status=ActionLogStatus.SUCCESS,
                details={"resumed_at": datetime.utcnow().isoformat()},
            )
            logger.info("cooldown.resumed", account=account.name)

    except Exception as e:
        logger.error("cooldown.check_failed", error=str(e))
    finally:
        await session.close()


async def check_acceptances():
    """
    Check for accepted connection requests using invitation manager diff.

    Runs once daily. Loads the invitation manager page ONCE, extracts all
    pending sent invitation URLs, diffs against DB leads with CONNECTION_REQUESTED
    status. Leads that have "disappeared" from the invitation list are visited
    individually to confirm whether they accepted (→ CONNECTED) or declined/expired
    (→ WITHDRAWN). This replaces the old approach of visiting up to 30 profiles
    every 3 hours.
    """
    from linauto.linkedin.actions import LinkedInActions
    from linauto.safety.delays import DelayGenerator

    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        for account in accounts:
            # Skip paused accounts
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            # Collect all CONNECTION_REQUESTED leads across all active campaigns
            campaigns = await repo.get_active_campaigns(account.id)
            campaign_map = {c.id: c for c in campaigns}
            requested_leads = []
            for campaign in campaigns:
                leads = await repo.get_leads_by_status(campaign.id, LeadStatus.CONNECTION_REQUESTED)
                requested_leads.extend(leads)

            # Determine if a browser is needed
            needs_browser = bool(requested_leads) or bool(account.withdraw_threshold)
            if not needs_browser:
                continue

            # Bandwidth guard
            bw_used = await repo.get_daily_proxy_mb(account.id)
            bw_limit = get_settings().daily_bandwidth_limit_mb
            if bw_used >= bw_limit:
                logger.warning(
                    "acceptance.bandwidth_limit_reached",
                    account=account.name,
                    used_mb=round(bw_used, 1),
                    limit_mb=bw_limit,
                )
                continue

            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.error("acceptance.pool_acquire_failed", account=account.name, error=str(e))
                continue

            try:
                # Open invitation manager page
                inv_page = await pool_context.new_page()
                actions = LinkedInActions(inv_page)

                # Pass tracked slugs so pagination stops as soon as all DB leads are visible
                tracked_slugs = {
                    n for lead in requested_leads
                    if (n := _normalize_li_url(lead.linkedin_url))
                }
                result = await actions.get_sent_invitation_urls(stop_when_found=tracked_slugs)

                if not result.success:
                    logger.warning("acceptance.invitation_manager_failed", account=account.name)
                    await inv_page.close()
                    continue

                if not result.session_valid:
                    logger.error("acceptance.session_expired", account=account.name)
                    await repo.update_account(account, status="cookie_expired")
                    await slack_notify(
                        f":warning: *Cookie expired* — account *{account.name}* (detected by acceptance checker)."
                    )
                    await repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": "acceptance_checker_session_expired"},
                    )
                    await inv_page.close()
                    continue

                # Build normalized set of pending invitation slugs
                pending_normalized = {
                    _normalize_li_url(u) for u in result.urls if _normalize_li_url(u)
                }

                pool.confirm_session(account.id)
                await repo.add_proxy_mb(account.id, 5.0)  # invitation manager + profile visits
                logger.info(
                    "acceptance.invitation_manager_loaded",
                    account=account.name,
                    pending_count=len(result.urls),
                    db_requested=len(requested_leads),
                )

                # Withdrawal check — do this while still on the invitation manager page
                if account.withdraw_threshold and len(result.urls) > account.withdraw_threshold:
                    excess = len(result.urls) - account.withdraw_threshold
                    to_withdraw = min(excess, 10)
                    logger.info(
                        "withdraw.starting",
                        account=account.name,
                        pending=len(result.urls),
                        threshold=account.withdraw_threshold,
                        withdrawing=to_withdraw,
                    )
                    withdrawn_urls = await actions.withdraw_oldest_invitations(
                        to_withdraw, already_on_page=True
                    )
                    for url in withdrawn_urls:
                        matching = await repo.get_leads_by_url(account.id, url)
                        for ml in matching:
                            if ml.status == LeadStatus.CONNECTION_REQUESTED:
                                validate_transition(ml.status, LeadStatus.WITHDRAWN)
                                await repo.update_lead(ml, status=LeadStatus.WITHDRAWN)
                    if withdrawn_urls:
                        await repo.log_action(
                            account_id=account.id,
                            action_type=ActionType.INVITATION_WITHDRAWN,
                            status=ActionLogStatus.SUCCESS,
                            details={
                                "withdrawn": len(withdrawn_urls),
                                "pending_before": len(result.urls),
                            },
                        )
                elif account.withdraw_threshold:
                    logger.info(
                        "withdraw.under_threshold",
                        account=account.name,
                        pending=len(result.urls),
                        threshold=account.withdraw_threshold,
                    )

                await inv_page.close()

                # Diff: find leads that disappeared from the invitation manager
                disappeared = [
                    lead for lead in requested_leads
                    if (n := _normalize_li_url(lead.linkedin_url)) and n not in pending_normalized
                ]
                logger.info(
                    "acceptance.diff_result",
                    account=account.name,
                    disappeared=len(disappeared),
                )

                if not disappeared:
                    continue

                # Visit disappeared profiles to confirm accepted vs declined/expired
                confirm_page = await pool_context.new_page()
                try:
                    confirm_actions = LinkedInActions(confirm_page)
                    newly_connected = []  # list of (lead, campaign) pairs

                    for lead in disappeared:
                        campaign = campaign_map.get(lead.campaign_id)
                        if campaign is None:
                            continue
                        status = await confirm_actions.check_connection_status(lead.linkedin_url)
                        if status == "connected":
                            validate_transition(lead.status, LeadStatus.CONNECTED)
                            await repo.update_lead(
                                lead,
                                status=LeadStatus.CONNECTED,
                                connection_accepted_at=datetime.utcnow(),
                            )
                            await repo.log_action(
                                account_id=account.id,
                                campaign_id=campaign.id,
                                lead_id=lead.id,
                                action_type=ActionType.CHECK_ACCEPTANCE,
                                status=ActionLogStatus.SUCCESS,
                                details={"accepted": True},
                            )
                            await repo.increment_daily_stat(account.id, "connections_accepted")
                            newly_connected.append((lead, campaign))
                            logger.info("acceptance.connected", url=lead.linkedin_url)
                        elif status == "not_connected":
                            # Confirmed: invitation gone, not connected → declined or expired
                            validate_transition(lead.status, LeadStatus.WITHDRAWN)
                            await repo.update_lead(lead, status=LeadStatus.WITHDRAWN)
                            await repo.log_action(
                                account_id=account.id,
                                campaign_id=campaign.id,
                                lead_id=lead.id,
                                action_type=ActionType.CHECK_ACCEPTANCE,
                                status=ActionLogStatus.SUCCESS,
                                details={"accepted": False, "status": "not_connected"},
                            )
                            logger.info("acceptance.declined_or_expired", url=lead.linkedin_url)
                        elif status == "unknown":
                            # Profile visit inconclusive (network error, page state ambiguous) — leave as-is
                            logger.debug("acceptance.check_inconclusive", url=lead.linkedin_url)
                        else:
                            # "pending" — leave as-is (URL normalization edge case)
                            logger.debug("acceptance.still_pending", url=lead.linkedin_url)

                        await DelayGenerator().micro_delay(2, 5)
                finally:
                    await confirm_page.close()

                # Schedule or send follow-up messages for newly connected leads
                for lead, campaign in newly_connected:
                    if not campaign.followup_enabled:
                        continue
                    has_messages = any(
                        getattr(campaign, f"followup_message_{i}", None)
                        for i in (1, 2, 3)
                    )
                    if not has_messages:
                        continue

                    delay_hours = campaign.followup_delay_hours or 0
                    if delay_hours > 0:
                        followup_time = datetime.utcnow() + timedelta(hours=delay_hours)
                        validate_transition(lead.status, LeadStatus.FOLLOWUP_SCHEDULED)
                        await repo.update_lead(
                            lead,
                            status=LeadStatus.FOLLOWUP_SCHEDULED,
                            scheduled_at=followup_time,
                        )
                        logger.info(
                            "followup.scheduled",
                            url=lead.linkedin_url,
                            campaign=campaign.name,
                            scheduled_at=followup_time.isoformat(),
                            delay_hours=delay_hours,
                        )
                    else:
                        # Send immediately (delay=0) — use existing pool context (already acquired)
                        try:
                            from linauto.campaign.executor import CampaignExecutor
                            executor = CampaignExecutor(repo, browser_context=pool_context)
                            logger.info(
                                "followup.starting_sequence",
                                url=lead.linkedin_url,
                                campaign=campaign.name,
                            )
                            fu_result = await executor.execute_followup_sequence(
                                account, campaign, lead
                            )
                            if fu_result["success"]:
                                validate_transition(lead.status, LeadStatus.FOLLOWUP_SENT)
                                await repo.update_lead(
                                    lead,
                                    status=LeadStatus.FOLLOWUP_SENT,
                                    followup_sent_at=datetime.utcnow(),
                                )
                                logger.info(
                                    "followup.sequence_done",
                                    url=lead.linkedin_url,
                                    messages_sent=fu_result["messages_sent"],
                                )
                            else:
                                logger.warning("followup.sequence_failed", url=lead.linkedin_url)
                            if fu_result.get("fatal"):
                                break
                        except Exception as e:
                            logger.error("followup.immediate_failed", url=lead.linkedin_url, error=str(e))

            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("acceptance.check_failed", error=str(e))
    finally:
        await session.close()


async def dispatch_followups():
    """
    Send follow-up messages for leads where the delay has elapsed.

    Runs every 30 minutes. Picks up FOLLOWUP_SCHEDULED leads where
    scheduled_at <= now and sends the follow-up message sequence.
    """
    repo, session = await _get_repo()
    try:
        now = datetime.utcnow()
        accounts = await repo.list_active_accounts()

        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            # Check if any campaign has due follow-ups before acquiring pool
            campaigns = await repo.get_active_campaigns(account.id)
            has_due = False
            for campaign in campaigns:
                if campaign.followup_enabled:
                    due = await repo.get_followup_due_leads(campaign.id, before=now)
                    if due:
                        has_due = True
                        break
            if not has_due:
                continue

            # Bandwidth guard
            bw_used = await repo.get_daily_proxy_mb(account.id)
            bw_limit = get_settings().daily_bandwidth_limit_mb
            if bw_used >= bw_limit:
                logger.warning(
                    "followup_dispatch.bandwidth_limit_reached",
                    account=account.name,
                    used_mb=round(bw_used, 1),
                    limit_mb=bw_limit,
                )
                continue

            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.error("followup_dispatch.pool_acquire_failed", account=account.name, error=str(e))
                continue

            try:
                from linauto.campaign.executor import CampaignExecutor
                executor = CampaignExecutor(repo, browser_context=pool_context)

                for campaign in campaigns:
                    if not campaign.followup_enabled:
                        continue

                    due_leads = await repo.get_followup_due_leads(campaign.id, before=now)
                    if not due_leads:
                        continue

                    for lead in due_leads:
                        logger.info(
                            "followup.starting_sequence",
                            url=lead.linkedin_url,
                            campaign=campaign.name,
                        )
                        fu_result = await executor.execute_followup_sequence(
                            account, campaign, lead
                        )
                        await repo.add_proxy_mb(account.id, 1.0)  # messaging page
                        if fu_result["success"]:
                            validate_transition(lead.status, LeadStatus.FOLLOWUP_SENT)
                            await repo.update_lead(
                                lead,
                                status=LeadStatus.FOLLOWUP_SENT,
                                followup_sent_at=datetime.utcnow(),
                            )
                            logger.info(
                                "followup.sequence_done",
                                url=lead.linkedin_url,
                                messages_sent=fu_result["messages_sent"],
                            )
                        else:
                            new_retries = (lead.retry_count or 0) + 1
                            if new_retries >= 3:
                                validate_transition(lead.status, LeadStatus.ERROR)
                                await repo.update_lead(
                                    lead,
                                    status=LeadStatus.ERROR,
                                    retry_count=new_retries,
                                )
                                logger.warning(
                                    "followup.max_retries_reached",
                                    url=lead.linkedin_url,
                                    retries=new_retries,
                                )
                            else:
                                await repo.update_lead(lead, retry_count=new_retries)
                                logger.warning(
                                    "followup.sequence_failed",
                                    url=lead.linkedin_url,
                                    retry=new_retries,
                                )
                        if fu_result.get("fatal"):
                            break
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("followup_dispatch.failed", error=str(e))
    finally:
        await session.close()


async def check_cookie_health():
    """
    Proactive session validation for all active AND cookie_expired accounts.

    Runs every 6 hours. Routes through the account's proxy so LinkedIn sees a
    consistent IP — unproxied datacenter requests trigger session invalidation.
    Also checks cookie_expired accounts so they can self-recover after re-login.
    """
    from sqlalchemy import select as sa_select
    from linauto.db.models import AccountStatus

    repo, session = await _get_repo()
    try:
        # Check both active and cookie_expired accounts (to enable self-recovery)
        result = await session.execute(
            sa_select(Account).where(
                Account.status.in_([AccountStatus.ACTIVE, AccountStatus.COOKIE_EXPIRED]),
                Account.archived == False,  # noqa: E712
            )
        )
        accounts = result.scalars().all()

        for account in accounts:
            if not account.li_at_cookie:
                continue

            # Skip accounts that are temporarily paused (rate-limit, manual pause, etc.)
            # No point checking cookie health for accounts that can't dispatch anyway.
            if account.paused_until and account.paused_until > datetime.utcnow():
                logger.debug("cookie_health.skipped_paused", account=account.name)
                continue

            # Build proxy URL — prefer direct proxy_url, fall back to country-based builder
            proxy_url = account.proxy_url
            if not proxy_url and account.proxy_country:
                from linauto.linkedin.browser import _build_proxy_url
                try:
                    proxy_url = _build_proxy_url(account.id, account.proxy_country)
                except Exception:
                    pass

            try:
                valid = await _http_check_session(account.li_at_cookie, account.user_agent, proxy_url)
            except Exception as e:
                logger.warning("cookie_health.check_error", account=account.name, error=str(e))
                continue  # Network error — don't mark expired, try again next cycle

            if valid:
                logger.info("cookie_health.valid", account=account.name, status=account.status)
                if account.status == AccountStatus.COOKIE_EXPIRED:
                    await repo.update_account(account, status="active")
                    logger.info("cookie_health.auto_recovered", account=account.name)
            else:
                if account.status == AccountStatus.ACTIVE:
                    logger.warning("cookie_health.expired", account=account.name)
                    await repo.update_account(account, status="cookie_expired")
                    await repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": "cookie_expired_detected_by_health_check"},
                    )
                else:
                    logger.debug("cookie_health.still_expired", account=account.name)

    except Exception as e:
        logger.error("cookie_health.sweep_failed", error=str(e))
    finally:
        await session.close()


async def keep_alive():
    """
    Daily organic morning session — simulates a user opening LinkedIn in the morning.

    Runs once per day at 8:00 ±90 min. Does a multi-step browsing sequence
    (feed scroll + one additional page) to look like natural morning usage.
    Session expiry is detected reactively by the dispatcher (3 consecutive errors),
    not by this job. This job's purpose is organic-looking activity, not health checking.
    Skips accounts whose browser is currently in use by the dispatcher.
    """
    repo, session = await _get_repo()
    try:
        pool = get_browser_pool()
        accounts = await repo.list_active_accounts()

        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            if not account.li_at_cookie:
                continue

            # Skip if browser is busy with another job — try next cycle
            if pool.is_busy(account.id):
                logger.debug("keepalive.skipped_busy", account=account.name)
                continue

            # Bandwidth guard
            bw_used = await repo.get_daily_proxy_mb(account.id)
            bw_limit = get_settings().daily_bandwidth_limit_mb
            if bw_used >= bw_limit:
                logger.warning(
                    "keepalive.bandwidth_limit_reached",
                    account=account.name,
                    used_mb=round(bw_used, 1),
                    limit_mb=bw_limit,
                )
                continue

            try:
                context = await pool.acquire(account)
            except Exception as e:
                logger.warning("keepalive.acquire_failed", account=account.name, error=str(e))
                continue

            try:
                page = await context.new_page()
                try:
                    from linauto.linkedin.noise import BrowsingNoise
                    noise = BrowsingNoise(page)

                    # Step 1: Always start with the feed (most natural morning action)
                    await noise.scroll_feed(duration_seconds=random.uniform(15, 35))

                    # Step 2: Visit one more page — notifications, network, messaging, jobs, or learning
                    second_name, second_url = random.choices(
                        _KEEPALIVE_ACTIVITIES[1:], weights=_KEEPALIVE_STEP2_WEIGHTS, k=1
                    )[0]
                    await page.goto(second_url, wait_until="domcontentloaded", timeout=20000)

                    # Check for login redirect (session expired)
                    if any(p in page.url for p in ["/login", "/uas/login", "/signup", "/checkpoint/"]):
                        logger.warning("keepalive.session_expired_detected", account=account.name)
                        await repo.update_account(account, status="cookie_expired")
                        await repo.log_action(
                            account_id=account.id,
                            action_type=ActionType.ERROR,
                            status=ActionLogStatus.FAILED,
                            details={"reason": "session_expired_detected_by_morning_warmup"},
                        )
                    else:
                        await asyncio.sleep(random.uniform(5, 15))
                        await page.mouse.wheel(0, random.randint(200, 500))
                        await asyncio.sleep(random.uniform(2, 5))
                        await repo.add_proxy_mb(account.id, 3.0)  # feed scroll + second page
                        logger.info("keepalive.morning_done", account=account.name, second_page=second_name)

                except Exception as e:
                    logger.warning("keepalive.scroll_failed", account=account.name, error=str(e))
                finally:
                    await page.close()
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("keepalive.failed", error=str(e))
    finally:
        await session.close()


async def _start_api_server():
    """Start embedded FastAPI server if API is enabled."""
    settings = get_settings()
    if not settings.api_enabled:
        return

    try:
        import uvicorn
        from linauto.api.app import create_app
    except ImportError:
        logger.warning("api.missing_deps", msg="Install with pip install '.[api]' to enable the API")
        return

    app = create_app()
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.api_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    logger.info("api.starting", port=settings.api_port)
    asyncio.create_task(server.serve())


async def daily_summary():
    """Send end-of-day Slack summary of activity across all active accounts."""
    from datetime import date as date_type
    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        lines = [f"*Daily Summary — {date_type.today().strftime('%b %d')}*"]
        for account in accounts:
            stat = await repo.get_or_create_daily_stat(account.id)
            campaigns = await repo.list_campaigns(account_id=account.id)
            remaining = 0
            skipped_today = 0
            for campaign in campaigns:
                remaining += await repo.count_leads_by_status(
                    campaign.id, [LeadStatus.PENDING, LeadStatus.SCHEDULED]
                )
                skipped_today += await repo.count_leads_updated_today_with_status(
                    campaign.id, LeadStatus.SKIPPED
                )
            lines.append(
                f"\n*{account.name}*\n"
                f"• Connections sent: {stat.connection_requests_sent}\n"
                f"• Skipped: {skipped_today}\n"
                f"• Errors: {stat.errors}\n"
                f"• Remaining: {remaining}"
            )
        await slack_notify("\n".join(lines))
    finally:
        await session.close()


async def start_scheduler():
    """Start the APScheduler daemon. Blocks until interrupted."""
    await init_db()

    # Pre-warm browser pool for active accounts
    repo, session = await _get_repo()
    try:
        active_accounts = await repo.list_active_accounts()
    finally:
        await session.close()
    await init_pool(active_accounts)

    # Start API server if enabled
    await _start_api_server()

    settings = get_settings()
    scheduler = AsyncIOScheduler()

    # Daily planning sweep at 06:00
    scheduler.add_job(
        daily_planning_sweep,
        CronTrigger(hour=6, minute=0),
        id="daily_planner",
        name="Daily Planning Sweep",
        replace_existing=True,
    )

    # Dispatcher every 5 minutes with ±75s jitter to avoid predictable cadence.
    # misfire_grace_time=600: if the previous run was still going (processing leads),
    # APScheduler waits and runs when free without logging a spurious "missed" warning.
    scheduler.add_job(
        dispatch,
        IntervalTrigger(minutes=5, jitter=75),
        id="dispatcher",
        name="Action Dispatcher",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Acceptance checker once daily
    scheduler.add_job(
        check_acceptances,
        CronTrigger(hour=settings.acceptance_check_hour),
        id="acceptance_checker",
        name="Daily Acceptance Checker",
        replace_existing=True,
    )

    # Cooldown checker daily at midnight
    scheduler.add_job(
        check_cooldowns,
        CronTrigger(hour=0, minute=0),
        id="cooldown_checker",
        name="Cooldown Checker",
        replace_existing=True,
    )

    # Follow-up dispatcher every 30 minutes
    scheduler.add_job(
        dispatch_followups,
        IntervalTrigger(minutes=30),
        id="followup_dispatcher",
        name="Follow-up Dispatcher",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Session keep-alive — once per day, morning window (8:00 ±90 min).
    # Not a health check: LinkedIn sessions last months on their own.
    # This is purely organic-looking morning activity, not a ping.
    # Expiry is detected reactively by the dispatcher (3 consecutive errors).
    scheduler.add_job(
        keep_alive,
        CronTrigger(hour=8, minute=0, jitter=5400),  # 6:30–9:30 AM window
        id="keepalive",
        name="Morning Session Warm-Up",
        replace_existing=True,
    )

    # Daily summary Slack notification at 20:00 (±5 min jitter)
    scheduler.add_job(
        daily_summary,
        CronTrigger(hour=20, minute=0, jitter=300),
        id="daily_summary",
        name="Daily Slack Summary",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("scheduler.started", jobs=len(scheduler.get_jobs()))

    # On startup: validate all account sessions once (catches expired cookies
    # from before this deploy without waiting for the morning warm-up).
    await check_cookie_health()

    # Also run planning sweep immediately on startup
    await daily_planning_sweep()

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("scheduler.stopping")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    scheduler.shutdown(wait=True)
    await shutdown_pool()
    await close_db()
    logger.info("scheduler.stopped")

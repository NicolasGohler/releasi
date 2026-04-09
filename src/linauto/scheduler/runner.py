"""APScheduler-based scheduler daemon for unattended operation."""
from __future__ import annotations

import asyncio
import random
import signal
from datetime import date, datetime, timedelta, timezone as _dt_tz
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

# Per-account "ran today" trackers for once-daily jobs.
# Key: account_id, Value: date in the account's local timezone.
# Reset on container restart (intentional — re-run keeps things fresh).
_keepalive_ran: dict = {}      # keep_alive: only once per local day, morning window
_acceptance_checked: dict = {} # check_acceptances: only once per local day


def _acct_local_now(account):
    """Return current datetime in the account's timezone (aware), or UTC if unset/invalid."""
    tz_str = account.timezone
    if tz_str:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_str))
        except Exception:
            pass
    return datetime.now(_dt_tz.utc)

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

    Runs hourly (IntervalTrigger). Per-account logic:
    - Uses the account's local date (not server date) so a Tokyo account
      plans for the correct calendar day even when the server is in Berlin.
    - Skips if leads are already scheduled for today (future_scheduled > 0)
      and the work window has already started — the dispatcher and backfill
      logic handle the rest from that point.
    - Passes effective_start=now when called after the account's work window
      start (e.g. container restart mid-day) so the planner spreads remaining
      slots across the rest of the day rather than bursting them all at once.
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

            # Compute account's local "today" so the plan is always anchored to
            # the correct calendar date regardless of the server's timezone.
            acct_now_aware = _acct_local_now(account)
            acct_today = acct_now_aware.date()

            campaigns = await repo.get_active_campaigns(account.id)
            for campaign in campaigns:
                now = datetime.utcnow()

                # Reset any stale past-scheduled leads back to PENDING.
                stale = await repo.reset_stale_scheduled_leads(campaign.id)
                if stale:
                    logger.info(
                        "planner.stale_reset",
                        campaign=campaign.name,
                        count=stale,
                    )

                # Determine the account's work window boundaries in UTC.
                _settings = get_settings()
                _ws_utc = None
                _we_utc = None
                try:
                    from zoneinfo import ZoneInfo
                    _acct_tz = ZoneInfo(account.timezone or "UTC")
                    _ws_utc = datetime(
                        acct_today.year, acct_today.month, acct_today.day,
                        _settings.work_start_hour, 0, tzinfo=_acct_tz,
                    ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                    _we_utc = datetime(
                        acct_today.year, acct_today.month, acct_today.day,
                        _settings.work_end_hour, 0, tzinfo=_acct_tz,
                    ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                except Exception:
                    pass

                # Past the work window — stale_reset already ran above, but
                # don't generate new plans. Tomorrow's morning run handles it.
                if _we_utc is not None and now > _we_utc:
                    continue

                pending = await repo.get_pending_leads(campaign.id)
                if not pending:
                    continue

                lead_ids = [l.id for l in pending]

                sent_today = await repo.get_daily_requests_sent(account.id)
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

                # If a full daily plan was already generated today, don't re-plan.
                # NOTE: do NOT use future_scheduled > 0 here — the dispatcher's
                # backfill mechanism keeps exactly 1 lead in SCHEDULED state at
                # all times, which would make future_scheduled always > 0 and
                # permanently block the planner from generating a real plan.
                if await repo.plan_generated_today(campaign.id, acct_today):
                    continue

                # effective_start: None before the work window (standard morning plan,
                # sessions distributed across the full window); now_utc after the
                # window has started (container restart, spread remaining slots forward).
                effective_start = now if (_ws_utc is not None and now > _ws_utc) else None

                plan = generate_daily_plan(
                    account_id=account.id,
                    day=acct_today,
                    pending_lead_ids=lead_ids,
                    daily_limit=account.daily_limit,
                    timezone_str=account.timezone,
                    campaign_weekend_enabled=campaign.weekend_enabled,
                    effective_start=effective_start,
                    remaining_budget=remaining_budget,
                )

                scheduled_count = 0
                for slot in plan:
                    if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                        await repo.update_lead_schedule(
                            slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED
                        )
                        scheduled_count += 1

                # Log the plan (skip empty plans to avoid activity clutter)
                if scheduled_count > 0:
                    await repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        action_type=ActionType.DAILY_PLAN_GENERATED,
                        status=ActionLogStatus.SUCCESS,
                        details={
                            "date": acct_today.isoformat(),
                            "total_slots": len(plan),
                            "connection_requests": scheduled_count,
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
                        _anchor = datetime.utcnow() + timedelta(seconds=_inter_gap)
                        # Clamp backfill anchor to account's work window so leads are never
                        # scheduled overnight. If anchor falls before today's window, push
                        # to window start. If after today's window, push to tomorrow's start.
                        if account.timezone:
                            try:
                                from zoneinfo import ZoneInfo
                                _bkf_tz = ZoneInfo(account.timezone)
                                _bkf_settings = get_settings()
                                _bkf_day = _anchor.date()
                                _ws = datetime(
                                    _bkf_day.year, _bkf_day.month, _bkf_day.day,
                                    _bkf_settings.work_start_hour, 0, tzinfo=_bkf_tz,
                                ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                                _we = datetime(
                                    _bkf_day.year, _bkf_day.month, _bkf_day.day,
                                    _bkf_settings.work_end_hour, 0, tzinfo=_bkf_tz,
                                ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                                if _anchor < _ws:
                                    _anchor = _ws
                                elif _anchor >= _we:
                                    _tmrw = _bkf_day + timedelta(days=1)
                                    _anchor = datetime(
                                        _tmrw.year, _tmrw.month, _tmrw.day,
                                        _bkf_settings.work_start_hour, 0, tzinfo=_bkf_tz,
                                    ).astimezone(_dt_tz.utc).replace(tzinfo=None)
                            except Exception:
                                pass  # keep original anchor if timezone logic fails
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
    Check for accepted connection requests.

    Strategy (zero individual profile visits):
      1. Connections page (sorted by recently added): scroll until we hit cards
         older than CUTOFF_HOURS. Every slug found → mark lead CONNECTED.
      2. Invitation manager diff: find CONNECTION_REQUESTED leads that have
         disappeared from the pending list AND were not found in step 1 → mark
         WITHDRAWN (declined or expired). No profile visits needed.
      3. Auto-withdraw: if withdraw_threshold set, withdraw oldest invitations
         while already on the invitation manager page.

    Runs hourly (IntervalTrigger). Per-account: fires once per local day at
    acceptance_check_hour (default 10 AM in account timezone).
    """
    from linauto.linkedin.actions import LinkedInActions

    # Cutoff: how far back to scan the connections page. 30h gives a comfortable
    # overlap with a daily run — a connection accepted right before yesterday's run
    # will still appear within 30h of today's run.
    CUTOFF_HOURS = 30.0

    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            acct_now = _acct_local_now(account)
            acct_today = acct_now.date()
            check_hour = get_settings().acceptance_check_hour
            if acct_now.hour != check_hour:
                continue
            if _acceptance_checked.get(account.id) == acct_today:
                continue

            # Include paused campaigns — acceptances while paused still need recording
            campaigns = await repo.get_operational_campaigns(account.id)
            campaign_map = {c.id: c for c in campaigns}
            requested_leads = []
            for campaign in campaigns:
                leads = await repo.get_leads_by_status(campaign.id, LeadStatus.CONNECTION_REQUESTED)
                requested_leads.extend(leads)

            needs_browser = bool(requested_leads) or bool(account.withdraw_threshold)
            if not needs_browser:
                _acceptance_checked[account.id] = acct_today
                continue

            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.error("acceptance.pool_acquire_failed", account=account.name, error=str(e))
                continue

            try:
                # ── Step 1: connections page → find recent acceptances ────────
                conn_page = await pool_context.new_page()
                conn_actions = LinkedInActions(conn_page)
                conn_result = await conn_actions.get_recent_connections(
                    cutoff_hours=CUTOFF_HOURS
                )
                await conn_page.close()

                if not conn_result.session_valid:
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
                    continue

                # Build set of recently-accepted slugs from the connections page
                recent_slugs = set(conn_result.slugs)  # e.g. {"/in/john-doe", ...}
                newly_connected = []  # (lead, campaign) pairs

                if requested_leads and conn_result.success:
                    for lead in requested_leads:
                        slug = _normalize_li_url(lead.linkedin_url)
                        if not slug or slug not in recent_slugs:
                            continue
                        campaign = campaign_map.get(lead.campaign_id)
                        if campaign is None:
                            continue
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
                            details={"accepted": True, "method": "connections_page"},
                        )
                        await repo.increment_daily_stat(account.id, "connections_accepted")
                        newly_connected.append((lead, campaign))
                        logger.info("acceptance.connected", url=lead.linkedin_url)

                pool.confirm_session(account.id)
                logger.info(
                    "acceptance.connections_page_done",
                    account=account.name,
                    recent_slugs=len(recent_slugs),
                    newly_connected=len(newly_connected),
                    hit_cutoff=conn_result.hit_cutoff,
                )

                # ── Step 2: schedule follow-ups for newly connected leads ────
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

                # Mark as checked for today so subsequent hourly runs skip this account
                _acceptance_checked[account.id] = acct_today

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

    Runs hourly (IntervalTrigger). Per-account logic: only fires once per local day
    during the account's 7–10 AM window. The exact trigger time varies by ≤1 hour
    depending on when the hourly job happens to land in that window.

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

            # Only run during the account's 7–10 AM morning window.
            acct_now = _acct_local_now(account)
            if not (7 <= acct_now.hour < 10):
                continue

            # Only once per local day — prevents running again if the job fires
            # multiple times within the same window (e.g. near the boundary).
            acct_today = acct_now.date()
            if _keepalive_ran.get(account.id) == acct_today:
                continue

            # Skip if browser is busy with another job — try next cycle
            if pool.is_busy(account.id):
                logger.debug("keepalive.skipped_busy", account=account.name)
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
                        _keepalive_ran[account.id] = acct_today
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
    # Scheduler runs in UTC. Jobs that need per-account timezone awareness (planner,
    # keepalive, acceptance checker) fire hourly and decide internally whether to act
    # for each account based on that account's local time. This scales to any timezone
    # without hardcoded offsets — a Tokyo account and a Montreal account are both
    # handled correctly by the same job.
    scheduler = AsyncIOScheduler(timezone='UTC')

    # Daily planning sweep — fires hourly; per-account logic handles local date/time.
    scheduler.add_job(
        daily_planning_sweep,
        IntervalTrigger(hours=1),
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

    # Acceptance checker — fires hourly; per-account logic checks if it's
    # acceptance_check_hour in the account's local timezone (once per local day).
    scheduler.add_job(
        check_acceptances,
        IntervalTrigger(hours=1),
        id="acceptance_checker",
        name="Daily Acceptance Checker",
        replace_existing=True,
    )

    # Cooldown checker daily at midnight EDT (04:00 UTC)
    scheduler.add_job(
        check_cooldowns,
        CronTrigger(hour=4, minute=0),
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

    # Session keep-alive — fires hourly; per-account logic checks if it's within
    # the 7–10 AM window in the account's local timezone (once per local day).
    scheduler.add_job(
        keep_alive,
        IntervalTrigger(hours=1),
        id="keepalive",
        name="Morning Session Warm-Up",
        replace_existing=True,
    )

    # Daily summary Slack notification at 21:00 EDT (01:00 UTC, ±5 min jitter)
    scheduler.add_job(
        daily_summary,
        CronTrigger(hour=1, minute=0, jitter=300),
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

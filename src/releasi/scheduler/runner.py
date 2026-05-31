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

from releasi.config import get_settings
from releasi.db.engine import init_db, get_session_factory, close_db
from releasi.db.models import (
    Account, AccountStatus, Campaign, CampaignStatus,
    Lead, LeadStatus, ActionType, ActionLogStatus,
)
from releasi.db.repository import Repository
from releasi.linkedin.pool import get_browser_pool, init_pool, shutdown_pool
from releasi.scheduler.planner import generate_daily_plan, SlotType
from releasi.safety.cooldown import is_cooldown_expired, calculate_cooldown_resume, push_cooldown_one_day
from releasi.safety import limits as rate_limits
from releasi.campaign.state_machine import validate_transition
from releasi.notifications.slack import notify as slack_notify
from releasi.safety.dispatch_decisions import (
    classify_connection_result,
    classify_followup_result,
    DispatchIntent,
    AccountAction,
    LeadAction,
)

logger = structlog.get_logger()


async def _apply_connection_intent(
    intent: DispatchIntent,
    repo: "Repository",
    account,
    campaign,
) -> None:
    """Execute DB writes, Slack pings, and logging encoded in a DispatchIntent
    for connection-request dispatchers.  Does NOT set stop_account or break."""
    if intent.log_event:
        if "network" in intent.log_event or "proxy" in intent.log_event:
            logger.warning(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                consecutive=intent.consecutive_network,
            )
        else:
            logger.error(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                consecutive=max(intent.consecutive_session, intent.consecutive_network),
            )
        await repo.log_action(
            account_id=account.id,
            campaign_id=campaign.id,
            action_type=ActionType.ERROR,
            status=ActionLogStatus.FAILED,
            details={k: v for k, v in {
                "reason": intent.log_reason,
                "count": max(intent.consecutive_session, intent.consecutive_network) or None,
            }.items() if v is not None},
        )
    if intent.account_action == AccountAction.MARK_COOKIE_EXPIRED:
        await repo.update_account(account, status="cookie_expired")
    if intent.slack_message:
        await slack_notify(intent.slack_message)
    if intent.reset_scheduled_to_pending:
        await repo.bulk_update_lead_status(
            campaign.id,
            from_status=LeadStatus.SCHEDULED,
            to_status=LeadStatus.PENDING,
        )


async def _apply_followup_intent(
    intent: DispatchIntent,
    repo: "Repository",
    account,
    campaign,
    lead,
) -> None:
    """Execute DB writes, Slack pings, and logging for followup dispatchers."""
    if intent.lead_action == LeadAction.MARK_SENT:
        validate_transition(lead.status, LeadStatus.FOLLOWUP_SENT)
        await repo.update_lead(
            lead,
            campaign_id_override=campaign.id,
            status=LeadStatus.FOLLOWUP_SENT,
            followup_sent_at=datetime.utcnow(),
        )
    elif intent.lead_action == LeadAction.MARK_ERROR:
        validate_transition(lead.status, LeadStatus.ERROR)
        await repo.update_lead(
            lead,
            campaign_id_override=campaign.id,
            status=LeadStatus.ERROR,
            retry_count=1,
        )
    if intent.account_action == AccountAction.MARK_COOKIE_EXPIRED:
        await repo.update_account(account, status="cookie_expired")
    if intent.slack_message:
        await slack_notify(intent.slack_message)
    if intent.log_event:
        if "network" in intent.log_event or "proxy" in intent.log_event:
            logger.warning(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                url=lead.linkedin_url,
                consecutive=intent.consecutive_network,
            )
        else:
            log_fn = logger.error if (
                "expired" in intent.log_event
                or "consecutive" in intent.log_event
                or "fatal" in intent.log_event
            ) else logger.warning
            log_fn(
                intent.log_event,
                account=account.name,
                campaign=campaign.name,
                url=lead.linkedin_url,
                consecutive=max(intent.consecutive_session, intent.consecutive_network),
            )
        await repo.log_action(
            account_id=account.id,
            campaign_id=campaign.id,
            lead_id=lead.id,
            action_type=ActionType.ERROR,
            status=ActionLogStatus.FAILED,
            details={k: v for k, v in {
                "reason": intent.log_reason,
                "count": max(intent.consecutive_session, intent.consecutive_network) or None,
            }.items() if v is not None},
        )

# Per-account session tracking: when the last dispatch session ended (UTC naive).
# Prevents rapid-fire dispatching across 5-min cycles — enforces inter-session gap.
_last_session_end: dict = {}

# Per-account "ran today" trackers for once-daily jobs.
# Key: account_id, Value: date in the account's local timezone.
# Reset on container restart (intentional — re-run keeps things fresh).
_keepalive_ran: dict = {}      # keep_alive: only once per local day, morning window
_acceptance_checked: dict = {} # check_acceptances: only once per local day

# Continuous dispatch mode — per-account daily state.
# Resets automatically when the account's local date changes.
# {account_id: {state_date, work_start, work_end, target_gap_sec, next_gap_sec}}
_continuous_state: dict = {}


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


def _continuous_day_state(account, now_utc: datetime) -> dict:
    """Return (and lazily initialise) today's continuous dispatch state for account.

    Work-window boundaries and target gap are generated once per local calendar
    day with random variation, then held in memory for the rest of that day.
    On container restart the dict is empty — state regenerates on first call,
    which is safe: next_gap_sec starts at 0 so the first post-restart session
    fires immediately (gap elapsed = infinite).
    """
    from zoneinfo import ZoneInfo

    acct_now = _acct_local_now(account)
    acct_today = acct_now.date()
    existing = _continuous_state.get(account.id)
    if existing and existing["state_date"] == acct_today:
        return existing

    settings = get_settings()
    rng = random.Random()  # unseeded — fresh randomness each day

    # Work-window variation (same ranges as the planner, ±30-45 min)
    start_offset = rng.randint(settings.work_start_variation[0], settings.work_start_variation[1])
    end_offset = rng.randint(settings.work_end_variation[0], settings.work_end_variation[1])

    try:
        tz = ZoneInfo(account.timezone or "UTC")
        base_start = datetime(
            acct_today.year, acct_today.month, acct_today.day,
            settings.work_start_hour, 0, tzinfo=tz,
        ).astimezone(_dt_tz.utc).replace(tzinfo=None)
        base_end = datetime(
            acct_today.year, acct_today.month, acct_today.day,
            settings.work_end_hour, 0, tzinfo=tz,
        ).astimezone(_dt_tz.utc).replace(tzinfo=None)
    except Exception:
        base_start = datetime.combine(acct_today, datetime.min.time().replace(hour=settings.work_start_hour))
        base_end = datetime.combine(acct_today, datetime.min.time().replace(hour=settings.work_end_hour))

    work_start = base_start + timedelta(minutes=start_offset)
    work_end = base_end + timedelta(minutes=end_offset)

    # Dynamic gap: spread daily_limit evenly across the work window.
    # avg_batch = midpoint of continuous_batch_size range.
    avg_batch = (settings.continuous_batch_size[0] + settings.continuous_batch_size[1]) / 2
    work_window_min = max(1.0, (work_end - work_start).total_seconds() / 60)
    sessions_needed = max(1.0, account.daily_limit / avg_batch)
    target_gap_sec = (work_window_min / sessions_needed) * 60  # seconds

    state = {
        "state_date": acct_today,
        "work_start": work_start,
        "work_end": work_end,
        "target_gap_sec": target_gap_sec,
        "next_gap_sec": 0.0,  # first session fires as soon as work window opens
    }
    _continuous_state[account.id] = state

    try:
        from zoneinfo import ZoneInfo as _ZI
        _tz = _ZI(account.timezone or "UTC")
        _ws_lbl = work_start.replace(tzinfo=_dt_tz.utc).astimezone(_tz).strftime("%H:%M %Z")
        _we_lbl = work_end.replace(tzinfo=_dt_tz.utc).astimezone(_tz).strftime("%H:%M %Z")
    except Exception:
        _ws_lbl = work_start.strftime("%H:%M UTC")
        _we_lbl = work_end.strftime("%H:%M UTC")

    logger.info(
        "dispatch.continuous_day_init",
        account=account.name,
        work_start=_ws_lbl,
        work_end=_we_lbl,
        target_gap_min=round(target_gap_sec / 60, 1),
        daily_limit=account.daily_limit,
    )
    return state


def _next_continuous_gap(target_gap_sec: float) -> float:
    """Randomise ±15 % around the target gap. Returns seconds."""
    return random.uniform(target_gap_sec * 0.85, target_gap_sec * 1.15)


async def _dispatch_continuous(
    account,
    repo: "Repository",
    pool_context,
    remaining: int,
) -> int:
    """Run one continuous-mode dispatch session. Returns number of successful sends."""
    from releasi.campaign.executor import CampaignExecutor
    from releasi.linkedin.noise import BrowsingNoise

    settings = get_settings()
    batch_size = random.randint(settings.continuous_batch_size[0], settings.continuous_batch_size[1])
    batch_size = min(batch_size, remaining)
    if batch_size <= 0:
        return 0

    campaigns = await repo.get_active_campaigns(account.id)
    total_sent = 0
    remaining_batch = batch_size
    stop_account = False

    for campaign in campaigns:
        if stop_account or remaining_batch <= 0:
            break

        # Phase 2 read cutover: use CampaignLeadAssignment as source of truth.
        pending = list(await repo.get_pending_leads_via_assignments(campaign.id, limit=remaining_batch * 4))
        if not pending:
            continue

        executor = CampaignExecutor(repo, browser_context=pool_context)
        successful_sends = 0
        consecutive_session_errors = 0
        consecutive_network_errors = 0
        max_attempts = remaining_batch * 4
        lead_queue = list(pending)
        attempts = 0

        while lead_queue and successful_sends < remaining_batch and attempts < max_attempts:
            attempts += 1
            lead = lead_queue.pop(0)
            result = await executor.execute_single_lead(account, campaign, lead)

            if result.get("soft_limit_reached"):
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
                logger.warning("dispatch.continuous_soft_limit", account=account.name, resume=str(resume))
                stop_account = True
                break

            if result.get("limit_reached"):
                resume = calculate_cooldown_resume(account.timezone)
                await repo.update_account(account, paused_until=resume)
                await repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    action_type=ActionType.COOLDOWN_STARTED,
                    status=ActionLogStatus.SUCCESS,
                    details={"paused_until": str(resume)},
                )
                logger.warning("dispatch.continuous_cooldown_started", account=account.name, resume=str(resume))
                stop_account = True
                break

            intent = classify_connection_result(
                result,
                consecutive_session_errors,
                consecutive_network_errors,
                account.name,
                campaign.name,
            )
            consecutive_session_errors = intent.consecutive_session
            consecutive_network_errors = intent.consecutive_network
            await _apply_connection_intent(intent, repo, account, campaign)
            if intent.stop_account:
                stop_account = True
                break

            if result.get("success"):
                successful_sends += 1
                total_sent += 1
                await repo.add_proxy_mb(account.id, 2.0)
            # skipped and non-3-strike failures: no backfill in continuous mode

        remaining_batch -= successful_sends

        if successful_sends > 0:
            sent_today = await repo.get_daily_requests_sent(account.id)
            logger.info(
                "dispatch.continuous_cycle_complete",
                campaign=campaign.name,
                cycle_sent=successful_sends,
                day_sent=sent_today,
                daily_limit=account.daily_limit,
                batch_size=batch_size,
            )

    # Post-session noise: 1-2 actions in the same browser context
    if total_sent > 0 and not stop_account:
        noise_page = None
        try:
            noise_page = await pool_context.new_page()
            noise = BrowsingNoise(noise_page)
            noise_count = random.randint(1, 2)
            for _ in range(noise_count):
                action = random.choice(["profile", "feed"])
                if action == "profile":
                    await noise.view_random_profile()
                else:
                    await noise.like_feed_post()
                await asyncio.sleep(random.uniform(3, 8))
        except Exception as e:
            logger.debug("dispatch.continuous_noise_failed", error=str(e))
        finally:
            if noise_page:
                try:
                    await noise_page.close()
                except Exception:
                    pass

    return total_sent


async def _dispatch_planned(
    account,
    repo: "Repository",
    pool_context,
    pool,
    remaining: int,
    now: datetime,
    sent_today_count: int,
) -> None:
    """Legacy planned-mode dispatch: execute SCHEDULED leads past their slot time.

    Only called when account.dispatch_mode == 'planned'. Continuous mode (the
    default for all accounts) uses _dispatch_continuous() instead.

    Behaviour:
    - Fetches SCHEDULED leads with scheduled_at <= now for each active campaign.
    - Caps each cycle to actions_per_session[1] to prevent bursts on restart.
    - Backfills skipped/errored leads into future slots (inter_session_delay ahead).
    - Clamps backfill anchor to the account's work window (never schedules overnight).
    """
    from releasi.campaign.executor import CampaignExecutor

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

        executor = CampaignExecutor(repo, browser_context=pool_context)

        successful_sends = 0
        attempts = 0
        consecutive_session_errors = 0   # non-network failures → cookie suspect
        consecutive_network_errors = 0   # timeouts/proxy → network suspect
        max_attempts = target * 4  # Safety cap: allow skips+errors without infinite loop
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

            intent = classify_connection_result(
                result,
                consecutive_session_errors,
                consecutive_network_errors,
                account.name,
                campaign.name,
            )
            consecutive_session_errors = intent.consecutive_session
            consecutive_network_errors = intent.consecutive_network
            await _apply_connection_intent(intent, repo, account, campaign)
            if intent.stop_account:
                stop_account = True
                break

            if result.get("success"):
                successful_sends += 1
                await repo.add_proxy_mb(account.id, 2.0)  # ~2 MB per connection request
            elif result.get("skipped"):
                backfill_count += 1
            elif not result.get("network_error") and not result.get("session_expired") and not result.get("fatal"):
                # Generic failure that didn't hit 3-strike stop — schedule a backfill slot
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
                    campaign_id_override=campaign.id,
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
            # Continuous-mode accounts (default) manage their own scheduling — no planning needed.
            # Only accounts with an explicit 'planned' dispatch_mode use the pre-scheduled slot system.
            if account.dispatch_mode != "planned":
                continue

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

            # Continuous is the default. Only 'planned' opts into the legacy pre-scheduled slot system.
            _is_continuous = account.dispatch_mode != "planned"

            if _is_continuous:
                # ── Continuous mode: dynamic gap + work-window guard ──────────
                _state = _continuous_day_state(account, now)
                if now < _state["work_start"] or now >= _state["work_end"]:
                    continue  # outside today's work window
                _last_end = _last_session_end.get(account.id)
                if _last_end is not None:
                    _elapsed = (now - _last_end).total_seconds()
                    _gap = _state["next_gap_sec"]
                    if _elapsed < _gap:
                        logger.debug(
                            "dispatch.continuous_gap_enforced",
                            account=account.name,
                            elapsed_min=round(_elapsed / 60, 1),
                            gap_min=round(_gap / 60, 1),
                        )
                        continue
            else:
                # ── Planned mode: fixed minimum inter-session gap ─────────────
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

            # Check daily limits (both modes)
            sent_today_count = await repo.get_daily_requests_sent(account.id)
            can_send, remaining = await rate_limits.can_send_today(
                account.id,
                account.daily_limit,
                sent_today_count,
            )

            if not can_send:
                logger.info("dispatch.daily_limit_reached", account=account.name)
                continue

            # Quick DB pre-check: skip browser entirely if no work to do.
            campaigns_preview = await repo.get_active_campaigns(account.id)
            any_due = False
            if _is_continuous:
                # Continuous: look for any PENDING leads.
                # Phase 2 read cutover: use CampaignLeadAssignment as source of truth.
                for _c in campaigns_preview:
                    _pending_check = await repo.get_pending_leads_via_assignments(_c.id, limit=1)
                    if _pending_check:
                        any_due = True
                        break
            else:
                # Planned: look for SCHEDULED leads past their slot time
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
                from releasi.linkedin.pool import _VALIDATION_COOLDOWN as _VC
                _seconds_since_check = pool.time_since_validated(account.id)
                if _seconds_since_check >= _VC:
                    _pre_page = await pool_context.new_page()
                    try:
                        from releasi.linkedin.navigator import LinkedInNavigator
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

                # ── Route to continuous or planned dispatch ───────────────
                if _is_continuous:
                    sent = await _dispatch_continuous(account, repo, pool_context, remaining)
                    if sent > 0:
                        pool.confirm_session(account.id)
                        _last_session_end[account.id] = datetime.utcnow()
                        # Compute gap to next session now, so it's stable for the
                        # entire waiting period rather than re-randomised each poll.
                        _state["next_gap_sec"] = _next_continuous_gap(_state["target_gap_sec"])
                else:
                    # ── Legacy planned-mode dispatch (explicit dispatch_mode='planned' only) ──
                    await _dispatch_planned(account, repo, pool_context, pool, remaining, now, sent_today_count)
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
    from releasi.linkedin.actions import LinkedInActions

    # Cutoff: how far back to scan the connections page.  72h (3-day window) means
    # we catch connections accepted even if the checker missed yesterday's run, and
    # covers accounts that only connected in the last 1-2 days.
    CUTOFF_HOURS = 72.0

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
            # Phase 2 read cutover: filter via CampaignLeadAssignment.
            # Store (lead, campaign) tuples so the campaign is always available
            # even after Phase 3a collapse when lead.campaign_id is NULL.
            requested_lead_pairs: list[tuple] = []
            for campaign in campaigns:
                leads = await repo.get_leads_by_status_via_assignments(
                    campaign.id, LeadStatus.CONNECTION_REQUESTED
                )
                for lead in leads:
                    requested_lead_pairs.append((lead, campaign))

            needs_browser = bool(requested_lead_pairs) or bool(account.withdraw_threshold)
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

                if requested_lead_pairs and conn_result.success:
                    for lead, campaign in requested_lead_pairs:
                        slug = _normalize_li_url(lead.linkedin_url)
                        if not slug or slug not in recent_slugs:
                            continue
                        validate_transition(lead.status, LeadStatus.CONNECTED)
                        await repo.update_lead(
                            lead,
                            campaign_id_override=campaign.id,
                            status=LeadStatus.CONNECTED,
                            connection_accepted_at=datetime.utcnow(),
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

                # ── One summary log entry for this run (replaces per-lead entries) ──
                await repo.log_action(
                    account_id=account.id,
                    action_type=ActionType.ACCEPTANCE_CHECK_SUMMARY,
                    status=ActionLogStatus.SUCCESS,
                    details={
                        "accepted": len(newly_connected),
                        "scanned": len(recent_slugs),
                        "hit_cutoff": conn_result.hit_cutoff,
                        "cutoff_hours": CUTOFF_HOURS,
                    },
                )

                # ── Step 1b: harvest Contact Info (email + phone) for newly connected leads ──
                # One page load per accepted lead using the already-acquired browser context.
                # Fail-open: any error is logged but does NOT abort the acceptance run.
                if newly_connected:
                    contact_page = await pool_context.new_page()
                    contact_actions = LinkedInActions(contact_page)
                    for lead, _campaign in newly_connected:
                        try:
                            slug = lead.linkedin_url.split("/in/")[-1].strip("/")
                            info = await contact_actions.get_contact_info(slug)
                            if info.get("email") or info.get("phone"):
                                await repo.update_lead(
                                    lead,
                                    campaign_id_override=_campaign.id,
                                    email=info.get("email"),
                                    phone=info.get("phone"),
                                )
                                logger.info(
                                    "acceptance.contact_info_saved",
                                    url=lead.linkedin_url,
                                    has_email=bool(info.get("email")),
                                    has_phone=bool(info.get("phone")),
                                )
                            await asyncio.sleep(random.uniform(2.0, 3.5))
                        except Exception as e:
                            logger.warning(
                                "acceptance.contact_info_failed",
                                url=lead.linkedin_url,
                                error=str(e),
                            )
                    await contact_page.close()

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
                            campaign_id_override=campaign.id,
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
                            from releasi.campaign.executor import CampaignExecutor
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
                                    campaign_id_override=campaign.id,
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

            except Exception as e:
                # Classify per-account exceptions so a redirect loop on the
                # connections page surfaces as cookie_expired instead of
                # silently logging and moving on. Other accounts in the
                # loop continue uninterrupted.
                err_str = str(e)
                logger.error(
                    "acceptance.account_failed",
                    account=account.name,
                    error=err_str,
                )
                from releasi.safety.error_signals import is_session_expired_signal
                if is_session_expired_signal(err_str):
                    logger.error("acceptance.session_expired_detected", account=account.name)
                    try:
                        await repo.update_account(account, status="cookie_expired")
                        await slack_notify(
                            f":warning: *Cookie expired* — account *{account.name}* "
                            f"(detected by acceptance checker)."
                        )
                    except Exception as inner:
                        logger.warning("acceptance.cookie_expired_update_failed", error=str(inner))
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

        settings = get_settings()

        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            campaigns = await repo.get_active_campaigns(account.id)

            # ── Daily cap pre-check ────────────────────────────────────────────
            # Count distinct leads that already had a followup sequence started today.
            # Multi-message sequences count as one, so the cap is in sequences not messages.
            sent_today = await repo.get_followup_sequences_started_today(account.id)
            daily_cap = settings.followup_daily_cap
            if sent_today >= daily_cap:
                logger.info(
                    "followup.daily_cap_reached",
                    account=account.name,
                    sent_today=sent_today,
                    cap=daily_cap,
                )
                continue

            # Check if any campaign has due follow-ups before acquiring pool
            # Phase 2 read cutover: use CampaignLeadAssignment as source of truth.
            has_due = False
            for campaign in campaigns:
                if campaign.followup_enabled:
                    due = await repo.get_followup_due_leads_via_assignments(campaign.id, before=now)
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
                from releasi.campaign.executor import CampaignExecutor
                executor = CampaignExecutor(repo, browser_context=pool_context)

                # Track sends in this dispatch cycle so we respect the cap even
                # if DailyStat writes haven't been flushed yet.
                sent_this_cycle = 0
                remaining_cap = daily_cap - sent_today

                # Mirror the connection-request dispatcher's session-health
                # heuristics. Before this was added, 5+ followups would fail
                # in a row with ERR_TOO_MANY_REDIRECTS (login redirect loop =
                # expired cookie) and each lead was just marked ERROR without
                # ever flagging the account as cookie_expired.
                consecutive_session_errors = 0
                consecutive_network_errors = 0
                stop_account = False

                for campaign in campaigns:
                    if stop_account:
                        break
                    if not campaign.followup_enabled:
                        continue
                    if remaining_cap <= 0:
                        logger.info(
                            "followup.daily_cap_reached_mid_cycle",
                            account=account.name,
                            sent_today=sent_today,
                            sent_this_cycle=sent_this_cycle,
                            cap=daily_cap,
                        )
                        break

                    due_leads = await repo.get_followup_due_leads_via_assignments(campaign.id, before=now)
                    if not due_leads:
                        continue

                    for lead in due_leads:
                        if remaining_cap <= 0:
                            break

                        # ── Guard: re-fetch lead status before acting ──────────
                        # Prevents acting on stale data from the batch query above
                        # (race condition, concurrent job, or a prior cycle that
                        # updated the status after the batch was fetched).
                        fresh = await repo.get_lead_by_id(lead.id)
                        if fresh is None:
                            logger.warning("followup.lead_vanished", url=lead.linkedin_url)
                            continue
                        if fresh.status != LeadStatus.FOLLOWUP_SCHEDULED:
                            logger.warning(
                                "followup.stale_status_skipped",
                                url=lead.linkedin_url,
                                expected="FOLLOWUP_SCHEDULED",
                                actual=fresh.status.value,
                            )
                            continue
                        if fresh.followup_sent_at is not None:
                            logger.warning(
                                "followup.already_sent_skipped",
                                url=lead.linkedin_url,
                                followup_sent_at=str(fresh.followup_sent_at),
                            )
                            continue
                        # Use the freshly-fetched object for all subsequent ops
                        lead = fresh

                        logger.info(
                            "followup.starting_sequence",
                            url=lead.linkedin_url,
                            campaign=campaign.name,
                        )
                        fu_result = await executor.execute_followup_sequence(
                            account, campaign, lead
                        )
                        await repo.add_proxy_mb(account.id, 1.0)  # messaging page

                        intent = classify_followup_result(
                            fu_result,
                            consecutive_session_errors,
                            consecutive_network_errors,
                            account.name,
                            campaign.name,
                        )
                        consecutive_session_errors = intent.consecutive_session
                        consecutive_network_errors = intent.consecutive_network
                        await _apply_followup_intent(intent, repo, account, campaign, lead)
                        if intent.stop_account:
                            stop_account = True
                            break

                        if intent.lead_action == LeadAction.MARK_SENT:
                            if fu_result.get("success"):
                                sent_this_cycle += 1
                                remaining_cap -= 1
                                logger.info(
                                    "followup.sequence_done",
                                    url=lead.linkedin_url,
                                    messages_sent=fu_result["messages_sent"],
                                )
                            else:
                                logger.info(
                                    "followup.skipped_marked_sent",
                                    url=lead.linkedin_url,
                                )
            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("followup_dispatch.failed", error=str(e))
    finally:
        await session.close()


async def withdraw_invitations_sweep():
    """
    Interval-based auto-withdraw. Runs hourly.

    Per-account: fires when withdraw_threshold is set and auto_withdraw_interval_days
    have elapsed since auto_withdraw_last_run (or it has never run).

    Logic:
      1. Get live pending invitation count from LinkedIn.
      2. Cache it on the account row.
      3. If count > threshold: withdraw oldest invitations until count reaches a
         random target within [threshold*0.95, threshold], hard-capped at 200/session.
      4. Stamp auto_withdraw_last_run regardless of whether withdrawal was needed,
         so the interval resets from today.
    """
    import re as _re_wd
    from releasi.linkedin.actions import LinkedInActions

    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        now = datetime.utcnow()

        for account in accounts:
            # Skip if auto-withdraw is not configured
            if account.withdraw_threshold is None:
                continue

            # Skip paused accounts
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            # Check interval: skip if not enough days have elapsed
            interval_days = account.auto_withdraw_interval_days or 30
            if account.auto_withdraw_last_run is not None:
                days_elapsed = (now - account.auto_withdraw_last_run).total_seconds() / 86400
                if days_elapsed < interval_days:
                    continue

            threshold = account.withdraw_threshold
            pool = get_browser_pool()
            pool_context = None
            try:
                pool_context = await pool.acquire(account)
            except Exception as e:
                logger.warning("withdraw_sweep.pool_acquire_failed", account=account.name, error=str(e))
                continue

            try:
                # Get live invitation count
                count_page = await pool_context.new_page()
                try:
                    actions = LinkedInActions(count_page)
                    live_count = await actions.get_pending_invitation_count()
                finally:
                    await count_page.close()

                if live_count < 0:
                    logger.warning("withdraw_sweep.count_failed", account=account.name)
                    continue

                # Cache count and stamp last_run regardless of whether we withdraw
                await repo.update_account(account, pending_invitations_count=live_count, auto_withdraw_last_run=now)

                if live_count <= threshold:
                    logger.info(
                        "withdraw_sweep.below_threshold",
                        account=account.name,
                        count=live_count,
                        threshold=threshold,
                    )
                    continue

                # Target = random in [threshold*0.95, threshold] (organic, avoids exact number)
                target = random.randint(int(threshold * 0.95), threshold)
                to_withdraw = min(live_count - target, 200)

                if to_withdraw <= 0:
                    continue

                logger.info(
                    "withdraw_sweep.starting",
                    account=account.name,
                    live_count=live_count,
                    threshold=threshold,
                    target=target,
                    to_withdraw=to_withdraw,
                )

                wd_page = await pool_context.new_page()
                try:
                    wd_actions = LinkedInActions(wd_page)
                    withdrawn_urls = await wd_actions.withdraw_invitations(to_withdraw, order="oldest")
                finally:
                    await wd_page.close()

                # Sync withdrawn leads to DB
                db_updated = 0
                for url in withdrawn_urls:
                    m = _re_wd.search(r'/in/([^/?#\s]+)', url)
                    if not m:
                        continue
                    slug = m.group(1).rstrip('/')
                    updated = await repo.mark_lead_withdrawn_by_slug(slug)
                    if updated:
                        db_updated += 1

                # Update cached count with post-withdrawal estimate
                new_count = max(0, live_count - len(withdrawn_urls))
                await repo.update_account(account, pending_invitations_count=new_count)

                logger.info(
                    "withdraw_sweep.done",
                    account=account.name,
                    withdrawn=len(withdrawn_urls),
                    db_updated=db_updated,
                    new_count=new_count,
                )

            finally:
                await pool.release_idle(account.id)

    except Exception as e:
        logger.error("withdraw_sweep.failed", error=str(e))
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
    from releasi.db.models import AccountStatus

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
                from releasi.linkedin.browser import _build_proxy_url
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
                    from releasi.linkedin.noise import BrowsingNoise
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
        from releasi.api.app import create_app
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

    # Auto-withdraw sweep — fires hourly; per-account logic checks interval_days elapsed.
    # Only runs for accounts with withdraw_threshold set.
    scheduler.add_job(
        withdraw_invitations_sweep,
        IntervalTrigger(hours=1),
        id="withdraw_sweep",
        name="Auto-Withdraw Sweep",
        replace_existing=True,
    )

    # Daily summary Slack notification disabled
    # scheduler.add_job(
    #     daily_summary,
    #     CronTrigger(hour=1, minute=0, jitter=300),
    #     id="daily_summary",
    #     name="Daily Slack Summary",
    #     replace_existing=True,
    # )

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

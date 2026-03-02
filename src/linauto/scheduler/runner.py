"""APScheduler-based scheduler daemon for unattended operation."""
from __future__ import annotations

import asyncio
import signal
from datetime import date, datetime
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
from linauto.scheduler.planner import generate_daily_plan, SlotType
from linauto.safety.cooldown import is_cooldown_expired, calculate_cooldown_resume, push_cooldown_one_day
from linauto.safety import limits as rate_limits

logger = structlog.get_logger()


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
                pending = await repo.get_pending_leads(campaign.id)
                if not pending:
                    continue

                lead_ids = [l.id for l in pending]

                plan = generate_daily_plan(
                    account_id=account.id,
                    day=date.today(),
                    pending_lead_ids=lead_ids,
                    daily_limit=account.daily_limit,
                    timezone_str=account.timezone,
                    campaign_weekend_enabled=campaign.weekend_enabled,
                )

                # Assign scheduled_at to leads for connection_request slots
                for slot in plan:
                    if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                        await repo.update_lead_schedule(
                            slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED
                        )

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
        now = datetime.utcnow()
        accounts = await repo.list_active_accounts()

        for account in accounts:
            # Skip paused accounts
            if account.paused_until and not is_cooldown_expired(account.paused_until):
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

            campaigns = await repo.get_active_campaigns(account.id)
            for campaign in campaigns:
                due_leads = list(await repo.get_scheduled_leads(campaign.id, before=now))
                if not due_leads:
                    continue

                # Calculate target for this dispatch cycle
                target = len(due_leads)
                if remaining is not None:
                    target = min(target, remaining)

                from linauto.campaign.executor import CampaignExecutor
                executor = CampaignExecutor(repo)

                successful_sends = 0
                attempts = 0
                consecutive_errors = 0
                max_attempts = target * 3  # Safety cap: don't try more than 3x target
                lead_queue = list(due_leads[:target])
                stop_account = False

                while lead_queue and successful_sends < target and attempts < max_attempts:
                    attempts += 1
                    lead = lead_queue.pop(0)
                    result = await executor.execute_single_lead(account, campaign, lead)

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
                        consecutive_errors = 0
                    else:
                        consecutive_errors += 1

                        # 3+ consecutive errors likely means cookie expired / session broken
                        if consecutive_errors >= 3:
                            logger.error(
                                "dispatch.consecutive_errors_detected",
                                account=account.name,
                                campaign=campaign.name,
                                consecutive=consecutive_errors,
                            )
                            await repo.update_account(account, status="cookie_expired")
                            await repo.log_action(
                                account_id=account.id,
                                campaign_id=campaign.id,
                                action_type=ActionType.ERROR,
                                status=ActionLogStatus.FAILED,
                                details={"reason": "consecutive_navigation_errors", "count": consecutive_errors},
                            )
                            stop_account = True
                            break

                        # Lead was skipped/errored — backfill from pending pool
                        backfill = await repo.get_pending_leads(campaign.id, limit=1)
                        if backfill:
                            bl = backfill[0]
                            # Transition to SCHEDULED so executor can mark CONNECTION_REQUESTED
                            bl.status = LeadStatus.SCHEDULED
                            bl.scheduled_at = datetime.utcnow()
                            await repo.session.commit()
                            lead_queue.append(bl)
                            logger.info(
                                "dispatch.backfill_lead",
                                campaign=campaign.name,
                                new_lead=bl.linkedin_url,
                            )

                if successful_sends > 0:
                    logger.info(
                        "dispatch.campaign_done",
                        campaign=campaign.name,
                        successful=successful_sends,
                        target=target,
                    )

                if stop_account:
                    break

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

    Runs every 3 hours. Visits invitation manager and individual
    profiles to detect newly accepted connections.
    """
    repo, session = await _get_repo()
    settings = get_settings()
    try:
        accounts = await repo.list_active_accounts()
        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            campaigns = await repo.get_active_campaigns(account.id)
            for campaign in campaigns:
                requested_leads = await repo.get_leads_by_status(
                    campaign.id, LeadStatus.CONNECTION_REQUESTED
                )
                if not requested_leads:
                    continue

                # Limit checks per cycle
                to_check = requested_leads[:settings.max_profiles_per_acceptance_check]

                from linauto.linkedin.browser import LinkedInBrowser
                from linauto.linkedin.actions import LinkedInActions

                browser = LinkedInBrowser()
                try:
                    await browser.launch(
                        account_id=account.id,
                        li_at_cookie=account.li_at_cookie,
                        user_agent=account.user_agent,
                        proxy_url=account.proxy_url,
                        timezone=account.timezone,
                    )
                    valid = await browser.validate_session()
                    if not valid:
                        logger.warning("acceptance.session_expired", account=account.name)
                        continue

                    page = await browser.new_page()
                    actions = LinkedInActions(page)

                    newly_connected = []
                    for lead in to_check:
                        status = await actions.check_connection_status(lead.linkedin_url)
                        if status == "connected":
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
                            await repo.increment_daily_stat(
                                account.id, "connections_accepted"
                            )
                            newly_connected.append(lead)
                            logger.info("acceptance.connected", url=lead.linkedin_url)

                        # Small delay between checks
                        from linauto.safety.delays import DelayGenerator
                        await DelayGenerator().micro_delay(2, 5)

                    # Check if we need to withdraw stale invitations
                    if account.withdraw_threshold:
                        try:
                            pending_count = await actions.get_pending_invitation_count()
                            if pending_count > account.withdraw_threshold:
                                excess = pending_count - account.withdraw_threshold
                                to_withdraw = min(excess, 10)  # Max 10 per cycle
                                logger.info(
                                    "withdraw.starting",
                                    account=account.name,
                                    pending=pending_count,
                                    threshold=account.withdraw_threshold,
                                    withdrawing=to_withdraw,
                                )
                                withdrawn_urls = await actions.withdraw_oldest_invitations(to_withdraw)
                                for url in withdrawn_urls:
                                    matching = await repo.get_leads_by_url(account.id, url)
                                    for ml in matching:
                                        if ml.status == LeadStatus.CONNECTION_REQUESTED:
                                            await repo.update_lead(ml, status=LeadStatus.WITHDRAWN)
                                if withdrawn_urls:
                                    await repo.log_action(
                                        account_id=account.id,
                                        action_type=ActionType.INVITATION_WITHDRAWN,
                                        status=ActionLogStatus.SUCCESS,
                                        details={"withdrawn": len(withdrawn_urls), "pending_before": pending_count},
                                    )
                            elif pending_count >= 0:
                                logger.info(
                                    "withdraw.under_threshold",
                                    account=account.name,
                                    pending=pending_count,
                                    threshold=account.withdraw_threshold,
                                )
                        except Exception as e:
                            logger.error("withdraw.failed", account=account.name, error=str(e))

                    await page.close()
                finally:
                    await browser.close()

                # Send immediate follow-up messages for newly connected leads
                if newly_connected and campaign.followup_enabled:
                    has_messages = any(
                        getattr(campaign, f"followup_message_{i}", None)
                        for i in (1, 2, 3)
                    )
                    if has_messages:
                        from linauto.campaign.executor import CampaignExecutor
                        executor = CampaignExecutor(repo)
                        for lead in newly_connected:
                            logger.info(
                                "followup.starting_sequence",
                                url=lead.linkedin_url,
                                campaign=campaign.name,
                            )
                            fu_result = await executor.execute_followup_sequence(
                                account, campaign, lead
                            )
                            if fu_result["success"]:
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
                                logger.warning(
                                    "followup.sequence_failed",
                                    url=lead.linkedin_url,
                                )
                            if fu_result.get("fatal"):
                                break

    except Exception as e:
        logger.error("acceptance.check_failed", error=str(e))
    finally:
        await session.close()


async def check_cookie_health():
    """
    Proactive daily session validation for all active accounts.

    Runs once daily at 05:00. Marks accounts as cookie_expired if invalid.
    Also saves avatars for accounts that don't have one yet.
    """
    repo, session = await _get_repo()
    try:
        accounts = await repo.list_active_accounts()
        for account in accounts:
            if account.paused_until and not is_cooldown_expired(account.paused_until):
                continue

            from linauto.linkedin.browser import LinkedInBrowser
            browser = LinkedInBrowser()
            try:
                await browser.launch(
                    account_id=account.id,
                    li_at_cookie=account.li_at_cookie,
                    user_agent=account.user_agent,
                    proxy_url=account.proxy_url,
                    timezone=account.timezone,
                )
                valid = await browser.validate_session()
                if not valid:
                    logger.warning("cookie_health.expired", account=account.name)
                    await repo.update_account(account, status="cookie_expired")
                    await repo.log_action(
                        account_id=account.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": "cookie_health_check_failed"},
                    )
                else:
                    logger.info("cookie_health.valid", account=account.name)
                    # Save avatar if not yet saved
                    if not account.avatar_path:
                        avatar_path = await browser.save_avatar(account.id)
                        if avatar_path:
                            await repo.update_account(account, avatar_path=avatar_path)
            except Exception as e:
                logger.error("cookie_health.error", account=account.name, error=str(e))
            finally:
                await browser.close()

    except Exception as e:
        logger.error("cookie_health.sweep_failed", error=str(e))
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


async def start_scheduler():
    """Start the APScheduler daemon. Blocks until interrupted."""
    await init_db()

    # Start API server if enabled
    await _start_api_server()

    scheduler = AsyncIOScheduler()

    # Daily planning sweep at 06:00
    scheduler.add_job(
        daily_planning_sweep,
        CronTrigger(hour=6, minute=0),
        id="daily_planner",
        name="Daily Planning Sweep",
        replace_existing=True,
    )

    # Dispatcher every 5 minutes
    scheduler.add_job(
        dispatch,
        IntervalTrigger(minutes=5),
        id="dispatcher",
        name="Action Dispatcher",
        replace_existing=True,
    )

    # Acceptance checker every 3 hours
    settings = get_settings()
    scheduler.add_job(
        check_acceptances,
        IntervalTrigger(hours=settings.acceptance_check_interval_hours),
        id="acceptance_checker",
        name="Acceptance Checker",
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

    # Cookie health check daily at 05:00
    scheduler.add_job(
        check_cookie_health,
        CronTrigger(hour=5, minute=0),
        id="cookie_health_checker",
        name="Cookie Health Check",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("scheduler.started", jobs=len(scheduler.get_jobs()))

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
    await close_db()
    logger.info("scheduler.stopped")

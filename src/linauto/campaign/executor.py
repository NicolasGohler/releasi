"""Campaign execution orchestrator — ties browser actions, state machine, and logging together."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import List, Optional, Sequence

import structlog

from playwright.async_api import BrowserContext

from linauto.db.models import Lead, LeadStatus, Account, Campaign, ActionType, ActionLogStatus
from linauto.db.repository import Repository
from linauto.linkedin.browser import LinkedInBrowser
from linauto.linkedin.actions import LinkedInActions, ActionStatus
from linauto.campaign.template import render_template
from linauto.campaign.state_machine import validate_transition
from linauto.linkedin.profile_filter import ProfileFilters
from linauto.safety.delays import DelayGenerator

logger = structlog.get_logger()

# Re-export classification helpers from the no-deps module so existing
# callers (and tests that mock these names) keep working.
from linauto.safety.error_signals import (
    is_network_error as _is_network_error,
    is_session_expired_signal as _is_session_expired_signal,
)


class CampaignExecutor:
    """Executes campaign actions for a batch of leads."""

    def __init__(self, repo: Repository, browser_context: Optional[BrowserContext] = None):
        self.repo = repo
        self.delay = DelayGenerator()
        self._browser: Optional[LinkedInBrowser] = None
        self._shared_context = browser_context

    async def execute_single_lead(
        self,
        account: Account,
        campaign: Campaign,
        lead: Lead,
    ) -> dict:
        """
        Execute a single connection request (used by the scheduler dispatcher).

        When ``self._shared_context`` is set (pool mode), opens/closes only
        pages on the shared browser.  Otherwise creates an ephemeral browser.
        """
        # `session_expired` is the explicit cookie-expiry signal (redirect
        # loop, /login redirect, authwall, checkpoint). When set, the
        # dispatcher should mark the account cookie_expired immediately
        # rather than waiting for 3 consecutive errors.
        result = {
            "success": False,
            "limit_reached": False,
            "fatal": False,
            "skipped": False,
            "session_expired": False,
        }

        # Pool mode: use shared context, only manage pages
        browser: Optional[LinkedInBrowser] = None
        if self._shared_context:
            page = await self._shared_context.new_page()
        else:
            browser = LinkedInBrowser()
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if not valid:
                logger.error("executor.session_invalid", account=account.name)
                result["fatal"] = True
                result["session_expired"] = True
                await browser.close()
                return result
            page = await browser.new_page()

        try:
            actions = LinkedInActions(page)

            # Optional: browsing noise before request
            try:
                from linauto.linkedin.noise import BrowsingNoise
                noise = BrowsingNoise(page)
                import random
                if random.random() < 0.4:  # 40% chance of noise before action
                    await noise.view_random_profile()
            except Exception:
                pass  # Noise is best-effort

            # Render message
            message = None
            if campaign.connection_message_template:
                message = render_template(campaign.connection_message_template, lead)

            # Build profile filters from campaign config
            filters = ProfileFilters(
                no_photo=campaign.filter_no_photo,
                min_connections=campaign.filter_min_connections,
                exclude_open_to_work=campaign.filter_exclude_open_to_work,
            )

            # Execute
            action_result = await actions.send_connection_request(lead.linkedin_url, message, filters=filters)

            if action_result.status == ActionStatus.SUCCESS:
                validate_transition(lead.status, LeadStatus.CONNECTION_REQUESTED)
                await self.repo.update_lead(
                    lead,
                    status=LeadStatus.CONNECTION_REQUESTED,
                    connection_requested_at=datetime.utcnow(),
                )
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.CONNECTION_REQUEST,
                    status=ActionLogStatus.SUCCESS,
                )
                await self.repo.increment_daily_stat(account.id, "connection_requests_sent")
                result["success"] = True

            elif action_result.status == ActionStatus.LIMIT_REACHED:
                # Differentiate weekly quota (pause until Monday) from soft rate-limit modal
                # (2–4 hour backoff only — leads stay SCHEDULED for later today).
                if action_result.reason == "rate_limit_modal":
                    result["soft_limit_reached"] = True
                else:
                    result["limit_reached"] = True
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.LIMIT_DETECTED,
                    status=ActionLogStatus.FAILED,
                    details=action_result.details,
                )

            elif action_result.status in (ActionStatus.CAPTCHA, ActionStatus.SESSION_EXPIRED):
                result["fatal"] = True
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.ERROR,
                    status=ActionLogStatus.FAILED,
                    details={"reason": action_result.status.value},
                )

            elif action_result.status == ActionStatus.INVALID:
                # Lead data is permanently bad (404, deleted profile) — never retry.
                validate_transition(lead.status, LeadStatus.INVALID)
                await self.repo.update_lead(
                    lead, status=LeadStatus.INVALID, error_message=action_result.reason
                )
                await self.repo.log_action(
                    account_id=account.id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    action_type=ActionType.CONNECTION_REQUEST,
                    status=ActionLogStatus.SKIPPED,
                    details={"reason": action_result.reason},
                )
                result["skipped"] = True  # counts as skipped for dispatcher backfill

            elif action_result.status == ActionStatus.SKIPPED:
                if action_result.reason == "already_connected":
                    # Already a 1st-degree connection — mark CONNECTED, not SKIPPED.
                    # Don't count as a new send; just update the DB to reflect reality.
                    validate_transition(lead.status, LeadStatus.CONNECTED)
                    await self.repo.update_lead(
                        lead,
                        status=LeadStatus.CONNECTED,
                        connection_accepted_at=datetime.utcnow(),
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SUCCESS,
                        details={"reason": "already_connected"},
                    )
                else:
                    validate_transition(lead.status, LeadStatus.SKIPPED)
                    await self.repo.update_lead(
                        lead, status=LeadStatus.SKIPPED, error_message=action_result.reason
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SKIPPED,
                        details={"reason": action_result.reason},
                    )
                result["skipped"] = True

            else:  # ERROR
                reason = action_result.reason or ""
                # Priority chain: explicit session signal → mark fatal +
                # session_expired and DON'T burn the lead to ERROR (the
                # request never went through). Network signal → flag for
                # dispatcher, mark ERROR. Else → mark ERROR normally.
                if _is_session_expired_signal(reason):
                    result["fatal"] = True
                    result["session_expired"] = True
                    # Leave the lead in its current status so the dispatcher
                    # can reset SCHEDULED → PENDING for re-planning after
                    # cookie renewal (same as the 3-strike path already does).
                else:
                    validate_transition(lead.status, LeadStatus.ERROR)
                    await self.repo.update_lead(
                        lead,
                        status=LeadStatus.ERROR,
                        retry_count=lead.retry_count + 1,
                        error_message=reason,
                    )
                    await self.repo.increment_daily_stat(account.id, "errors")
                    if _is_network_error(reason):
                        result["network_error"] = True

        except Exception as e:
            err_str = str(e)
            logger.error("executor.single_lead_failed", error=err_str)
            # Priority chain mirrors the action-result branch above.
            if _is_session_expired_signal(err_str):
                # Don't burn the lead — message never went through.
                result["fatal"] = True
                result["session_expired"] = True
            else:
                try:
                    await self.repo.update_lead(
                        lead,
                        status=LeadStatus.ERROR,
                        retry_count=lead.retry_count + 1,
                        error_message=err_str[:500],
                    )
                except Exception:
                    pass  # Best-effort — don't mask the original error
                if _is_network_error(err_str):
                    result["network_error"] = True
                else:
                    result["fatal"] = True
        finally:
            await page.close()
            if browser:
                await browser.close()

        return result

    async def execute_followup_sequence(
        self,
        account: Account,
        campaign: Campaign,
        lead: Lead,
    ) -> dict:
        """
        Send 1-3 follow-up messages immediately after connection acceptance.

        Messages are sent with 30-60s delays between each.
        Returns result dict with messages_sent count and success flag.
        """
        # `session_expired` and `network_error` mirror the connection-request
        # path so dispatch_followups can apply the same consecutive-error
        # heuristics (3+ session errors → mark cookie_expired, network errors
        # don't penalise the session, etc.).
        result = {
            "success": False,
            "messages_sent": 0,
            "fatal": False,
            "skipped": False,
            "session_expired": False,
            "network_error": False,
            "reason": None,
        }

        # ── Guard 1: action-log resume ─────────────────────────────────────────
        # Check the highest message_index already successfully sent for this lead.
        # If all messages were sent in a prior run, skip entirely (dedup).
        # If some were sent (e.g. msg 1 ok, msg 2 crashed), resume from the next one.
        # Collect configured messages first so we know the total.
        messages: List[str] = []
        for attr in ("followup_message_1", "followup_message_2", "followup_message_3"):
            msg = getattr(campaign, attr, None)
            if msg:
                messages.append(msg)

        if not messages:
            logger.warning("followup.no_messages_configured", campaign=campaign.name)
            return result

        start_from = await self.repo.get_last_successful_followup_index(lead.id)
        # start_from is 1-based (message_index); loop i is 0-based.
        # start_from == len(messages) means all messages already sent → full skip.
        if start_from >= len(messages):
            logger.warning(
                "followup.action_log_dedup_skipped",
                url=lead.linkedin_url,
                messages_already_sent=start_from,
            )
            result["skipped"] = True
            return result
        if start_from > 0:
            logger.info(
                "followup.resuming_partial_sequence",
                url=lead.linkedin_url,
                resuming_from_index=start_from + 1,
            )

        # Pool mode: use shared context, only manage pages
        browser: Optional[LinkedInBrowser] = None
        if self._shared_context:
            page = await self._shared_context.new_page()
        else:
            browser = LinkedInBrowser()
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if not valid:
                logger.error("followup.session_invalid", account=account.name)
                result["fatal"] = True
                result["session_expired"] = True
                result["reason"] = "session_validate_failed"
                await browser.close()
                return result
            page = await browser.new_page()

        try:
            actions = LinkedInActions(page)

            for i, msg_template in enumerate(messages):
                if i < start_from:
                    continue  # already sent in a prior run

                msg = render_template(msg_template, lead)

                action_result = await actions.send_message(
                    lead.linkedin_url,
                    msg,
                    skip_prior_conversation_check=(i > 0),
                )

                if action_result.status == ActionStatus.SKIPPED:
                    # Prior conversation detected — treat the whole sequence as skipped.
                    result["skipped"] = True
                    logger.info(
                        "followup.skipped_existing_conversation",
                        url=lead.linkedin_url,
                        reason=action_result.reason,
                    )
                    break

                elif action_result.status == ActionStatus.SUCCESS:
                    result["messages_sent"] += 1
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.FOLLOWUP_MESSAGE,
                        status=ActionLogStatus.SUCCESS,
                        details={"message_index": i + 1, "total": len(messages)},
                    )
                    await self.repo.increment_daily_stat(account.id, "followup_messages_sent")
                    logger.info(
                        "followup.message_sent",
                        url=lead.linkedin_url,
                        index=i + 1,
                        total=len(messages),
                    )

                    # Wait 10-20s between messages (skip after last)
                    if i < len(messages) - 1:
                        delay = random.uniform(10, 20)
                        logger.info("followup.waiting", seconds=round(delay))
                        await asyncio.sleep(delay)
                else:
                    # Stop on first failure
                    reason_str = action_result.reason or ""
                    logger.error(
                        "followup.message_failed",
                        url=lead.linkedin_url,
                        index=i + 1,
                        reason=reason_str,
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.FOLLOWUP_MESSAGE,
                        status=ActionLogStatus.FAILED,
                        details={
                            "message_index": i + 1,
                            "reason": reason_str,
                        },
                    )
                    result["reason"] = reason_str
                    # Classify the failure so dispatch_followups can apply the
                    # same consecutive-error heuristics as the connection
                    # dispatcher. Priority: explicit status → session signal
                    # in reason string → network signal → generic fatal.
                    if action_result.status == ActionStatus.SESSION_EXPIRED:
                        result["fatal"] = True
                        result["session_expired"] = True
                    elif action_result.status == ActionStatus.CAPTCHA:
                        result["fatal"] = True
                    elif _is_session_expired_signal(reason_str):
                        # ERR_TOO_MANY_REDIRECTS, /login redirect, authwall, etc.
                        result["fatal"] = True
                        result["session_expired"] = True
                    elif _is_network_error(reason_str):
                        result["network_error"] = True
                    break

            if result["messages_sent"] > 0:
                result["success"] = True

        except Exception as e:
            err_str = str(e)
            logger.error("followup.sequence_failed", error=err_str)
            result["reason"] = err_str
            # Same priority chain as the action-level error path.
            if _is_session_expired_signal(err_str):
                result["fatal"] = True
                result["session_expired"] = True
            elif _is_network_error(err_str):
                result["network_error"] = True
            else:
                result["fatal"] = True
        finally:
            await page.close()
            if browser:
                await browser.close()

        return result

    async def execute_batch(
        self,
        account: Account,
        campaign: Campaign,
        leads: Sequence[Lead],
    ) -> dict:
        """
        Execute connection requests for a batch of leads.
        Returns summary stats.
        """
        stats = {"success": 0, "skipped": 0, "error": 0, "limit_reached": False}

        browser = LinkedInBrowser()
        try:
            context = await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                proxy_country=account.proxy_country,
                timezone=account.timezone,
            )

            # Validate session first
            is_valid = await browser.validate_session()
            if not is_valid:
                logger.error("executor.session_invalid", account=account.name)
                await self.repo.update_account(account, status="cookie_expired")
                return stats

            page = await browser.new_page()
            actions = LinkedInActions(page)

            # Build profile filters from campaign config
            filters = ProfileFilters(
                no_photo=campaign.filter_no_photo,
                min_connections=campaign.filter_min_connections,
                exclude_open_to_work=campaign.filter_exclude_open_to_work,
            )

            for i, lead in enumerate(leads):
                logger.info(
                    "executor.processing_lead",
                    lead=lead.linkedin_url,
                    index=i + 1,
                    total=len(leads),
                )

                # Render personalized message
                message = None
                if campaign.connection_message_template:
                    message = render_template(campaign.connection_message_template, lead)

                # Execute the action
                result = await actions.send_connection_request(lead.linkedin_url, message, filters=filters)

                # Handle result
                if result.status == ActionStatus.SUCCESS:
                    validate_transition(lead.status, LeadStatus.CONNECTION_REQUESTED)
                    await self.repo.update_lead(
                        lead,
                        status=LeadStatus.CONNECTION_REQUESTED,
                        connection_requested_at=datetime.utcnow(),
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SUCCESS,
                    )
                    await self.repo.increment_daily_stat(
                        account.id, "connection_requests_sent"
                    )
                    stats["success"] += 1
                    logger.info("executor.request_sent", url=lead.linkedin_url)

                elif result.status == ActionStatus.SKIPPED:
                    validate_transition(lead.status, LeadStatus.SKIPPED)
                    await self.repo.update_lead(
                        lead,
                        status=LeadStatus.SKIPPED,
                        error_message=result.reason,
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.CONNECTION_REQUEST,
                        status=ActionLogStatus.SKIPPED,
                        details={"reason": result.reason},
                    )
                    stats["skipped"] += 1
                    logger.info("executor.skipped", url=lead.linkedin_url, reason=result.reason)

                elif result.status == ActionStatus.LIMIT_REACHED:
                    # Stop processing — weekly limit hit
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.LIMIT_DETECTED,
                        status=ActionLogStatus.FAILED,
                        details=result.details,
                    )
                    stats["limit_reached"] = True
                    logger.warning("executor.limit_reached", url=lead.linkedin_url)
                    break

                elif result.status in (ActionStatus.CAPTCHA, ActionStatus.SESSION_EXPIRED):
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": result.status.value},
                    )
                    logger.error("executor.critical_error", status=result.status.value)
                    break

                else:  # ERROR
                    new_retry = lead.retry_count + 1
                    new_status = LeadStatus.ERROR
                    validate_transition(lead.status, new_status)
                    await self.repo.update_lead(
                        lead,
                        status=new_status,
                        retry_count=new_retry,
                        error_message=result.reason,
                    )
                    await self.repo.log_action(
                        account_id=account.id,
                        campaign_id=campaign.id,
                        lead_id=lead.id,
                        action_type=ActionType.ERROR,
                        status=ActionLogStatus.FAILED,
                        details={"reason": result.reason, **result.details},
                    )
                    await self.repo.increment_daily_stat(account.id, "errors")
                    stats["error"] += 1
                    logger.error("executor.error", url=lead.linkedin_url, reason=result.reason)

                # Wait between actions (skip delay after last lead)
                if i < len(leads) - 1 and not stats["limit_reached"]:
                    await self.delay.action_delay()

            await page.close()

        finally:
            await browser.close()

        return stats

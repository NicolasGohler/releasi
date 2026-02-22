"""Campaign execution orchestrator — ties browser actions, state machine, and logging together."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

import structlog

from linauto.db.models import Lead, LeadStatus, Account, Campaign, ActionType, ActionLogStatus
from linauto.db.repository import Repository
from linauto.linkedin.browser import LinkedInBrowser
from linauto.linkedin.actions import LinkedInActions, ActionStatus
from linauto.campaign.template import render_template
from linauto.campaign.state_machine import validate_transition
from linauto.linkedin.profile_filter import ProfileFilters
from linauto.safety.delays import DelayGenerator

logger = structlog.get_logger()


class CampaignExecutor:
    """Executes campaign actions for a batch of leads."""

    def __init__(self, repo: Repository):
        self.repo = repo
        self.delay = DelayGenerator()
        self._browser: Optional[LinkedInBrowser] = None

    async def execute_single_lead(
        self,
        account: Account,
        campaign: Campaign,
        lead: Lead,
    ) -> dict:
        """
        Execute a single connection request (used by the scheduler dispatcher).
        Manages browser lifecycle per call. Returns result dict.
        """
        result = {"success": False, "limit_reached": False, "fatal": False}

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
                logger.error("executor.session_invalid", account=account.name)
                result["fatal"] = True
                return result

            page = await browser.new_page()
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

            elif action_result.status == ActionStatus.SKIPPED:
                validate_transition(lead.status, LeadStatus.SKIPPED)
                await self.repo.update_lead(
                    lead, status=LeadStatus.SKIPPED, error_message=action_result.reason
                )

            else:  # ERROR
                await self.repo.update_lead(
                    lead,
                    status=LeadStatus.ERROR,
                    retry_count=lead.retry_count + 1,
                    error_message=action_result.reason,
                )
                await self.repo.increment_daily_stat(account.id, "errors")

            await page.close()

        except Exception as e:
            logger.error("executor.single_lead_failed", error=str(e))
            result["fatal"] = True
        finally:
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

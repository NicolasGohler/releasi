"""Data access layer — CRUD operations for all models."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Sequence

from sqlalchemy import case, select, func, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from linauto.db.models import (
    Account, AccountStatus,
    Campaign, CampaignStatus,
    Lead, LeadStatus,
    ActionLog, ActionType, ActionLogStatus,
    DailyStat,
    LeadList, CampaignLeadList,
)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Accounts ───────────────────────────────────────────────────────────

    async def create_account(self, name: str, li_at_cookie: str, **kwargs) -> Account:
        account = Account(name=name, li_at_cookie=li_at_cookie, **kwargs)
        self.session.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def get_account_by_name(self, name: str) -> Account | None:
        result = await self.session.execute(
            select(Account).where(Account.name == name)
        )
        return result.scalar_one_or_none()

    async def get_account(self, account_id: str) -> Account | None:
        return await self.session.get(Account, account_id)

    async def list_accounts(self, include_archived: bool = False) -> Sequence[Account]:
        stmt = select(Account).order_by(Account.name)
        if not include_archived:
            stmt = stmt.where(Account.archived == False)  # noqa: E712
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_account(self, account: Account, **kwargs) -> Account:
        for key, value in kwargs.items():
            setattr(account, key, value)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    # ── Campaigns ──────────────────────────────────────────────────────────

    async def create_campaign(self, account_id: str, name: str, **kwargs) -> Campaign:
        campaign = Campaign(account_id=account_id, name=name, **kwargs)
        self.session.add(campaign)
        await self.session.commit()
        await self.session.refresh(campaign)
        return campaign

    async def get_campaign_by_name(self, name: str) -> Campaign | None:
        result = await self.session.execute(
            select(Campaign).where(Campaign.name == name)
        )
        return result.scalar_one_or_none()

    async def get_campaign(self, campaign_id: str) -> Campaign | None:
        return await self.session.get(Campaign, campaign_id)

    async def list_campaigns(self, account_id: str | None = None, include_archived: bool = False) -> Sequence[Campaign]:
        stmt = select(Campaign).order_by(Campaign.created_at.desc())
        if account_id:
            stmt = stmt.where(Campaign.account_id == account_id)
        if not include_archived:
            stmt = stmt.where(Campaign.archived == False)  # noqa: E712
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_campaign(self, campaign: Campaign, **kwargs) -> Campaign:
        for key, value in kwargs.items():
            setattr(campaign, key, value)
        await self.session.commit()
        await self.session.refresh(campaign)
        return campaign

    # ── Leads ──────────────────────────────────────────────────────────────

    async def bulk_create_leads(self, leads: list[Lead]) -> int:
        self.session.add_all(leads)
        await self.session.commit()
        return len(leads)

    async def get_leads_by_status(
        self, campaign_id: str, status: LeadStatus
    ) -> Sequence[Lead]:
        result = await self.session.execute(
            select(Lead)
            .where(Lead.campaign_id == campaign_id, Lead.status == status)
            .order_by(Lead.created_at)
        )
        return result.scalars().all()

    async def get_scheduled_leads(
        self, campaign_id: str, before: datetime
    ) -> Sequence[Lead]:
        """Get leads scheduled to execute before the given time."""
        result = await self.session.execute(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.SCHEDULED,
                Lead.scheduled_at <= before,
            )
            .order_by(Lead.scheduled_at)
        )
        return result.scalars().all()

    async def count_future_scheduled_leads(self, campaign_id: str) -> int:
        """Count SCHEDULED leads whose scheduled_at is still in the future."""
        now = datetime.utcnow()
        result = await self.session.execute(
            select(func.count()).where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.SCHEDULED,
                Lead.scheduled_at > now,
            )
        )
        return result.scalar_one()

    async def plan_generated_today(self, campaign_id: str, local_date: "date") -> bool:
        """Return True if a DAILY_PLAN_GENERATED entry exists for this campaign today.

        Used by the planner to distinguish a real daily plan from a single
        dispatcher backfill lead — both result in future_scheduled > 0, but
        only the former means planning is done for the day.
        """
        day_str = local_date.isoformat()  # stored in details JSON as "date": "YYYY-MM-DD"
        result = await self.session.execute(
            select(func.count()).where(
                ActionLog.campaign_id == campaign_id,
                ActionLog.action_type == ActionType.DAILY_PLAN_GENERATED,
                ActionLog.status == ActionLogStatus.SUCCESS,
                # created_at covers any UTC time that day; the details.date field
                # is the account's local date, which is the authoritative anchor.
                ActionLog.details.contains(day_str),
            )
        )
        return (result.scalar_one() or 0) > 0

    async def reset_stale_scheduled_leads(self, campaign_id: str) -> int:
        """Reset SCHEDULED leads with a past scheduled_at back to PENDING.

        Called at the start of each planning sweep so that leads from a
        missed/skipped window (container restart, previous day) re-enter
        the pending pool and are cleanly re-planned rather than silently
        accumulating as a backlog of overdue SCHEDULED rows.
        Returns the number of leads reset.
        """
        now = datetime.utcnow()
        result = await self.session.execute(
            update(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.SCHEDULED,
                Lead.scheduled_at < now,
            )
            .values(status=LeadStatus.PENDING, scheduled_at=None)
        )
        await self.session.commit()
        return result.rowcount

    async def get_latest_future_scheduled_at(self, campaign_id: str) -> Optional[datetime]:
        """Return the latest scheduled_at among SCHEDULED leads still in the future."""
        now = datetime.utcnow()
        result = await self.session.execute(
            select(func.max(Lead.scheduled_at)).where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.SCHEDULED,
                Lead.scheduled_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def get_followup_due_leads(
        self, campaign_id: str, before: datetime
    ) -> Sequence[Lead]:
        """Get leads with follow-ups scheduled before the given time."""
        result = await self.session.execute(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.FOLLOWUP_SCHEDULED,
                Lead.scheduled_at <= before,
            )
            .order_by(Lead.scheduled_at)
        )
        return result.scalars().all()

    async def get_stranded_followup_leads(self, campaign_id: str) -> Sequence[Lead]:
        """Get CONNECTED leads that never got a followup scheduled or sent.

        These are leads that the acceptance checker marked CONNECTED but whose
        followup scheduling was missed (e.g. followup was disabled at accept
        time, a scheduler restart interrupted the step, etc.).  They are
        "stranded" because the acceptance checker only schedules *newly*
        connected leads, so they would sit in CONNECTED forever without this
        rescue query.
        """
        result = await self.session.execute(
            select(Lead).where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.CONNECTED,
                Lead.followup_sent_at.is_(None),
            )
        )
        return result.scalars().all()

    async def get_pending_leads(
        self, campaign_id: str, limit: int | None = None
    ) -> Sequence[Lead]:
        stmt = (
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status == LeadStatus.PENDING,
            )
            .order_by(Lead.created_at)
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def lead_exists_in_campaign(
        self, campaign_id: str, linkedin_url: str
    ) -> bool:
        result = await self.session.execute(
            select(func.count()).where(
                Lead.campaign_id == campaign_id,
                Lead.linkedin_url == linkedin_url,
            )
        )
        return result.scalar_one() > 0

    async def update_lead(self, lead: Lead, **kwargs) -> Lead:
        for key, value in kwargs.items():
            setattr(lead, key, value)
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def get_campaign_status_counts(self, campaign_id: str) -> dict[str, int]:
        """Get lead counts grouped by status for a campaign (excludes REMOVED leads)."""
        result = await self.session.execute(
            select(Lead.status, func.count())
            .where(
                Lead.campaign_id == campaign_id,
                Lead.status != LeadStatus.REMOVED,
            )
            .group_by(Lead.status)
        )
        return {row[0].value: row[1] for row in result.all()}

    async def get_list_status_counts_for_campaign(
        self, lead_list_id: str, campaign_id: str
    ) -> dict[str, int]:
        """Get lead counts grouped by status for a specific list within a campaign."""
        result = await self.session.execute(
            select(Lead.status, func.count())
            .where(
                Lead.lead_list_id == lead_list_id,
                Lead.campaign_id == campaign_id,
                Lead.status != LeadStatus.REMOVED,
            )
            .group_by(Lead.status)
        )
        return {row[0].value: row[1] for row in result.all()}

    async def get_leads_by_ids(self, lead_ids: list) -> dict:
        """Batch-fetch leads by id. Returns {lead_id: Lead}."""
        if not lead_ids:
            return {}
        result = await self.session.execute(
            select(Lead).where(Lead.id.in_(lead_ids))
        )
        return {l.id: l for l in result.scalars().all()}

    # ── Action Log ─────────────────────────────────────────────────────────

    async def log_action(
        self,
        account_id: str,
        action_type: ActionType,
        status: ActionLogStatus,
        campaign_id: str | None = None,
        lead_id: str | None = None,
        details: dict | None = None,
    ) -> ActionLog:
        entry = ActionLog(
            account_id=account_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            action_type=action_type,
            status=status,
            details=details,
        )
        self.session.add(entry)
        await self.session.commit()
        return entry

    # ── Daily Stats ────────────────────────────────────────────────────────

    async def get_or_create_daily_stat(
        self, account_id: str, stat_date: date | None = None
    ) -> DailyStat:
        stat_date = stat_date or date.today()
        result = await self.session.execute(
            select(DailyStat).where(
                DailyStat.account_id == account_id,
                DailyStat.date == stat_date,
            )
        )
        stat = result.scalar_one_or_none()
        if stat is None:
            stat = DailyStat(account_id=account_id, date=stat_date)
            self.session.add(stat)
            await self.session.commit()
            await self.session.refresh(stat)
        return stat

    async def increment_daily_stat(
        self, account_id: str, field: str, amount: int = 1
    ) -> None:
        stat = await self.get_or_create_daily_stat(account_id)
        setattr(stat, field, getattr(stat, field) + amount)
        await self.session.commit()

    async def get_weekly_request_count(
        self, account_id: str, week_start: date
    ) -> int:
        """Sum connection_requests_sent from week_start through +6 days."""
        from datetime import timedelta
        week_end = week_start + timedelta(days=6)
        result = await self.session.execute(
            select(func.coalesce(func.sum(DailyStat.connection_requests_sent), 0))
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date >= week_start,
                DailyStat.date <= week_end,
            )
        )
        return result.scalar_one()

    async def get_daily_proxy_mb(self, account_id: str) -> float:
        """Get today's estimated proxy bandwidth usage in MB."""
        stat = await self.get_or_create_daily_stat(account_id)
        return stat.proxy_mb_used or 0.0

    async def add_proxy_mb(self, account_id: str, mb: float) -> None:
        """Add MB to today's proxy bandwidth usage tally."""
        stat = await self.get_or_create_daily_stat(account_id)
        stat.proxy_mb_used = round((stat.proxy_mb_used or 0.0) + mb, 2)
        await self.session.commit()

    async def get_daily_requests_sent(
        self, account_id: str, stat_date: date | None = None
    ) -> int:
        """Get today's connection_requests_sent count."""
        stat_date = stat_date or date.today()
        result = await self.session.execute(
            select(func.coalesce(DailyStat.connection_requests_sent, 0))
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date == stat_date,
            )
        )
        return result.scalar_one_or_none() or 0

    async def get_followup_messages_sent_today(
        self, account_id: str, stat_date: date | None = None
    ) -> int:
        """Get today's followup_messages_sent count from DailyStat."""
        stat_date = stat_date or date.today()
        result = await self.session.execute(
            select(func.coalesce(DailyStat.followup_messages_sent, 0))
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date == stat_date,
            )
        )
        return result.scalar_one_or_none() or 0

    async def get_lead_by_id(self, lead_id: str) -> Optional[Lead]:
        """Fetch a single lead by primary key (for pre-send status re-verification)."""
        return await self.session.get(Lead, lead_id)

    async def has_successful_followup_log(self, lead_id: str) -> bool:
        """Return True if a successful FOLLOWUP_MESSAGE action exists for this lead.

        Used as a belt-and-suspenders guard: if the lead status update after a
        send failed (e.g. crash between browser action and DB write), the action
        log still records the send so we don't resend to the same person.
        """
        result = await self.session.execute(
            select(ActionLog.id)
            .where(
                ActionLog.lead_id == lead_id,
                ActionLog.action_type == ActionType.FOLLOWUP_MESSAGE,
                ActionLog.status == ActionLogStatus.SUCCESS,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def count_leads_by_status(self, campaign_id: str, statuses: list) -> int:
        """Count leads in a campaign matching any of the given statuses."""
        result = await self.session.execute(
            select(func.count()).select_from(Lead).where(
                Lead.campaign_id == campaign_id,
                Lead.status.in_(statuses),
            )
        )
        return result.scalar_one()

    async def count_leads_updated_today_with_status(self, campaign_id: str, status: LeadStatus) -> int:
        """Count leads in a campaign updated today with the given status."""
        from sqlalchemy import cast, Date as SADate
        from datetime import date as date_type
        result = await self.session.execute(
            select(func.count()).select_from(Lead).where(
                Lead.campaign_id == campaign_id,
                Lead.status == status,
                cast(Lead.updated_at, SADate) == date_type.today(),
            )
        )
        return result.scalar_one()

    async def count_pending_requests_for_account(self, account_id: str) -> int:
        """Count CONNECTION_REQUESTED leads across all campaigns for an account."""
        result = await self.session.execute(
            select(func.count()).select_from(Lead)
            .join(Campaign, Campaign.id == Lead.campaign_id)
            .where(
                Campaign.account_id == account_id,
                Lead.status == LeadStatus.CONNECTION_REQUESTED,
            )
        )
        return result.scalar_one()

    async def get_todays_schedule(self, account_id: str, day_start: datetime, day_end: datetime) -> list:
        """Get today's scheduled + already-executed leads for an account.

        Returns list of dicts with lead info + campaign_name + status.
        """
        # SCHEDULED leads (not yet executed)
        scheduled_q = await self.session.execute(
            select(Lead, Campaign.name.label("campaign_name"))
            .join(Campaign, Campaign.id == Lead.campaign_id)
            .where(
                Campaign.account_id == account_id,
                Lead.status == LeadStatus.SCHEDULED,
                Lead.scheduled_at >= day_start,
                Lead.scheduled_at < day_end,
            )
            .order_by(Lead.scheduled_at)
        )
        scheduled_rows = scheduled_q.all()

        # Already-executed today (CONNECTION_REQUESTED with connection_requested_at today)
        executed_q = await self.session.execute(
            select(Lead, Campaign.name.label("campaign_name"))
            .join(Campaign, Campaign.id == Lead.campaign_id)
            .where(
                Campaign.account_id == account_id,
                Lead.status == LeadStatus.CONNECTION_REQUESTED,
                Lead.connection_requested_at >= day_start,
                Lead.connection_requested_at < day_end,
            )
            .order_by(Lead.connection_requested_at)
        )
        executed_rows = executed_q.all()

        # Also include connected leads that were requested today
        connected_q = await self.session.execute(
            select(Lead, Campaign.name.label("campaign_name"))
            .join(Campaign, Campaign.id == Lead.campaign_id)
            .where(
                Campaign.account_id == account_id,
                Lead.status.in_([LeadStatus.CONNECTED, LeadStatus.FOLLOWUP_SCHEDULED, LeadStatus.FOLLOWUP_SENT, LeadStatus.COMPLETED]),
                Lead.connection_requested_at >= day_start,
                Lead.connection_requested_at < day_end,
            )
            .order_by(Lead.connection_requested_at)
        )
        connected_rows = connected_q.all()

        results = []
        for lead, cname in scheduled_rows:
            results.append({
                "lead_id": lead.id,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "linkedin_url": lead.linkedin_url,
                "campaign_name": cname,
                "scheduled_at": lead.scheduled_at.isoformat() if lead.scheduled_at else None,
                "status": lead.status.value,
            })
        for lead, cname in list(executed_rows) + list(connected_rows):
            results.append({
                "lead_id": lead.id,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "linkedin_url": lead.linkedin_url,
                "campaign_name": cname,
                "scheduled_at": lead.connection_requested_at.isoformat() if lead.connection_requested_at else None,
                "status": "sent",
            })

        # Sort by scheduled_at
        results.sort(key=lambda r: r["scheduled_at"] or "")
        return results

    async def get_account_health_stats(self, account_id: str) -> dict:
        """Get health stats for an account: last activity, 7d error rate."""
        from datetime import timedelta
        seven_days_ago = datetime.utcnow() - timedelta(days=7)

        # Last action + 7d error rate in one query
        result = await self.session.execute(
            select(
                func.max(ActionLog.created_at).label("last_action_at"),
                func.count().label("total_7d"),
                func.sum(
                    case(
                        (ActionLog.status == ActionLogStatus.FAILED, 1),
                        else_=0,
                    )
                ).label("errors_7d"),
            ).where(
                ActionLog.account_id == account_id,
                ActionLog.created_at >= seven_days_ago,
            )
        )
        row = result.one()
        total_7d = row.total_7d or 0
        errors_7d = row.errors_7d or 0
        last_action_at = row.last_action_at

        # Last error message
        last_error_msg = None
        if errors_7d > 0:
            err_result = await self.session.execute(
                select(ActionLog.details)
                .where(
                    ActionLog.account_id == account_id,
                    ActionLog.status == ActionLogStatus.FAILED,
                )
                .order_by(ActionLog.created_at.desc())
                .limit(1)
            )
            details = err_result.scalar_one_or_none()
            if details and isinstance(details, dict):
                last_error_msg = details.get("error") or details.get("reason")

        days_since = None
        if last_action_at:
            days_since = (datetime.utcnow() - last_action_at).days

        return {
            "last_action_at": last_action_at,
            "days_since_last_activity": days_since,
            "error_rate_7d": round(errors_7d / total_7d * 100, 1) if total_7d > 0 else 0.0,
            "total_actions_7d": total_7d,
            "errors_7d": errors_7d,
            "last_error_message": last_error_msg,
        }

    # ── Scheduler helpers ─────────────────────────────────────────────────

    async def list_active_accounts(self) -> Sequence[Account]:
        """Get all non-archived accounts with status=active."""
        result = await self.session.execute(
            select(Account).where(
                Account.status == AccountStatus.ACTIVE,
                Account.archived == False,  # noqa: E712
            )
        )
        return result.scalars().all()

    async def list_paused_accounts(self) -> Sequence[Account]:
        """Get all accounts with a paused_until set."""
        result = await self.session.execute(
            select(Account).where(Account.paused_until.isnot(None))
        )
        return result.scalars().all()

    async def get_active_campaigns(self, account_id: str) -> Sequence[Campaign]:
        """Get all active, non-archived campaigns for an account."""
        result = await self.session.execute(
            select(Campaign).where(
                Campaign.account_id == account_id,
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.archived == False,  # noqa: E712
            )
        )
        return result.scalars().all()

    async def get_operational_campaigns(self, account_id: str) -> Sequence[Campaign]:
        """Get all non-archived campaigns (active + paused) for an account.

        Used by acceptance checker and follow-up dispatcher so that leads in
        paused campaigns are still processed — connections accepted while a
        campaign is paused should still be marked CONNECTED.
        """
        result = await self.session.execute(
            select(Campaign).where(
                Campaign.account_id == account_id,
                Campaign.archived == False,  # noqa: E712
            )
        )
        return result.scalars().all()

    async def update_lead_schedule(
        self, lead_id: str, scheduled_at, new_status: LeadStatus
    ) -> None:
        """Update a lead's scheduled_at and status."""
        await self.session.execute(
            update(Lead)
            .where(Lead.id == lead_id)
            .values(scheduled_at=scheduled_at, status=new_status)
        )
        await self.session.commit()

    async def bulk_update_lead_status(
        self,
        campaign_id: str,
        from_status: LeadStatus,
        to_status: LeadStatus,
    ) -> int:
        """Bulk update leads from one status to another within a campaign."""
        result = await self.session.execute(
            update(Lead)
            .where(Lead.campaign_id == campaign_id, Lead.status == from_status)
            .values(status=to_status)
        )
        await self.session.commit()
        return result.rowcount

    async def reset_campaign_leads(
        self,
        campaign_id: str,
        statuses: list[LeadStatus] | None = None,
    ) -> int:
        """
        Reset leads back to PENDING so they can be reprocessed.
        If statuses is None, resets error, skipped, and limit_paused leads.
        Also clears error_message, retry_count, and timestamp fields.
        """
        if statuses is None:
            statuses = [LeadStatus.ERROR, LeadStatus.SKIPPED, LeadStatus.LIMIT_PAUSED]

        result = await self.session.execute(
            update(Lead)
            .where(Lead.campaign_id == campaign_id, Lead.status.in_(statuses))
            .values(
                status=LeadStatus.PENDING,
                error_message=None,
                retry_count=0,
                connection_requested_at=None,
                connection_accepted_at=None,
                followup_sent_at=None,
                scheduled_at=None,
            )
        )
        await self.session.commit()
        return result.rowcount

    async def list_leads_paginated(
        self,
        campaign_id: str,
        page: int = 1,
        per_page: int = 50,
        status_filter: str | None = None,
        search: str | None = None,
        exclude_removed: bool = False,
        lead_list_id: str | None = None,
    ) -> tuple:
        """Return (leads, total_count) with pagination, optional status filter and search."""
        stmt = select(Lead).where(Lead.campaign_id == campaign_id)
        count_stmt = select(func.count()).select_from(Lead).where(Lead.campaign_id == campaign_id)

        if status_filter:
            stmt = stmt.where(Lead.status == status_filter)
            count_stmt = count_stmt.where(Lead.status == status_filter)
        elif exclude_removed:
            stmt = stmt.where(Lead.status != "REMOVED")
            count_stmt = count_stmt.where(Lead.status != "REMOVED")

        if lead_list_id:
            stmt = stmt.where(Lead.lead_list_id == lead_list_id)
            count_stmt = count_stmt.where(Lead.lead_list_id == lead_list_id)

        if search:
            pattern = f"%{search}%"
            search_filter = or_(
                Lead.first_name.ilike(pattern),
                Lead.last_name.ilike(pattern),
                Lead.company.ilike(pattern),
                Lead.title.ilike(pattern),
                Lead.linkedin_url.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(Lead.created_at).offset((page - 1) * per_page).limit(per_page)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    async def list_action_log(
        self,
        account_id: str | None = None,
        campaign_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[ActionLog]:
        """List action log entries, newest first."""
        stmt = select(ActionLog).order_by(ActionLog.created_at.desc())
        if account_id:
            stmt = stmt.where(ActionLog.account_id == account_id)
        if campaign_id:
            stmt = stmt.where(ActionLog.campaign_id == campaign_id)
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_daily_stats_range(
        self,
        account_id: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyStat]:
        """Get daily stats for an account within a date range."""
        result = await self.session.execute(
            select(DailyStat)
            .where(
                DailyStat.account_id == account_id,
                DailyStat.date >= start_date,
                DailyStat.date <= end_date,
            )
            .order_by(DailyStat.date)
        )
        return result.scalars().all()

    async def bulk_update_lead_status_for_account(
        self,
        account_id: str,
        from_status: LeadStatus,
        to_status: LeadStatus,
    ) -> int:
        """Bulk update leads across all campaigns of an account."""
        # Get all campaign IDs for this account
        camp_result = await self.session.execute(
            select(Campaign.id).where(Campaign.account_id == account_id)
        )
        campaign_ids = [r[0] for r in camp_result.all()]
        if not campaign_ids:
            return 0

        result = await self.session.execute(
            update(Lead)
            .where(Lead.campaign_id.in_(campaign_ids), Lead.status == from_status)
            .values(status=to_status)
        )
        await self.session.commit()
        return result.rowcount

    # ── Withdrawal helpers ──────────────────────────────────────────────

    async def get_leads_by_url(self, account_id: str, linkedin_url: str) -> Sequence[Lead]:
        """Find leads by LinkedIn URL across all campaigns for an account."""
        # Normalize: strip trailing slash for matching
        url_base = linkedin_url.rstrip("/")
        result = await self.session.execute(
            select(Lead)
            .join(Campaign, Lead.campaign_id == Campaign.id)
            .where(
                Campaign.account_id == account_id,
                Lead.linkedin_url.contains(url_base.split("/in/")[-1] if "/in/" in url_base else url_base),
            )
        )
        return result.scalars().all()

    # ── Campaign Stats ─────────────────────────────────────────────────

    async def get_campaign_daily_stats(
        self, campaign_id: str, start_date: Optional[date], end_date: date,
        granularity: str = "day",
    ) -> list:
        """Aggregate stats per campaign from ActionLog + accepted from leads."""
        from sqlalchemy import cast, Date as SADate, case, text

        # ── Sent + errors from action_log ──────────────────────────────────
        if granularity == "hour":
            # SQLite: bucket by hour string
            bucket_expr = func.strftime("%Y-%m-%dT%H:00", ActionLog.created_at).label("bucket")
        else:
            bucket_expr = cast(ActionLog.created_at, SADate).label("bucket")

        sent_q = (
            select(
                bucket_expr,
                func.sum(case(
                    (ActionLog.action_type == ActionType.CONNECTION_REQUEST, 1),
                    else_=0,
                )).label("sent"),
                func.sum(case(
                    (ActionLog.action_type == ActionType.ERROR, 1),
                    else_=0,
                )).label("errors"),
            )
            .where(ActionLog.campaign_id == campaign_id)
            .group_by(text("bucket"))
            .order_by(text("bucket"))
        )
        if start_date:
            sent_q = sent_q.where(cast(ActionLog.created_at, SADate) >= start_date)
        sent_q = sent_q.where(cast(ActionLog.created_at, SADate) <= end_date)

        sent_rows = (await self.session.execute(sent_q)).all()

        # ── Accepted from leads (connection_accepted_at is authoritative) ─
        if granularity == "hour":
            acc_bucket = func.strftime("%Y-%m-%dT%H:00", Lead.connection_accepted_at).label("bucket")
        else:
            acc_bucket = cast(Lead.connection_accepted_at, SADate).label("bucket")

        acc_q = (
            select(acc_bucket, func.count().label("accepted"))
            .where(
                Lead.campaign_id == campaign_id,
                Lead.connection_accepted_at.isnot(None),
            )
            .group_by(text("bucket"))
        )
        if start_date:
            acc_q = acc_q.where(cast(Lead.connection_accepted_at, SADate) >= start_date)
        acc_q = acc_q.where(cast(Lead.connection_accepted_at, SADate) <= end_date)

        acc_map: dict = {row.bucket: row.accepted for row in (await self.session.execute(acc_q)).all()}

        return [
            {
                "date": str(row.bucket),
                "sent": row.sent,
                "accepted": acc_map.get(str(row.bucket), 0),
                "errors": row.errors,
            }
            for row in sent_rows
        ]

    async def get_campaign_acceptance_stats(self, campaign_id: str) -> dict:
        """Get acceptance rate and average time-to-accept for a campaign."""
        # total_sent: count successful CONNECTION_REQUEST actions (more reliable than
        # connection_requested_at which was not always populated in older runs)
        sent_result = await self.session.execute(
            select(func.count())
            .where(
                ActionLog.campaign_id == campaign_id,
                ActionLog.action_type == ActionType.CONNECTION_REQUEST,
                ActionLog.status == ActionLogStatus.SUCCESS,
            )
        )
        total_sent = sent_result.scalar() or 0

        # total_accepted: leads with connection_accepted_at set (authoritative)
        acc_result = await self.session.execute(
            select(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.connection_accepted_at.isnot(None),
            )
        )
        accepted_leads = acc_result.scalars().all()
        total_accepted = len(accepted_leads)

        avg_hours = None
        if accepted_leads:
            deltas = [
                (l.connection_accepted_at - l.connection_requested_at).total_seconds() / 3600
                for l in accepted_leads
                if l.connection_accepted_at and l.connection_requested_at
            ]
            avg_hours = round(sum(deltas) / len(deltas), 1) if deltas else None

        return {
            "total_sent": total_sent,
            "total_accepted": total_accepted,
            "acceptance_rate": round(total_accepted / total_sent * 100, 1) if total_sent > 0 else 0,
            "avg_time_to_accept_hours": avg_hours,
        }

    # ── Lead Lists ────────────────────────────────────────────────────────

    async def create_lead_list(self, name: str, csv_filename: str | None = None) -> LeadList:
        lead_list = LeadList(name=name, csv_filename=csv_filename)
        self.session.add(lead_list)
        await self.session.commit()
        await self.session.refresh(lead_list)
        return lead_list

    async def get_lead_list(self, lead_list_id: str) -> LeadList | None:
        return await self.session.get(LeadList, lead_list_id)

    async def get_lead_list_by_name(self, name: str) -> LeadList | None:
        result = await self.session.execute(
            select(LeadList).where(LeadList.name == name)
        )
        return result.scalar_one_or_none()

    async def list_lead_lists(self, include_archived: bool = False) -> Sequence[LeadList]:
        stmt = select(LeadList).order_by(LeadList.created_at.desc())
        if not include_archived:
            stmt = stmt.where(LeadList.archived == False)  # noqa: E712
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_lead_list(self, lead_list: LeadList, **kwargs) -> LeadList:
        for key, value in kwargs.items():
            setattr(lead_list, key, value)
        await self.session.commit()
        await self.session.refresh(lead_list)
        return lead_list

    async def delete_lead_list(self, lead_list_id: str) -> bool:
        """Delete a lead list and its campaign links. Leaves leads intact (nulls FK)."""
        lead_list = await self.get_lead_list(lead_list_id)
        if not lead_list:
            return False
        # Remove campaign links
        await self.session.execute(
            select(CampaignLeadList).where(
                CampaignLeadList.lead_list_id == lead_list_id
            )
        )
        from sqlalchemy import delete as sa_delete
        await self.session.execute(
            sa_delete(CampaignLeadList).where(
                CampaignLeadList.lead_list_id == lead_list_id
            )
        )
        # Null out lead_list_id on leads
        await self.session.execute(
            update(Lead)
            .where(Lead.lead_list_id == lead_list_id)
            .values(lead_list_id=None)
        )
        await self.session.delete(lead_list)
        await self.session.commit()
        return True

    async def get_list_leads(
        self, lead_list_id: str, page: int = 1, per_page: int = 50
    ) -> tuple:
        """Return (leads, total_count) for a lead list."""
        stmt = select(Lead).where(Lead.lead_list_id == lead_list_id)
        count_stmt = select(func.count()).select_from(Lead).where(
            Lead.lead_list_id == lead_list_id
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Lead.created_at).offset((page - 1) * per_page).limit(per_page)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    async def get_list_lead_urls(self, lead_list_id: str) -> set:
        """Get all linkedin_urls in a lead list (for dedup)."""
        result = await self.session.execute(
            select(Lead.linkedin_url).where(Lead.lead_list_id == lead_list_id)
        )
        return {r[0] for r in result.all()}

    # ── Campaign ↔ Lead List Assignment ───────────────────────────────────

    async def assign_list_to_campaign(
        self, lead_list_id: str, campaign_id: str
    ) -> int:
        """
        Assign a lead list to a campaign: copies leads from the list into the
        campaign (deduplicating by linkedin_url). Returns number of leads added.
        """
        # Check if already assigned
        existing = await self.session.execute(
            select(CampaignLeadList).where(
                CampaignLeadList.campaign_id == campaign_id,
                CampaignLeadList.lead_list_id == lead_list_id,
            )
        )
        if not existing.scalar_one_or_none():
            link = CampaignLeadList(
                campaign_id=campaign_id, lead_list_id=lead_list_id
            )
            self.session.add(link)

        # Get existing URLs in campaign for dedup
        existing_urls_result = await self.session.execute(
            select(Lead.linkedin_url).where(Lead.campaign_id == campaign_id)
        )
        existing_urls = {r[0] for r in existing_urls_result.all()}

        # Get leads from the list
        list_leads_result = await self.session.execute(
            select(Lead).where(Lead.lead_list_id == lead_list_id)
        )
        list_leads = list_leads_result.scalars().all()

        new_leads = []
        for source_lead in list_leads:
            if source_lead.linkedin_url in existing_urls:
                continue
            existing_urls.add(source_lead.linkedin_url)
            new_lead = Lead(
                campaign_id=campaign_id,
                lead_list_id=lead_list_id,
                linkedin_url=source_lead.linkedin_url,
                first_name=source_lead.first_name,
                last_name=source_lead.last_name,
                company=source_lead.company,
                title=source_lead.title,
                extra_data=source_lead.extra_data,
            )
            new_leads.append(new_lead)

        if new_leads:
            self.session.add_all(new_leads)

        await self.session.commit()
        return len(new_leads)

    async def unassign_list_from_campaign(
        self, lead_list_id: str, campaign_id: str
    ) -> int:
        """
        Unassign a lead list from a campaign: marks leads from that list as REMOVED.
        Returns number of leads removed.
        """
        from sqlalchemy import delete as sa_delete

        # Remove the junction link
        await self.session.execute(
            sa_delete(CampaignLeadList).where(
                CampaignLeadList.campaign_id == campaign_id,
                CampaignLeadList.lead_list_id == lead_list_id,
            )
        )

        # Mark leads from this list in this campaign as REMOVED
        result = await self.session.execute(
            update(Lead)
            .where(
                Lead.campaign_id == campaign_id,
                Lead.lead_list_id == lead_list_id,
                Lead.status != LeadStatus.REMOVED,
            )
            .values(status=LeadStatus.REMOVED)
        )
        await self.session.commit()
        return result.rowcount

    async def get_campaign_lists(self, campaign_id: str) -> Sequence[CampaignLeadList]:
        """Get all lead list links for a campaign."""
        result = await self.session.execute(
            select(CampaignLeadList).where(
                CampaignLeadList.campaign_id == campaign_id
            )
        )
        return result.scalars().all()

    async def get_list_campaigns(self, lead_list_id: str) -> Sequence[CampaignLeadList]:
        """Get all campaign links for a lead list."""
        result = await self.session.execute(
            select(CampaignLeadList).where(
                CampaignLeadList.lead_list_id == lead_list_id
            )
        )
        return result.scalars().all()

    # ── Lead Soft Delete / Restore ────────────────────────────────────────

    async def remove_lead(self, lead_id: str) -> Lead | None:
        """Soft delete a lead by setting status to REMOVED."""
        lead = await self.session.get(Lead, lead_id)
        if not lead or lead.status == LeadStatus.REMOVED:
            return lead
        lead.status = LeadStatus.REMOVED
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def restore_lead(self, lead_id: str) -> Lead | None:
        """Restore a removed lead back to PENDING."""
        lead = await self.session.get(Lead, lead_id)
        if not lead or lead.status != LeadStatus.REMOVED:
            return lead
        lead.status = LeadStatus.PENDING
        lead.error_message = None
        lead.retry_count = 0
        lead.connection_requested_at = None
        lead.connection_accepted_at = None
        lead.followup_sent_at = None
        lead.scheduled_at = None
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def skip_lead(self, lead_id: str) -> Lead | None:
        """Skip a lead — valid from PENDING or SCHEDULED."""
        from linauto.campaign.state_machine import validate_transition, InvalidTransition
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        validate_transition(lead.status, LeadStatus.SKIPPED)
        lead.status = LeadStatus.SKIPPED
        lead.scheduled_at = None
        lead.error_message = "skipped_manually"
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def mark_lead_withdrawn_by_slug(self, slug: str) -> bool:
        """
        Mark any CONNECTION_REQUESTED lead whose URL contains slug as WITHDRAWN.
        Returns True if at least one lead was updated.
        """
        result = await self.session.execute(
            select(Lead).where(
                Lead.linkedin_url.contains(slug),
                Lead.status == LeadStatus.CONNECTION_REQUESTED,
            )
        )
        leads = result.scalars().all()
        if not leads:
            return False
        for lead in leads:
            lead.status = LeadStatus.WITHDRAWN
        await self.session.commit()
        return True

    async def requeue_lead(self, lead_id: str) -> Lead | None:
        """Re-queue a lead back to PENDING — valid from ERROR, WITHDRAWN, SKIPPED."""
        from linauto.campaign.state_machine import validate_transition, InvalidTransition
        lead = await self.session.get(Lead, lead_id)
        if not lead:
            return None
        validate_transition(lead.status, LeadStatus.PENDING)
        lead.status = LeadStatus.PENDING
        lead.error_message = None
        lead.retry_count = 0
        lead.scheduled_at = None
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    # ── Global Leads (Lead Library) ───────────────────────────────────────

    async def list_leads_global(
        self,
        page: int = 1,
        per_page: int = 50,
        lead_list_id: str | None = None,
        campaign_id: str | None = None,
        status_filter: str | None = None,
        search: str | None = None,
    ) -> tuple:
        """Return (leads, total_count) across all lists/campaigns."""
        stmt = select(Lead)
        count_stmt = select(func.count()).select_from(Lead)

        if lead_list_id:
            stmt = stmt.where(Lead.lead_list_id == lead_list_id)
            count_stmt = count_stmt.where(Lead.lead_list_id == lead_list_id)

        if campaign_id:
            stmt = stmt.where(Lead.campaign_id == campaign_id)
            count_stmt = count_stmt.where(Lead.campaign_id == campaign_id)

        if status_filter:
            stmt = stmt.where(Lead.status == status_filter)
            count_stmt = count_stmt.where(Lead.status == status_filter)

        if search:
            pattern = f"%{search}%"
            search_filter = or_(
                Lead.first_name.ilike(pattern),
                Lead.last_name.ilike(pattern),
                Lead.company.ilike(pattern),
                Lead.title.ilike(pattern),
                Lead.linkedin_url.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Lead.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

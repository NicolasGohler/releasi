"""Data access layer — CRUD operations for all models."""
from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from linauto.db.models import (
    Account, AccountStatus,
    Campaign, CampaignStatus,
    Lead, LeadStatus,
    ActionLog, ActionType, ActionLogStatus,
    DailyStat,
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

    async def list_accounts(self) -> Sequence[Account]:
        result = await self.session.execute(select(Account).order_by(Account.name))
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

    async def list_campaigns(self, account_id: str | None = None) -> Sequence[Campaign]:
        stmt = select(Campaign).order_by(Campaign.created_at.desc())
        if account_id:
            stmt = stmt.where(Campaign.account_id == account_id)
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
        """Get lead counts grouped by status for a campaign."""
        result = await self.session.execute(
            select(Lead.status, func.count())
            .where(Lead.campaign_id == campaign_id)
            .group_by(Lead.status)
        )
        return {row[0].value: row[1] for row in result.all()}

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

    # ── Scheduler helpers ─────────────────────────────────────────────────

    async def list_active_accounts(self) -> Sequence[Account]:
        """Get all accounts with status=active."""
        result = await self.session.execute(
            select(Account).where(Account.status == AccountStatus.ACTIVE)
        )
        return result.scalars().all()

    async def list_paused_accounts(self) -> Sequence[Account]:
        """Get all accounts with a paused_until set."""
        result = await self.session.execute(
            select(Account).where(Account.paused_until.isnot(None))
        )
        return result.scalars().all()

    async def get_active_campaigns(self, account_id: str) -> Sequence[Campaign]:
        """Get all active campaigns for an account."""
        result = await self.session.execute(
            select(Campaign).where(
                Campaign.account_id == account_id,
                Campaign.status == CampaignStatus.ACTIVE,
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

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
    LeadListMembership, CampaignLeadAssignment,
)


# Phase 1 of the lead-centric refactor: every Lead mutation also writes
# to the new tables. Flip this off to disable dual-write (for emergency
# rollback or running on a DB that doesn't have the new tables yet).
DUAL_WRITE_NEW_SCHEMA = True

# Status fields on Lead that also live on CampaignLeadAssignment. When a
# Lead row is updated with any of these, the matching assignment row gets
# the same value.
_ASSIGNMENT_SYNC_FIELDS = (
    "status",
    "scheduled_at",
    "connection_requested_at",
    "connection_accepted_at",
    "followup_sent_at",
    "error_message",
    "retry_count",
)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Phase 1 dual-write helpers ─────────────────────────────────────────
    # These mirror Lead state to the new tables. Called from every Lead
    # mutation site. Safe no-ops when:
    #   - DUAL_WRITE_NEW_SCHEMA is False (rollback switch)
    #   - The Lead has no campaign_id / lead_list_id (no assignment/
    #     membership to sync)
    #   - The matching new-table row doesn't exist yet (we don't auto-create
    #     here — that's the backfill script's job for legacy rows, and the
    #     explicit creation sites' job for new rows)

    async def _sync_assignment_from_lead(self, lead: Lead) -> None:
        """Mirror a Lead's per-campaign state to its CampaignLeadAssignment.

        UPSERT-ish: if the assignment row exists, UPDATE its sync fields.
        If it doesn't exist (e.g. brand-new Lead created in this transaction),
        INSERT a fresh one mirroring the Lead's full state.
        """
        if not DUAL_WRITE_NEW_SCHEMA or lead.campaign_id is None:
            return

        existing = await self.session.execute(
            select(CampaignLeadAssignment).where(
                CampaignLeadAssignment.lead_id == lead.id,
                CampaignLeadAssignment.campaign_id == lead.campaign_id,
            )
        )
        assignment = existing.scalar_one_or_none()

        if assignment is None:
            assignment = CampaignLeadAssignment(
                lead_id=lead.id,
                campaign_id=lead.campaign_id,
                lead_list_id=lead.lead_list_id,
                status=lead.status.value if hasattr(lead.status, "value") else str(lead.status).lower(),
                scheduled_at=lead.scheduled_at,
                connection_requested_at=lead.connection_requested_at,
                connection_accepted_at=lead.connection_accepted_at,
                followup_sent_at=lead.followup_sent_at,
                error_message=lead.error_message,
                retry_count=lead.retry_count or 0,
            )
            self.session.add(assignment)
        else:
            assignment.status = (
                lead.status.value if hasattr(lead.status, "value") else str(lead.status).lower()
            )
            assignment.scheduled_at = lead.scheduled_at
            assignment.connection_requested_at = lead.connection_requested_at
            assignment.connection_accepted_at = lead.connection_accepted_at
            assignment.followup_sent_at = lead.followup_sent_at
            assignment.error_message = lead.error_message
            assignment.retry_count = lead.retry_count or 0
            # Update lead_list_id if it changed on the Lead (rare)
            if lead.lead_list_id and assignment.lead_list_id != lead.lead_list_id:
                assignment.lead_list_id = lead.lead_list_id

    async def _sync_membership_from_lead(self, lead: Lead) -> None:
        """Ensure a LeadListMembership row exists for the Lead's list.

        Membership rows are write-once: the lead either is or isn't in the
        list. Idempotent — re-runs are no-ops.
        """
        if not DUAL_WRITE_NEW_SCHEMA or lead.lead_list_id is None:
            return

        existing = await self.session.execute(
            select(LeadListMembership.id).where(
                LeadListMembership.lead_id == lead.id,
                LeadListMembership.lead_list_id == lead.lead_list_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return

        membership = LeadListMembership(
            lead_id=lead.id,
            lead_list_id=lead.lead_list_id,
        )
        self.session.add(membership)

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
        # Flush to populate generated Lead.id values before dual-writing
        # the membership + assignment rows.
        await self.session.flush()
        if DUAL_WRITE_NEW_SCHEMA:
            for lead in leads:
                await self._sync_assignment_from_lead(lead)
                await self._sync_membership_from_lead(lead)
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

    async def get_leads_by_status_via_assignments(
        self, campaign_id: str, status: LeadStatus
    ) -> Sequence[Lead]:
        """Phase 2 read cutover mirror of `get_leads_by_status`.

        Filters via CampaignLeadAssignment (the source of truth in the new
        schema) but returns Lead ORM instances so callers that mutate the
        Lead via `update_lead()` still work without changes. Since dual-
        write keeps both tables in sync, the resulting Lead set is
        identical to the legacy query for the same (campaign_id, status).

        The status comparison uses the lowercase enum value because
        CampaignLeadAssignment.status is stored as a plain string.
        """
        status_str = status.value if hasattr(status, "value") else str(status).lower()
        result = await self.session.execute(
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == status_str,
            )
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
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(
                    CampaignLeadAssignment.campaign_id == campaign_id,
                    CampaignLeadAssignment.status == LeadStatus.SCHEDULED.value,
                    CampaignLeadAssignment.scheduled_at < now,
                )
                .values(status=LeadStatus.PENDING.value, scheduled_at=None)
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

    async def get_followup_due_leads_via_assignments(
        self, campaign_id: str, before: datetime
    ) -> Sequence[Lead]:
        """Phase 2 read cutover mirror of `get_followup_due_leads`.

        Filters via CampaignLeadAssignment instead of Lead.campaign_id /
        Lead.status, but returns Lead ORM instances so all mutation sites
        (update_lead, etc.) work without changes.
        """
        status_str = LeadStatus.FOLLOWUP_SCHEDULED.value
        result = await self.session.execute(
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == status_str,
                CampaignLeadAssignment.scheduled_at <= before,
            )
            .order_by(CampaignLeadAssignment.scheduled_at)
        )
        return result.scalars().all()

    async def get_pending_leads_via_assignments(
        self, campaign_id: str, limit: Optional[int] = None
    ) -> Sequence[Lead]:
        """Phase 2 read cutover mirror of `get_pending_leads`.

        Filters via CampaignLeadAssignment. Only used by daily_planning_sweep
        (planned-mode accounts), which is currently a no-op in prod (all
        accounts are continuous). Included here so Phase 3 can drop the
        legacy path uniformly.
        """
        status_str = LeadStatus.PENDING.value
        stmt = (
            select(Lead)
            .join(CampaignLeadAssignment, CampaignLeadAssignment.lead_id == Lead.id)
            .where(
                CampaignLeadAssignment.campaign_id == campaign_id,
                CampaignLeadAssignment.status == status_str,
            )
            .order_by(Lead.created_at)
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
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
        # Dual-write: mirror state changes to the assignment row and ensure
        # membership exists. Both are no-ops if the lead has no campaign/list
        # or DUAL_WRITE_NEW_SCHEMA is False.
        await self._sync_assignment_from_lead(lead)
        await self._sync_membership_from_lead(lead)
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

    async def get_last_successful_followup_index(self, lead_id: str) -> int:
        """Return the highest message_index (1-based) successfully sent for this lead.

        Returns 0 if no successful FOLLOWUP_MESSAGE log exists. Used to resume
        a partial sequence: if message 1 sent+logged but message 2 failed, the
        next run skips index 0 and retries from index 1.
        """
        result = await self.session.execute(
            select(ActionLog.details)
            .where(
                ActionLog.lead_id == lead_id,
                ActionLog.action_type == ActionType.FOLLOWUP_MESSAGE,
                ActionLog.status == ActionLogStatus.SUCCESS,
            )
        )
        rows = result.scalars().all()
        if not rows:
            return 0
        return max((r.get("message_index", 0) for r in rows if r), default=0)

    async def get_followup_sequences_started_today(self, account_id: str) -> int:
        """Count distinct leads with at least one successful FOLLOWUP_MESSAGE today (UTC).

        Used as the daily cap check — counts sequences started, not individual
        messages sent, so multi-message sequences don't eat double the budget.
        """
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(func.count(func.distinct(ActionLog.lead_id)))
            .where(
                ActionLog.account_id == account_id,
                ActionLog.action_type == ActionType.FOLLOWUP_MESSAGE,
                ActionLog.status == ActionLogStatus.SUCCESS,
                ActionLog.created_at >= today_start,
            )
        )
        return result.scalar_one() or 0

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
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(CampaignLeadAssignment.lead_id == lead_id)
                .values(scheduled_at=scheduled_at, status=new_status.value)
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
        # Dual-write the same change to the assignment table. String values
        # because CampaignLeadAssignment.status is VARCHAR not Enum.
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(
                    CampaignLeadAssignment.campaign_id == campaign_id,
                    CampaignLeadAssignment.status == from_status.value,
                )
                .values(status=to_status.value)
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
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(
                    CampaignLeadAssignment.campaign_id == campaign_id,
                    CampaignLeadAssignment.status.in_([s.value for s in statuses]),
                )
                .values(
                    status=LeadStatus.PENDING.value,
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
        sort_by: str | None = None,
        sort_dir: str = "asc",
        requested_after: str | None = None,
        requested_before: str | None = None,
        skip_reason: str | None = None,
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

        if requested_after:
            stmt = stmt.where(Lead.connection_requested_at >= requested_after)
            count_stmt = count_stmt.where(Lead.connection_requested_at >= requested_after)
        if requested_before:
            stmt = stmt.where(Lead.connection_requested_at <= requested_before)
            count_stmt = count_stmt.where(Lead.connection_requested_at <= requested_before)

        if skip_reason:
            stmt = stmt.where(Lead.error_message == skip_reason)
            count_stmt = count_stmt.where(Lead.error_message == skip_reason)

        total = (await self.session.execute(count_stmt)).scalar_one()

        _SORT_COLS = {
            "name": Lead.first_name,
            "company": Lead.company,
            "status": Lead.status,
            "requested_at": Lead.connection_requested_at,
            "created_at": Lead.created_at,
        }
        col = _SORT_COLS.get(sort_by or "created_at", Lead.created_at)
        order_col = col.desc() if sort_dir == "desc" else col.asc()
        stmt = stmt.order_by(order_col).offset((page - 1) * per_page).limit(per_page)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    # ── Phase 2 read cutover: campaign-scoped lead listing via assignments ──
    # Same filters and sort options as `list_leads_paginated`, but the
    # primary table is `campaign_lead_assignments` joined to `leads` for
    # identity fields. Returns objects that quack like Lead rows for the
    # consumer (status/timestamps come from the assignment side).

    async def list_leads_via_assignments_paginated(
        self,
        campaign_id: str,
        page: int = 1,
        per_page: int = 50,
        status_filter: str | None = None,
        search: str | None = None,
        exclude_removed: bool = False,
        lead_list_id: str | None = None,
        sort_by: str | None = None,
        sort_dir: str = "asc",
        requested_after: str | None = None,
        requested_before: str | None = None,
        skip_reason: str | None = None,
    ) -> tuple:
        """Mirror of list_leads_paginated reading from CampaignLeadAssignment.

        The result row shape matches what the export consumer expects —
        Lead identity fields plus assignment-derived state fields. Returns
        a list of duck-typed objects with the same attribute names as Lead.
        """
        # Inline class so we don't add a public type for what's currently a
        # transitional shape. Once Phase 3 lands and the legacy path is gone,
        # we can fold this into a proper return type.
        class _LeadView:
            __slots__ = (
                "id", "linkedin_url", "first_name", "last_name", "company",
                "title", "email", "phone", "extra_data", "campaign_id",
                "lead_list_id", "created_at",
                # State fields sourced from the assignment row
                "status", "scheduled_at",
                "connection_requested_at", "connection_accepted_at",
                "followup_sent_at", "error_message", "retry_count",
            )

            def __init__(self, **kwargs):
                for k in self.__slots__:
                    setattr(self, k, kwargs.get(k))

        # Status comparisons need to bridge the enum/string gap. Legacy
        # `leads.status` stores upper-case (e.g. "CONNECTION_REQUESTED")
        # while assignments stores lower-case ("connection_requested").
        # We normalise inputs to lower-case for the assignment table.

        stmt = (
            select(
                CampaignLeadAssignment.id.label("assignment_id"),
                CampaignLeadAssignment.status,
                CampaignLeadAssignment.scheduled_at,
                CampaignLeadAssignment.connection_requested_at,
                CampaignLeadAssignment.connection_accepted_at,
                CampaignLeadAssignment.followup_sent_at,
                CampaignLeadAssignment.error_message,
                CampaignLeadAssignment.retry_count,
                CampaignLeadAssignment.lead_list_id.label("asgn_lead_list_id"),
                Lead.id,
                Lead.linkedin_url,
                Lead.first_name,
                Lead.last_name,
                Lead.company,
                Lead.title,
                Lead.email,
                Lead.phone,
                Lead.extra_data,
                Lead.campaign_id,
                Lead.lead_list_id,
                Lead.created_at,
            )
            .join(Lead, Lead.id == CampaignLeadAssignment.lead_id)
            .where(CampaignLeadAssignment.campaign_id == campaign_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(CampaignLeadAssignment)
            .join(Lead, Lead.id == CampaignLeadAssignment.lead_id)
            .where(CampaignLeadAssignment.campaign_id == campaign_id)
        )

        if status_filter:
            sf = status_filter.lower()
            stmt = stmt.where(CampaignLeadAssignment.status == sf)
            count_stmt = count_stmt.where(CampaignLeadAssignment.status == sf)
        elif exclude_removed:
            stmt = stmt.where(CampaignLeadAssignment.status != "removed")
            count_stmt = count_stmt.where(CampaignLeadAssignment.status != "removed")

        if lead_list_id:
            stmt = stmt.where(CampaignLeadAssignment.lead_list_id == lead_list_id)
            count_stmt = count_stmt.where(
                CampaignLeadAssignment.lead_list_id == lead_list_id
            )

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

        if requested_after:
            stmt = stmt.where(
                CampaignLeadAssignment.connection_requested_at >= requested_after
            )
            count_stmt = count_stmt.where(
                CampaignLeadAssignment.connection_requested_at >= requested_after
            )
        if requested_before:
            stmt = stmt.where(
                CampaignLeadAssignment.connection_requested_at <= requested_before
            )
            count_stmt = count_stmt.where(
                CampaignLeadAssignment.connection_requested_at <= requested_before
            )

        if skip_reason:
            stmt = stmt.where(CampaignLeadAssignment.error_message == skip_reason)
            count_stmt = count_stmt.where(
                CampaignLeadAssignment.error_message == skip_reason
            )

        total = (await self.session.execute(count_stmt)).scalar_one()

        _SORT_COLS = {
            "name": Lead.first_name,
            "company": Lead.company,
            "status": CampaignLeadAssignment.status,
            "requested_at": CampaignLeadAssignment.connection_requested_at,
            "created_at": Lead.created_at,
        }
        col = _SORT_COLS.get(sort_by or "created_at", Lead.created_at)
        order_col = col.desc() if sort_dir == "desc" else col.asc()
        stmt = stmt.order_by(order_col).offset((page - 1) * per_page).limit(per_page)

        result = await self.session.execute(stmt)
        rows = result.all()
        views = []
        for row in rows:
            m = row._mapping
            views.append(_LeadView(
                id=m["id"],
                linkedin_url=m["linkedin_url"],
                first_name=m["first_name"],
                last_name=m["last_name"],
                company=m["company"],
                title=m["title"],
                email=m["email"],
                phone=m["phone"],
                extra_data=m["extra_data"],
                campaign_id=m["campaign_id"],
                # Prefer the assignment's lead_list_id (which lead-list
                # brought this lead into THIS campaign); fall back to Lead's.
                lead_list_id=m["asgn_lead_list_id"] or m["lead_list_id"],
                created_at=m["created_at"],
                status=m["status"],  # lower-case from assignment
                scheduled_at=m["scheduled_at"],
                connection_requested_at=m["connection_requested_at"],
                connection_accepted_at=m["connection_accepted_at"],
                followup_sent_at=m["followup_sent_at"],
                error_message=m["error_message"],
                retry_count=m["retry_count"],
            ))
        return views, total

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
            # Flush so we have lead.id values for the dual-write step.
            await self.session.flush()
            # Dual-write: each new Lead row gets a matching assignment
            # (campaign_id is set) and membership (lead_list_id is set).
            for nl in new_leads:
                await self._sync_assignment_from_lead(nl)
                await self._sync_membership_from_lead(nl)

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
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(
                    CampaignLeadAssignment.campaign_id == campaign_id,
                    CampaignLeadAssignment.lead_list_id == lead_list_id,
                    CampaignLeadAssignment.status != LeadStatus.REMOVED.value,
                )
                .values(status=LeadStatus.REMOVED.value)
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
        await self._sync_assignment_from_lead(lead)
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
        await self._sync_assignment_from_lead(lead)
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
        await self._sync_assignment_from_lead(lead)
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
            await self._sync_assignment_from_lead(lead)
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
        await self._sync_assignment_from_lead(lead)
        await self.session.commit()
        await self.session.refresh(lead)
        return lead

    async def bulk_skip_leads(self, lead_ids: list) -> int:
        """Skip multiple leads (PENDING/SCHEDULED → SKIPPED). Returns count updated."""
        result = await self.session.execute(
            update(Lead)
            .where(Lead.id.in_(lead_ids), Lead.status.in_(["pending", "scheduled"]))
            .values(status=LeadStatus.SKIPPED, error_message="skipped_manually")
        )
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(
                    CampaignLeadAssignment.lead_id.in_(lead_ids),
                    CampaignLeadAssignment.status.in_(["pending", "scheduled"]),
                )
                .values(status=LeadStatus.SKIPPED.value, error_message="skipped_manually")
            )
        await self.session.commit()
        return result.rowcount

    async def bulk_remove_leads(self, lead_ids: list) -> int:
        """Soft-delete multiple leads. Returns count updated."""
        result = await self.session.execute(
            update(Lead)
            .where(Lead.id.in_(lead_ids), Lead.status != LeadStatus.REMOVED)
            .values(status=LeadStatus.REMOVED)
        )
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(
                    CampaignLeadAssignment.lead_id.in_(lead_ids),
                    CampaignLeadAssignment.status != LeadStatus.REMOVED.value,
                )
                .values(status=LeadStatus.REMOVED.value)
            )
        await self.session.commit()
        return result.rowcount

    async def bulk_requeue_leads(self, lead_ids: list) -> int:
        """Re-queue multiple leads (ERROR/WITHDRAWN/SKIPPED → PENDING). Returns count updated."""
        result = await self.session.execute(
            update(Lead)
            .where(Lead.id.in_(lead_ids), Lead.status.in_(["error", "withdrawn", "skipped"]))
            .values(status=LeadStatus.PENDING, error_message=None, retry_count=0, scheduled_at=None)
        )
        if DUAL_WRITE_NEW_SCHEMA:
            await self.session.execute(
                update(CampaignLeadAssignment)
                .where(
                    CampaignLeadAssignment.lead_id.in_(lead_ids),
                    CampaignLeadAssignment.status.in_(["error", "withdrawn", "skipped"]),
                )
                .values(
                    status=LeadStatus.PENDING.value,
                    error_message=None, retry_count=0, scheduled_at=None,
                )
            )
        await self.session.commit()
        return result.rowcount

    # ── Global Leads (Lead Library) ───────────────────────────────────────

    async def list_leads_global(
        self,
        page: int = 1,
        per_page: int = 50,
        lead_list_id: str | None = None,
        campaign_id: str | None = None,
        status_filter: str | None = None,
        search: str | None = None,
        sort_by: str | None = None,
        sort_dir: str = "desc",
        requested_after: str | None = None,
        requested_before: str | None = None,
        skip_reason: str | None = None,
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

        if requested_after:
            stmt = stmt.where(Lead.connection_requested_at >= requested_after)
            count_stmt = count_stmt.where(Lead.connection_requested_at >= requested_after)
        if requested_before:
            stmt = stmt.where(Lead.connection_requested_at <= requested_before)
            count_stmt = count_stmt.where(Lead.connection_requested_at <= requested_before)

        if skip_reason:
            stmt = stmt.where(Lead.error_message == skip_reason)
            count_stmt = count_stmt.where(Lead.error_message == skip_reason)

        total = (await self.session.execute(count_stmt)).scalar_one()

        _SORT_COLS = {
            "name": Lead.first_name,
            "company": Lead.company,
            "status": Lead.status,
            "requested_at": Lead.connection_requested_at,
            "created_at": Lead.created_at,
        }
        col = _SORT_COLS.get(sort_by or "created_at", Lead.created_at)
        order_col = col.desc() if sort_dir == "desc" else col.asc()
        stmt = stmt.order_by(order_col).offset((page - 1) * per_page).limit(per_page)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

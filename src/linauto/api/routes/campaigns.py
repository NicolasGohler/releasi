"""Campaign endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import CampaignOut, CampaignCreate, CampaignUpdate, CampaignStatsResponse
from linauto.db.models import CampaignStatus
from linauto.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


async def _enrich_campaign(repo: Repository, campaign) -> CampaignOut:
    """Convert a Campaign ORM object to CampaignOut with status counts."""
    out = CampaignOut.model_validate(campaign)
    out.status_counts = await repo.get_campaign_status_counts(campaign.id)
    # Add account name
    account = await repo.get_account(campaign.account_id)
    if account:
        out.account_name = account.name
        out.account_status = account.status.value if hasattr(account.status, 'value') else account.status
        out.account_paused_until = account.paused_until
        out.account_timezone = account.timezone
    # Add assigned lead lists
    links = await repo.get_campaign_lists(campaign.id)
    assigned = []
    for link in links:
        ll = await repo.get_lead_list(link.lead_list_id)
        if ll:
            sc = await repo.get_list_status_counts_for_campaign(ll.id, campaign.id)
            assigned.append({"id": ll.id, "name": ll.name, "total_leads": ll.total_leads, "status_counts": sc})
    out.assigned_lists = assigned
    return out


@router.get("/campaigns", response_model=List[CampaignOut])
async def list_campaigns(
    account_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    campaigns = await repo.list_campaigns(account_id=account_id, include_archived=include_archived)
    if status:
        campaigns = [c for c in campaigns if c.status.value == status]
    return [await _enrich_campaign(repo, c) for c in campaigns]


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
async def create_campaign(body: CampaignCreate, repo: Repository = Depends(get_repo)):
    account = await repo.get_account(body.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    campaign = await repo.create_campaign(
        account_id=body.account_id,
        name=body.name,
        connection_message_template=body.connection_message_template,
        followup_message_template=body.followup_message_template,
        followup_delay_hours=body.followup_delay_hours,
        filter_no_photo=body.filter_no_photo,
        filter_min_connections=body.filter_min_connections,
    )
    return await _enrich_campaign(repo, campaign)


@router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    updates = body.model_dump(exclude_unset=True)
    if updates:
        campaign = await repo.update_campaign(campaign, **updates)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/activate", response_model=CampaignOut)
async def activate_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    from datetime import datetime as _datetime, timedelta as _timedelta
    from linauto.db.models import LeadStatus
    from linauto.scheduler.planner import generate_daily_plan, SlotType

    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign = await repo.update_campaign(campaign, status=CampaignStatus.ACTIVE)

    # Trigger a fresh plan on resume so leads are scheduled immediately
    # rather than waiting until 06:00 the next morning.
    account = await repo.get_account(campaign.account_id)
    if account and not (account.paused_until and account.paused_until > _datetime.utcnow()):
        # Reset any stale scheduled leads back to pending first
        await repo.reset_stale_scheduled_leads(campaign.id)

        now = _datetime.utcnow()
        sent_today = await repo.get_daily_requests_sent(account.id)
        future_scheduled = await repo.count_future_scheduled_leads(campaign.id)
        remaining_budget = max(0, account.daily_limit - sent_today - future_scheduled)

        if remaining_budget > 0:
            pending = await repo.get_pending_leads(campaign.id)
            if pending:
                plan = generate_daily_plan(
                    account_id=account.id,
                    day=now.date(),
                    pending_lead_ids=[l.id for l in pending],
                    daily_limit=account.daily_limit,
                    timezone_str=account.timezone,
                    campaign_weekend_enabled=campaign.weekend_enabled,
                    effective_start=now + _timedelta(minutes=2),
                    remaining_budget=remaining_budget,
                )
                for slot in plan:
                    if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                        if slot.scheduled_at > now:
                            await repo.update_lead_schedule(
                                slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED
                            )

    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign = await repo.update_campaign(campaign, status=CampaignStatus.PAUSED)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/reset-leads")
async def reset_leads(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    count = await repo.reset_campaign_leads(campaign_id)
    return {"reset_count": count}


@router.post("/campaigns/{campaign_id}/archive", response_model=CampaignOut)
async def archive_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign = await repo.update_campaign(campaign, archived=True, status=CampaignStatus.PAUSED)
    return await _enrich_campaign(repo, campaign)


@router.post("/campaigns/{campaign_id}/unarchive", response_model=CampaignOut)
async def unarchive_campaign(campaign_id: str, repo: Repository = Depends(get_repo)):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign = await repo.update_campaign(campaign, archived=False)
    return await _enrich_campaign(repo, campaign)


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStatsResponse)
async def campaign_stats(
    campaign_id: str,
    days: int = Query(30, ge=0, le=3650),
    granularity: str = Query("day", regex="^(day|hour)$"),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    start = (date.today() - timedelta(days=days)) if days > 0 else None
    daily = await repo.get_campaign_daily_stats(campaign_id, start, date.today(), granularity)
    summary = await repo.get_campaign_acceptance_stats(campaign_id)
    return {"daily": daily, "summary": summary}

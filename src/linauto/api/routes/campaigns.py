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
            assigned.append({"id": ll.id, "name": ll.name, "total_leads": ll.total_leads})
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
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign = await repo.update_campaign(campaign, status=CampaignStatus.ACTIVE)
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
    days: int = Query(30, le=90),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    start = date.today() - timedelta(days=days)
    daily = await repo.get_campaign_daily_stats(campaign_id, start, date.today())
    summary = await repo.get_campaign_acceptance_stats(campaign_id)
    return {"daily": daily, "summary": summary}

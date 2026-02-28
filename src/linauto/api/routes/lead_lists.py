"""Lead list endpoints — CRUD, CSV upload, assign/unassign to campaigns."""
from __future__ import annotations

import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import (
    LeadListOut,
    LeadListCreate,
    LeadListDetail,
    AssignListRequest,
    ImportResponse,
    LeadOut,
    LeadPage,
)
from linauto.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


async def _enrich_lead_list(repo: Repository, lead_list) -> LeadListOut:
    """Add campaign_count to a LeadList."""
    out = LeadListOut.model_validate(lead_list)
    links = await repo.get_list_campaigns(lead_list.id)
    out.campaign_count = len(links)
    return out


@router.get("/lead-lists", response_model=list[LeadListOut])
async def list_lead_lists(repo: Repository = Depends(get_repo)):
    lists = await repo.list_lead_lists()
    return [await _enrich_lead_list(repo, ll) for ll in lists]


@router.get("/lead-lists/{lead_list_id}", response_model=LeadListDetail)
async def get_lead_list(lead_list_id: str, repo: Repository = Depends(get_repo)):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    links = await repo.get_list_campaigns(lead_list_id)
    campaigns = []
    for link in links:
        campaign = await repo.get_campaign(link.campaign_id)
        if campaign:
            campaigns.append({"id": campaign.id, "name": campaign.name})

    out = LeadListDetail.model_validate(ll)
    out.campaign_count = len(campaigns)
    out.campaigns = campaigns
    return out


@router.post("/lead-lists", response_model=LeadListOut, status_code=201)
async def create_lead_list(
    body: LeadListCreate,
    repo: Repository = Depends(get_repo),
):
    existing = await repo.get_lead_list_by_name(body.name)
    if existing:
        raise HTTPException(status_code=409, detail="Lead list name already exists")
    ll = await repo.create_lead_list(name=body.name)
    return await _enrich_lead_list(repo, ll)


@router.delete("/lead-lists/{lead_list_id}")
async def delete_lead_list(lead_list_id: str, repo: Repository = Depends(get_repo)):
    deleted = await repo.delete_lead_list(lead_list_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Lead list not found")
    return {"ok": True}


@router.post("/lead-lists/{lead_list_id}/import", response_model=ImportResponse)
async def import_csv_to_list(
    lead_list_id: str,
    file: UploadFile = File(...),
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    # Save uploaded file to temp location
    contents = await file.read()
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    # Get existing URLs in the list for dedup
    existing_urls = await repo.get_list_lead_urls(lead_list_id)

    # Parse CSV — leads created with lead_list_id (no campaign_id yet)
    from linauto.campaign.importer import parse_csv
    leads, result = parse_csv(tmp_path, lead_list_id=lead_list_id, existing_urls=existing_urls)

    if leads:
        count = await repo.bulk_create_leads(leads)
        await repo.update_lead_list(ll, total_leads=ll.total_leads + count)
        # Update csv_filename if not set
        if not ll.csv_filename and file.filename:
            await repo.update_lead_list(ll, csv_filename=file.filename)

    import os
    try:
        os.unlink(tmp_path)
    except OSError:
        pass

    return ImportResponse(
        total_rows=result.total_rows,
        imported=result.imported,
        duplicates_skipped=result.duplicates_skipped,
        no_url_skipped=result.no_url_skipped,
        errors=result.errors,
    )


@router.get("/lead-lists/{lead_list_id}/leads", response_model=LeadPage)
async def list_leads_in_list(
    lead_list_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    leads, total = await repo.get_list_leads(lead_list_id, page=page, per_page=per_page)
    return LeadPage(
        items=[LeadOut.model_validate(l) for l in leads],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/lead-lists/{lead_list_id}/assign")
async def assign_list_to_campaign(
    lead_list_id: str,
    body: AssignListRequest,
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    campaign = await repo.get_campaign(body.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    count = await repo.assign_list_to_campaign(lead_list_id, body.campaign_id)
    # Update campaign total_leads
    await repo.update_campaign(campaign, total_leads=campaign.total_leads + count)
    return {"leads_added": count}


@router.post("/lead-lists/{lead_list_id}/unassign")
async def unassign_list_from_campaign(
    lead_list_id: str,
    body: AssignListRequest,
    repo: Repository = Depends(get_repo),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    campaign = await repo.get_campaign(body.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    count = await repo.unassign_list_from_campaign(lead_list_id, body.campaign_id)
    return {"leads_removed": count}

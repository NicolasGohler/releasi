"""Lead endpoints — paginated list, CSV upload, soft delete, restore, global library."""
from __future__ import annotations

import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import LeadOut, LeadPage, ImportResponse
from linauto.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/campaigns/{campaign_id}/leads", response_model=LeadPage)
async def list_leads(
    campaign_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    exclude_removed: bool = Query(False),
    lead_list_id: Optional[str] = Query(None),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    leads, total = await repo.list_leads_paginated(
        campaign_id=campaign_id,
        page=page,
        per_page=per_page,
        status_filter=status,
        search=search,
        exclude_removed=exclude_removed,
        lead_list_id=lead_list_id,
    )
    return LeadPage(
        items=[LeadOut.model_validate(l) for l in leads],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/campaigns/{campaign_id}/leads/export")
async def export_campaign_leads_csv(
    campaign_id: str,
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    exclude_removed: bool = Query(False),
    lead_list_id: Optional[str] = Query(None),
    repo: Repository = Depends(get_repo),
):
    """Download filtered campaign leads as a CSV file.

    Applies the same filters as the list endpoint (status, search, exclude_removed,
    lead_list_id). Includes status and timestamps so users can slice by outcome
    outside the app.
    """
    import csv
    import io
    from fastapi.responses import StreamingResponse

    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    leads, _ = await repo.list_leads_paginated(
        campaign_id=campaign_id,
        page=1,
        per_page=100000,
        status_filter=status,
        search=search,
        exclude_removed=exclude_removed,
        lead_list_id=lead_list_id,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "linkedin_url", "first_name", "last_name", "company", "title",
        "email", "phone",
        "status", "connection_requested_at", "connection_accepted_at",
        "followup_sent_at", "error_message", "created_at",
    ])
    for lead in leads:
        writer.writerow([
            lead.linkedin_url,
            lead.first_name or "",
            lead.last_name or "",
            lead.company or "",
            lead.title or "",
            lead.email or "",
            lead.phone or "",
            lead.status or "",
            lead.connection_requested_at.isoformat() if lead.connection_requested_at else "",
            lead.connection_accepted_at.isoformat() if lead.connection_accepted_at else "",
            lead.followup_sent_at.isoformat() if lead.followup_sent_at else "",
            lead.error_message or "",
            lead.created_at.isoformat() if lead.created_at else "",
        ])

    buf.seek(0)
    safe_name = (campaign.name or "campaign").replace(" ", "_").replace("/", "_")
    filename = f"{safe_name}_leads.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/campaigns/{campaign_id}/import", response_model=ImportResponse)
async def import_csv(
    campaign_id: str,
    file: UploadFile = File(...),
    list_name: Optional[str] = Form(None),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Auto-create a lead list if list_name provided
    lead_list = None
    lead_list_id = None
    if list_name:
        existing_list = await repo.get_lead_list_by_name(list_name)
        if existing_list:
            raise HTTPException(status_code=409, detail="Lead list name already exists")
        lead_list = await repo.create_lead_list(
            name=list_name, csv_filename=file.filename
        )
        lead_list_id = lead_list.id

    # Save uploaded file to temp location
    contents = await file.read()
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    # Get existing URLs for dedup
    existing_leads, _ = await repo.list_leads_paginated(
        campaign_id=campaign_id, page=1, per_page=100000
    )
    existing_urls = {l.linkedin_url for l in existing_leads}

    # Parse CSV using existing importer
    from linauto.campaign.importer import parse_csv
    leads, result = parse_csv(
        tmp_path,
        campaign_id=campaign_id,
        lead_list_id=lead_list_id,
        existing_urls=existing_urls,
    )

    if leads:
        count = await repo.bulk_create_leads(leads)
        # Update campaign total
        await repo.update_campaign(
            campaign, total_leads=campaign.total_leads + count
        )
        # Update lead list total and create campaign link
        if lead_list:
            await repo.update_lead_list(lead_list, total_leads=count)
            from linauto.db.models import CampaignLeadList
            link = CampaignLeadList(
                campaign_id=campaign_id, lead_list_id=lead_list_id
            )
            repo.session.add(link)
            await repo.session.commit()

    # Clean up temp file
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


# ── Global Lead Library ───────────────────────────────────────────────

@router.get("/leads", response_model=LeadPage)
async def list_leads_global(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    lead_list_id: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    repo: Repository = Depends(get_repo),
):
    leads, total = await repo.list_leads_global(
        page=page,
        per_page=per_page,
        lead_list_id=lead_list_id,
        campaign_id=campaign_id,
        status_filter=status,
        search=search,
    )

    # Enrich with campaign names
    campaign_ids = {l.campaign_id for l in leads if l.campaign_id}
    campaign_names = {}
    for cid in campaign_ids:
        c = await repo.get_campaign(cid)
        if c:
            campaign_names[cid] = c.name

    items = []
    for l in leads:
        out = LeadOut.model_validate(l)
        if l.campaign_id and l.campaign_id in campaign_names:
            out.campaign_name = campaign_names[l.campaign_id]
        items.append(out)

    return LeadPage(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


# ── Soft Delete / Restore ────────────────────────────────────────────

@router.delete("/leads/{lead_id}", response_model=LeadOut)
async def remove_lead(lead_id: str, repo: Repository = Depends(get_repo)):
    lead = await repo.remove_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadOut.model_validate(lead)


@router.post("/leads/{lead_id}/restore", response_model=LeadOut)
async def restore_lead(lead_id: str, repo: Repository = Depends(get_repo)):
    lead = await repo.restore_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found or not removed")
    return LeadOut.model_validate(lead)


@router.post("/leads/{lead_id}/skip", response_model=LeadOut)
async def skip_lead(lead_id: str, repo: Repository = Depends(get_repo)):
    from linauto.campaign.state_machine import InvalidTransition
    try:
        lead = await repo.skip_lead(lead_id)
    except InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadOut.model_validate(lead)


@router.post("/leads/{lead_id}/requeue", response_model=LeadOut)
async def requeue_lead(lead_id: str, repo: Repository = Depends(get_repo)):
    from linauto.campaign.state_machine import InvalidTransition
    try:
        lead = await repo.requeue_lead(lead_id)
    except InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadOut.model_validate(lead)

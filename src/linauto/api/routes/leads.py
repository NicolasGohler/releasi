"""Lead endpoints — paginated list and CSV upload."""
from __future__ import annotations

import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File

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
    )
    return LeadPage(
        items=[LeadOut.model_validate(l) for l in leads],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/campaigns/{campaign_id}/import", response_model=ImportResponse)
async def import_csv(
    campaign_id: str,
    file: UploadFile = File(...),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

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
    leads, result = parse_csv(tmp_path, campaign_id, existing_urls)

    if leads:
        count = await repo.bulk_create_leads(leads)
        # Update campaign total
        await repo.update_campaign(
            campaign, total_leads=campaign.total_leads + count
        )

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

"""Lead list endpoints — CRUD, CSV upload, assign/unassign to campaigns."""
from __future__ import annotations

import tempfile
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File  # noqa: F401
from pydantic import BaseModel

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

logger = structlog.get_logger()
router = APIRouter(dependencies=[Depends(require_api_key)])

# ── Event import job tracking (in-memory) ────────────────────────────────
# Maps lead_list_id → job state. Single-process FastAPI — safe for our use case.
_scrape_jobs: dict[str, dict] = {}


class EventImportRequest(BaseModel):
    url: str
    account_id: str
    list_name: Optional[str] = None
    limit: Optional[int] = None


class ScrapeStatusOut(BaseModel):
    status: str  # "running" | "done" | "error" | "unknown"
    collected: int = 0
    error: Optional[str] = None


async def _run_event_scrape(list_id: str, account_id: str, url: str, limit: Optional[int]):
    """Background task: scrape LinkedIn event attendees and persist as leads."""
    from linauto.db.engine import get_session_factory
    from linauto.db.repository import Repository as _Repo
    from linauto.db.models import Lead, LeadStatus
    from linauto.linkedin.browser import LinkedInBrowser
    from linauto.linkedin.scraper import scrape_event_attendees

    _scrape_jobs[list_id] = {"status": "running", "collected": 0}

    session = get_session_factory()()
    repo = _Repo(session)
    browser = LinkedInBrowser()

    try:
        account = await repo.get_account(account_id)
        if not account:
            _scrape_jobs[list_id] = {"status": "error", "collected": 0, "error": "Account not found"}
            return

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
            _scrape_jobs[list_id] = {"status": "error", "collected": 0, "error": "Session expired — please re-login"}
            return

        page = await browser.new_page()
        try:
            def _on_progress(count: int, _page_num: int):
                _scrape_jobs[list_id]["collected"] = count

            urls = await scrape_event_attendees(page, url, limit=limit, on_progress=_on_progress)
        finally:
            await page.close()

        # Persist leads
        ll = await repo.get_lead_list(list_id)
        if ll and urls:
            existing_urls = await repo.get_list_lead_urls(list_id)
            new_leads = [
                Lead(lead_list_id=list_id, linkedin_url=u, status=LeadStatus.PENDING)
                for u in urls if u not in existing_urls
            ]
            if new_leads:
                count = await repo.bulk_create_leads(new_leads)
                await repo.update_lead_list(ll, total_leads=ll.total_leads + count, csv_filename="event_attendees.csv")

        _scrape_jobs[list_id] = {"status": "done", "collected": len(urls)}
        logger.info("event_scrape.done", list_id=list_id, total=len(urls))

    except Exception as e:
        logger.error("event_scrape.failed", list_id=list_id, error=str(e))
        prev = _scrape_jobs.get(list_id, {})
        _scrape_jobs[list_id] = {"status": "error", "collected": prev.get("collected", 0), "error": str(e)}
    finally:
        await browser.close()
        await session.close()


async def _enrich_lead_list(repo: Repository, lead_list) -> LeadListOut:
    """Add campaign_count to a LeadList."""
    out = LeadListOut.model_validate(lead_list)
    links = await repo.get_list_campaigns(lead_list.id)
    out.campaign_count = len(links)
    return out


@router.get("/lead-lists", response_model=list[LeadListOut])
async def list_lead_lists(
    include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    lists = await repo.list_lead_lists(include_archived=include_archived)
    return [await _enrich_lead_list(repo, ll) for ll in lists]


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


# Static routes must be registered before /{lead_list_id} to avoid 405s
@router.post("/lead-lists/event-import", response_model=LeadListOut, status_code=201)
async def event_import_list(
    body: EventImportRequest,
    background_tasks: BackgroundTasks,
    repo: Repository = Depends(get_repo),
):
    """Create a lead list and start scraping LinkedIn event attendees in the background."""
    name = body.list_name or f"Event Attendees - {datetime.utcnow().strftime('%m/%d')}"
    ll = await repo.create_lead_list(name=name, csv_filename="scraping...")
    background_tasks.add_task(_run_event_scrape, ll.id, body.account_id, body.url, body.limit)
    return await _enrich_lead_list(repo, ll)


@router.get("/lead-lists/{lead_list_id}/scrape-status", response_model=ScrapeStatusOut)
async def get_scrape_status(lead_list_id: str):
    """Poll the progress of an in-progress event attendee scrape."""
    job = _scrape_jobs.get(lead_list_id)
    if not job:
        return ScrapeStatusOut(status="unknown", collected=0)
    return ScrapeStatusOut(**job)


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


@router.post("/lead-lists/{lead_list_id}/archive", response_model=LeadListOut)
async def archive_lead_list(lead_list_id: str, repo: Repository = Depends(get_repo)):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    ll = await repo.update_lead_list(ll, archived=True)
    return await _enrich_lead_list(repo, ll)


@router.post("/lead-lists/{lead_list_id}/unarchive", response_model=LeadListOut)
async def unarchive_lead_list(lead_list_id: str, repo: Repository = Depends(get_repo)):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    ll = await repo.update_lead_list(ll, archived=False)
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


@router.get("/lead-lists/{lead_list_id}/export")
async def export_lead_list_csv(lead_list_id: str, repo: Repository = Depends(get_repo)):
    """Download all leads in a list as a CSV file."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    leads, _ = await repo.get_list_leads(lead_list_id, page=1, per_page=10000)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["linkedin_url", "first_name", "last_name", "company", "title"])
    for lead in leads:
        writer.writerow([
            lead.linkedin_url,
            lead.first_name or "",
            lead.last_name or "",
            lead.company or "",
            lead.title or "",
        ])

    buf.seek(0)
    filename = f"{ll.name.replace(' ', '_')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    await repo.update_campaign(campaign, total_leads=max(0, campaign.total_leads - count))
    return {"leads_removed": count}

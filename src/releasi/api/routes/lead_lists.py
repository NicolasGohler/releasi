"""Lead list endpoints — CRUD, CSV upload, assign/unassign to campaigns."""
from __future__ import annotations

import tempfile
from datetime import datetime
from typing import Optional

import structlog
import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File  # noqa: F401
from pydantic import BaseModel

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo, get_current_user_id
from releasi.api.schemas import (
    LeadListOut,
    LeadListCreate,
    LeadListUpdate,
    LeadListDetail,
    LeadListStats,
    AssignListRequest,
    ImportResponse,
    LeadOut,
    LeadPage,
)
from releasi.db.repository import Repository

logger = structlog.get_logger()
router = APIRouter(dependencies=[Depends(require_api_key)])

# ── Event import job tracking (in-memory) ────────────────────────────────
# Maps lead_list_id → job state. Single-process FastAPI — safe for our use case.
_scrape_jobs: dict[str, dict] = {}


def _get_apollo_key() -> str:
    """Read apollo_api_key from settings.yaml (top-level or under fundraising:)."""
    for path in ["/app/config/settings.yaml", "config/settings.yaml"]:
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return (
                data.get("apollo_api_key")
                or (data.get("fundraising") or {}).get("apollo_api_key")
                or ""
            )
        except FileNotFoundError:
            continue
    return ""


class EventImportRequest(BaseModel):
    url: str
    account_id: str
    list_name: Optional[str] = None
    limit: Optional[int] = None


class ScrapeStatusOut(BaseModel):
    status: str  # "running" | "done" | "error" | "unknown"
    collected: int = 0
    error: Optional[str] = None


async def _apollo_enrich_batch(items: list, apollo_api_key: str) -> dict:
    """
    Enrich up to 10 leads via Apollo bulk_match (linkedin_url → name, email, company, title).
    Returns a dict keyed by normalized linkedin_url with the enriched fields.
    """
    import httpx

    details = []
    for item in items:
        d = {"linkedin_url": item["url"]}
        name = item.get("name", "")
        if name:
            parts = name.strip().split(None, 1)
            if parts:
                d["first_name"] = parts[0]
            if len(parts) > 1:
                d["last_name"] = parts[1]
        details.append(d)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.apollo.io/api/v1/people/bulk_match",
                json={"details": details, "reveal_personal_emails": True},
                headers={"x-api-key": apollo_api_key, "Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            logger.warning("apollo_enrich.batch_failed", status=resp.status_code)
            return {}
        matches = resp.json().get("matches") or []
        result = {}
        for m in matches:
            li_url = (m.get("linkedin_url") or "").lower().rstrip("/")
            if not li_url:
                continue
            email = m.get("email") or None
            result[li_url] = {
                "first_name": m.get("first_name") or None,
                "last_name": m.get("last_name") or None,
                "email": email if email and "@" in email else None,
                "company": (m.get("organization") or {}).get("name") or None,
                "title": m.get("title") or None,
            }
        return result
    except Exception as e:
        logger.warning("apollo_enrich.error", error=str(e))
        return {}


async def _run_event_scrape(list_id: str, account_id: str, url: str, limit: Optional[int]):
    """Background task: scrape LinkedIn event attendees, Apollo-enrich, and persist as leads."""
    from releasi.db.engine import get_session_factory
    from releasi.db.repository import Repository as _Repo
    from releasi.db.models import Lead, LeadStatus
    from releasi.linkedin.browser import LinkedInBrowser
    from releasi.linkedin.scraper import scrape_event_attendees
    _scrape_jobs[list_id] = {"status": "running", "collected": 0, "enriched": 0}

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

            items = await scrape_event_attendees(
                page, url,
                limit=limit,
                on_progress=_on_progress,
                page_factory=browser.new_page,
            )
        finally:
            await page.close()

        # Apollo enrichment — batches of 10, only if API key is configured
        apollo_key = _get_apollo_key()
        enrichment: dict = {}
        if apollo_key and items:
            _scrape_jobs[list_id]["status"] = "enriching"
            batch_size = 10
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                batch_result = await _apollo_enrich_batch(batch, apollo_key)
                enrichment.update(batch_result)
                _scrape_jobs[list_id]["enriched"] = len(enrichment)
                logger.info("apollo_enrich.progress", enriched=len(enrichment), total=len(items))

        # Persist leads
        ll = await repo.get_lead_list(list_id)
        if ll and items:
            existing_urls = await repo.get_list_lead_urls(list_id)
            new_leads = []
            for item in items:
                li_url = item["url"]
                if li_url in existing_urls:
                    continue
                # Merge scraper name with Apollo enrichment (Apollo wins on fields it provides)
                apollo = enrichment.get(li_url.lower().rstrip("/"), {})
                scraper_name = item.get("name", "")
                scraper_parts = scraper_name.strip().split(None, 1) if scraper_name else []
                first_name = apollo.get("first_name") or (scraper_parts[0] if scraper_parts else None)
                last_name = apollo.get("last_name") or (scraper_parts[1] if len(scraper_parts) > 1 else None)
                new_leads.append(Lead(
                    lead_list_id=list_id,
                    linkedin_url=li_url,
                    first_name=first_name,
                    last_name=last_name,
                    email=apollo.get("email"),
                    company=apollo.get("company"),
                    title=apollo.get("title"),
                    status=LeadStatus.PENDING,
                ))
            if new_leads:
                count = await repo.bulk_create_leads(new_leads)
                await repo.update_lead_list(ll, total_leads=ll.total_leads + count, csv_filename="event_attendees.csv")

        _scrape_jobs[list_id] = {"status": "done", "collected": len(items), "enriched": len(enrichment)}
        logger.info("event_scrape.done", list_id=list_id, total=len(items), enriched=len(enrichment))

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
    ll = await repo.create_lead_list(name=body.name, tg_enrich_enabled=body.tg_enrich_enabled)
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
    ll = await repo.update_lead_list(ll, source_url=body.url, scrape_account_id=body.account_id)
    background_tasks.add_task(_run_event_scrape, ll.id, body.account_id, body.url, body.limit)
    return await _enrich_lead_list(repo, ll)


class ReScrapeRequest(BaseModel):
    account_id: Optional[str] = None
    limit: Optional[int] = None


@router.post("/lead-lists/{lead_list_id}/re-scrape", response_model=LeadListOut)
async def re_scrape_list(
    lead_list_id: str,
    body: ReScrapeRequest,
    background_tasks: BackgroundTasks,
    repo: Repository = Depends(get_repo),
):
    """Re-run the LinkedIn event scrape on an existing list, skipping already-imported leads."""
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    if not ll.source_url:
        raise HTTPException(status_code=400, detail="No source URL stored for this list — cannot re-scrape")

    account_id = body.account_id or ll.scrape_account_id
    if not account_id:
        raise HTTPException(status_code=400, detail="No account ID provided and none stored on this list")

    # Update stored account if caller explicitly overrode it
    if body.account_id and body.account_id != ll.scrape_account_id:
        ll = await repo.update_lead_list(ll, scrape_account_id=body.account_id)

    background_tasks.add_task(_run_event_scrape, ll.id, account_id, ll.source_url, body.limit)
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

    stats_data = await repo.get_lead_list_stats(lead_list_id)
    stats = LeadListStats(**stats_data)

    out = LeadListDetail.model_validate(ll)
    out.campaign_count = len(campaigns)
    out.campaigns = campaigns
    out.stats = stats
    return out


@router.patch("/lead-lists/{lead_list_id}", response_model=LeadListOut)
async def update_lead_list(
    lead_list_id: str,
    body: LeadListUpdate,
    repo: Repository = Depends(get_repo),
):
    """Update a lead list's name and/or tg_enrich_enabled flag."""
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    updates = body.model_dump(exclude_none=True)
    if "name" in updates:
        existing = await repo.get_lead_list_by_name(updates["name"])
        if existing and existing.id != lead_list_id:
            raise HTTPException(status_code=409, detail="Lead list name already exists")
    if updates:
        ll = await repo.update_lead_list(ll, **updates)
    return await _enrich_lead_list(repo, ll)


@router.post("/lead-lists/{lead_list_id}/archive", response_model=LeadListOut)
async def archive_lead_list(
    lead_list_id: str,
    repo: Repository = Depends(get_repo),
    actor_user_id: Optional[str] = Depends(get_current_user_id),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    ll = await repo.update_lead_list(ll, archived=True)
    logger.info(
        "lead_list_archived",
        list_id=lead_list_id,
        list_name=ll.name,
        actor_user_id=actor_user_id or "system",
    )
    return await _enrich_lead_list(repo, ll)


@router.post("/lead-lists/{lead_list_id}/unarchive", response_model=LeadListOut)
async def unarchive_lead_list(
    lead_list_id: str,
    repo: Repository = Depends(get_repo),
    actor_user_id: Optional[str] = Depends(get_current_user_id),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")
    ll = await repo.update_lead_list(ll, archived=False)
    logger.info(
        "lead_list_unarchived",
        list_id=lead_list_id,
        list_name=ll.name,
        actor_user_id=actor_user_id or "system",
    )
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
    from releasi.campaign.importer import parse_csv
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
        no_url_imported=result.no_url_imported,
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
    writer.writerow(["linkedin_url", "first_name", "last_name", "company", "title", "email", "phone"])
    for lead in leads:
        writer.writerow([
            lead.linkedin_url,
            lead.first_name or "",
            lead.last_name or "",
            lead.company or "",
            lead.title or "",
            lead.email or "",
            lead.phone or "",
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
    actor_user_id: Optional[str] = Depends(get_current_user_id),
):
    ll = await repo.get_lead_list(lead_list_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Lead list not found")

    campaign = await repo.get_campaign(body.campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    count = await repo.unassign_list_from_campaign(lead_list_id, body.campaign_id)
    await repo.update_campaign(campaign, total_leads=max(0, campaign.total_leads - count))
    logger.info(
        "lead_list_unassigned",
        list_id=lead_list_id,
        list_name=ll.name,
        campaign_id=body.campaign_id,
        campaign_name=campaign.name,
        leads_removed=count,
        actor_user_id=actor_user_id or "system",
    )
    return {"leads_removed": count}

"""Lead endpoints — paginated list, CSV upload, soft delete, restore, global library."""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import (
    LeadOut, LeadPage, ImportResponse,
    BulkLeadRequest, BulkLeadResponse,
    LeadUpdateRequest, LeadActivityOut,
    FindTelegramTaskOut,
)
from linauto.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])

# In-memory task store for find-telegram background jobs.
# { task_id: {status, telegram_username, telegram_alternatives, logs, error} }
_find_tg_tasks: dict[str, dict] = {}


@router.get("/campaigns/{campaign_id}/leads", response_model=LeadPage)
async def list_leads(
    campaign_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    exclude_removed: bool = Query(False),
    lead_list_id: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    sort_dir: str = Query("asc"),
    requested_after: Optional[str] = Query(None),
    requested_before: Optional[str] = Query(None),
    skip_reason: Optional[str] = Query(None),
    read_source: str = Query(
        "new",
        description=(
            "Phase 2 read cutover knob. 'new' (default) reads from "
            "campaign_lead_assignments. 'legacy' is the rollback escape "
            "hatch — removed in Phase 3."
        ),
    ),
    repo: Repository = Depends(get_repo),
):
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    paginator = (
        repo.list_leads_via_assignments_paginated
        if read_source == "new"
        else repo.list_leads_paginated
    )
    leads, total = await paginator(
        campaign_id=campaign_id,
        page=page,
        per_page=per_page,
        status_filter=status,
        search=search,
        exclude_removed=exclude_removed,
        lead_list_id=lead_list_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        requested_after=requested_after,
        requested_before=requested_before,
        skip_reason=skip_reason,
    )
    items = await _enrich_leads(repo, leads)
    return LeadPage(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


async def _enrich_leads(repo: Repository, leads) -> list[LeadOut]:
    """Attach campaign_name and lead_list_name to LeadOut.

    Batches the name lookups (one query per unique id) so we don't fan out
    one query per lead. Used by both per-campaign and global leads endpoints.
    """
    campaign_ids = {l.campaign_id for l in leads if l.campaign_id}
    list_ids = {l.lead_list_id for l in leads if l.lead_list_id}

    campaign_names: dict[str, str] = {}
    for cid in campaign_ids:
        c = await repo.get_campaign(cid)
        if c:
            campaign_names[cid] = c.name

    list_names: dict[str, str] = {}
    for lid in list_ids:
        ll = await repo.get_lead_list(lid)
        if ll:
            list_names[lid] = ll.name

    items: list[LeadOut] = []
    for l in leads:
        out = LeadOut.model_validate(l)
        if l.campaign_id and l.campaign_id in campaign_names:
            out.campaign_name = campaign_names[l.campaign_id]
        if l.lead_list_id and l.lead_list_id in list_names:
            out.lead_list_name = list_names[l.lead_list_id]
        items.append(out)
    return items


@router.get("/campaigns/{campaign_id}/leads/export")
async def export_campaign_leads_csv(
    campaign_id: str,
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    exclude_removed: bool = Query(False),
    lead_list_id: Optional[str] = Query(None),
    read_source: str = Query(
        "new",
        description=(
            "Phase 2 read cutover knob. 'new' (default) reads from "
            "campaign_lead_assignments. 'legacy' reads from leads.campaign_id "
            "and is the rollback escape hatch. Removed entirely in Phase 3 "
            "once we drop Lead.campaign_id."
        ),
    ),
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

    if read_source == "new":
        leads, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=campaign_id,
            page=1,
            per_page=100000,
            status_filter=status,
            search=search,
            exclude_removed=exclude_removed,
            lead_list_id=lead_list_id,
        )
    else:
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
        "email", "phone", "twitter_url", "telegram_username",
        "status", "connection_requested_at", "connection_accepted_at",
        "followup_sent_at", "error_message", "created_at",
    ])
    for lead in leads:
        # Normalise status so both read paths produce identical CSV output:
        # legacy returns a LeadStatus enum (`.value` → "connection_requested"),
        # the new path returns the lowercase string directly. Either way the
        # column is the lowercase enum value, which is also the dashboard's
        # canonical representation.
        status_val = lead.status.value if hasattr(lead.status, "value") else (lead.status or "")
        writer.writerow([
            lead.linkedin_url,
            lead.first_name or "",
            lead.last_name or "",
            lead.company or "",
            lead.title or "",
            lead.email or "",
            lead.phone or "",
            getattr(lead, "twitter_url", None) or "",
            getattr(lead, "telegram_username", None) or "",
            status_val,
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


@router.get("/campaigns/{campaign_id}/leads/export/parity")
async def export_parity_check(
    campaign_id: str,
    status: Optional[str] = Query(None),
    repo: Repository = Depends(get_repo),
):
    """Phase 2 step 1 verification: run BOTH read paths against the same
    campaign and compare. Returns a structured diff so we can confirm the
    new schema produces identical CSV output before flipping the default.

    Only counts, set-difference of lead IDs, and per-field mismatch counts —
    no full row dump (some campaigns have thousands of leads).
    """
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    legacy, _ = await repo.list_leads_paginated(
        campaign_id=campaign_id, page=1, per_page=100000,
        status_filter=status, exclude_removed=False,
    )
    new, _ = await repo.list_leads_via_assignments_paginated(
        campaign_id=campaign_id, page=1, per_page=100000,
        status_filter=status, exclude_removed=False,
    )

    legacy_by_id = {l.id: l for l in legacy}
    new_by_id = {l.id: l for l in new}

    only_in_legacy = sorted(legacy_by_id.keys() - new_by_id.keys())
    only_in_new = sorted(new_by_id.keys() - legacy_by_id.keys())
    common_ids = legacy_by_id.keys() & new_by_id.keys()

    mismatches = {
        "status": 0,
        "connection_requested_at": 0,
        "connection_accepted_at": 0,
        "followup_sent_at": 0,
        "scheduled_at": 0,
        "error_message": 0,
        "retry_count": 0,
    }
    sample_mismatches: list = []

    for lid in common_ids:
        a = legacy_by_id[lid]
        b = new_by_id[lid]

        # Status comparison: legacy returns enum, new returns lower-case str.
        a_status = a.status.value if hasattr(a.status, "value") else (a.status or "")
        b_status = b.status or ""
        if a_status != b_status:
            mismatches["status"] += 1
            if len(sample_mismatches) < 5:
                sample_mismatches.append({
                    "lead_id": lid, "field": "status",
                    "legacy": a_status, "new": b_status,
                })

        for field in ("connection_requested_at", "connection_accepted_at",
                      "followup_sent_at", "scheduled_at"):
            av = getattr(a, field, None)
            bv = getattr(b, field, None)
            if av != bv:
                mismatches[field] += 1
                if len(sample_mismatches) < 5:
                    sample_mismatches.append({
                        "lead_id": lid, "field": field,
                        "legacy": av.isoformat() if av else None,
                        "new": bv.isoformat() if bv else None,
                    })

        if (a.error_message or "") != (b.error_message or ""):
            mismatches["error_message"] += 1
        if (a.retry_count or 0) != (b.retry_count or 0):
            mismatches["retry_count"] += 1

    return {
        "campaign_id": campaign_id,
        "counts": {
            "legacy": len(legacy),
            "new": len(new),
            "only_in_legacy": len(only_in_legacy),
            "only_in_new": len(only_in_new),
            "common": len(common_ids),
        },
        "field_mismatches": mismatches,
        "sample_mismatches": sample_mismatches,
        "all_zero": (
            len(only_in_legacy) == 0
            and len(only_in_new) == 0
            and sum(mismatches.values()) == 0
        ),
    }


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

    # Get existing URLs for dedup via assignments (Phase 3b)
    existing_leads, _ = await repo.list_leads_via_assignments_paginated(
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


# ── TG Enrichment ────────────────────────────────────────────────────

@router.get("/campaigns/{campaign_id}/leads/enrich/telegram/status")
async def telegram_enrichment_status(
    campaign_id: str,
    repo: Repository = Depends(get_repo),
):
    """Return TG enrichment coverage stats for a campaign.

    Counts how many leads have a telegram_username vs. how many don't,
    so the dashboard can show a coverage indicator.
    """
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    leads, _ = await repo.list_leads_via_assignments_paginated(
        campaign_id=campaign_id, page=1, per_page=100000
    )
    total = len(leads)
    enriched = sum(1 for l in leads if getattr(l, "telegram_username", None))
    return {
        "total": total,
        "enriched": enriched,
        "missing": total - enriched,
        "coverage_pct": round(enriched / total * 100, 1) if total else 0.0,
    }


@router.post("/campaigns/{campaign_id}/leads/enrich/telegram")
async def trigger_telegram_enrichment(
    campaign_id: str,
    repo: Repository = Depends(get_repo),
):
    """Trigger TG enrichment for leads missing a telegram_username.

    TODO: Connect your prebuilt enrichment agent here. The endpoint
    should kick off a background task that iterates over leads where
    telegram_username IS NULL and populates them via your agent.

    Until the agent is wired in, returns 501.
    """
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    raise HTTPException(
        status_code=501,
        detail="Enrichment agent not yet connected. Wire your prebuilt agent to this endpoint.",
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
    sort_by: Optional[str] = Query(None),
    sort_dir: str = Query("desc"),
    requested_after: Optional[str] = Query(None),
    requested_before: Optional[str] = Query(None),
    skip_reason: Optional[str] = Query(None),
    unassigned_campaign: bool = Query(False),
    unassigned_list: bool = Query(False),
    has_telegram: Optional[bool] = Query(None),
    has_twitter: Optional[bool] = Query(None),
    has_email: Optional[bool] = Query(None),
    tg_contacted: Optional[bool] = Query(None),
    repo: Repository = Depends(get_repo),
):
    leads, total = await repo.list_leads_global(
        page=page,
        per_page=per_page,
        lead_list_id=lead_list_id,
        campaign_id=campaign_id,
        status_filter=status,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        requested_after=requested_after,
        requested_before=requested_before,
        skip_reason=skip_reason,
        unassigned_campaign=unassigned_campaign,
        unassigned_list=unassigned_list,
        has_telegram=has_telegram,
        has_twitter=has_twitter,
        has_email=has_email,
        tg_contacted=tg_contacted,
    )

    items = await _enrich_leads(repo, leads)
    return LeadPage(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


# ── Bulk Actions (must be before /{lead_id} routes to avoid path collision) ──

@router.post("/leads/bulk/skip", response_model=BulkLeadResponse)
async def bulk_skip_leads(body: BulkLeadRequest, repo: Repository = Depends(get_repo)):
    updated = await repo.bulk_skip_leads(body.lead_ids)
    return BulkLeadResponse(updated=updated)


@router.post("/leads/bulk/remove", response_model=BulkLeadResponse)
async def bulk_remove_leads(body: BulkLeadRequest, repo: Repository = Depends(get_repo)):
    updated = await repo.bulk_remove_leads(body.lead_ids)
    return BulkLeadResponse(updated=updated)


@router.post("/leads/bulk/requeue", response_model=BulkLeadResponse)
async def bulk_requeue_leads(body: BulkLeadRequest, repo: Repository = Depends(get_repo)):
    updated = await repo.bulk_requeue_leads(body.lead_ids)
    return BulkLeadResponse(updated=updated)


# ── Single Lead GET / PATCH ─────────────────────────────────────────

@router.get("/leads/{lead_id}", response_model=LeadOut)
async def get_lead(lead_id: str, repo: Repository = Depends(get_repo)):
    """Fetch a single lead with enriched campaign/list names."""
    lead = await repo.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    items = await _enrich_leads(repo, [lead])
    return items[0]


@router.patch("/leads/{lead_id}", response_model=LeadOut)
async def update_lead_profile(
    lead_id: str,
    body: LeadUpdateRequest,
    repo: Repository = Depends(get_repo),
):
    """Update editable profile fields on a lead (name, social handles, etc.).

    Only fields present in the request body are updated — absent fields are
    left unchanged. Pass an explicit null to clear a field.
    """
    from linauto.campaign.importer import normalize_twitter_url, normalize_telegram_username

    lead = await repo.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    updates: dict = {}
    for field in ("first_name", "last_name", "company", "title", "email", "phone"):
        if field in body.model_fields_set:
            val = getattr(body, field)
            updates[field] = val.strip() if isinstance(val, str) else val

    if "twitter_url" in body.model_fields_set:
        raw = body.twitter_url
        updates["twitter_url"] = normalize_twitter_url(raw) if raw else None

    if "telegram_username" in body.model_fields_set:
        raw = body.telegram_username
        normalized = normalize_telegram_username(raw) if raw else None
        updates["telegram_username"] = normalized
        # Clear alternatives when user explicitly saves a username (choice made).
        updates["telegram_alternatives"] = None
        if normalized:
            await repo.log_lead_event(lead_id, "telegram_saved", {"username": normalized})
            await repo.session.commit()

    if "tg_contacted" in body.model_fields_set:
        from datetime import datetime as _dt
        if body.tg_contacted:
            now = _dt.utcnow()
            updates["tg_contacted_at"] = now
            await repo.log_lead_event(lead_id, "tg_contacted", {"at": now.isoformat()})
            await repo.session.commit()
        else:
            updates["tg_contacted_at"] = None
            await repo.log_lead_event(lead_id, "tg_contacted_cleared", {})
            await repo.session.commit()

    if updates:
        lead = await repo.update_lead(lead, **updates)

    items = await _enrich_leads(repo, [lead])
    return items[0]


@router.get("/leads/{lead_id}/activity", response_model=list[LeadActivityOut])
async def get_lead_activity(
    lead_id: str,
    limit: int = 50,
    repo: Repository = Depends(get_repo),
):
    """Return activity for a lead: ActionLog entries + LeadEvent entries, newest first."""
    from sqlalchemy import select, desc
    from linauto.db.models import ActionLog, Account, LeadEvent

    lead = await repo.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Fetch ActionLog entries
    al_result = await repo.session.execute(
        select(ActionLog)
        .where(ActionLog.lead_id == lead_id)
        .order_by(desc(ActionLog.created_at))
        .limit(limit)
    )
    action_logs = al_result.scalars().all()

    # Batch-fetch account names
    account_ids = {l.account_id for l in action_logs}
    account_names: dict[str, str] = {}
    for aid in account_ids:
        acc = await repo.session.get(Account, aid)
        if acc:
            account_names[aid] = acc.name

    # Fetch LeadEvent entries
    le_result = await repo.session.execute(
        select(LeadEvent)
        .where(LeadEvent.lead_id == lead_id)
        .order_by(desc(LeadEvent.created_at))
        .limit(limit)
    )
    lead_events = le_result.scalars().all()

    # Merge and sort newest-first, cap at limit
    items: list[LeadActivityOut] = []
    for l in action_logs:
        items.append(LeadActivityOut(
            id=l.id,
            action_type=l.action_type.value if hasattr(l.action_type, "value") else l.action_type,
            status=l.status.value if hasattr(l.status, "value") else l.status,
            details=l.details,
            created_at=l.created_at,
            account_name=account_names.get(l.account_id),
            source="action_log",
        ))
    for e in lead_events:
        items.append(LeadActivityOut(
            id=e.id,
            action_type=e.event_type,
            status="success",
            details=e.details,
            created_at=e.created_at,
            account_name=None,
            source="lead_event",
        ))

    items.sort(key=lambda x: x.created_at, reverse=True)
    return items[:limit]


# ── Find Telegram ────────────────────────────────────────────────────

@router.post("/leads/{lead_id}/find-telegram", response_model=FindTelegramTaskOut)
async def start_find_telegram(lead_id: str, repo: Repository = Depends(get_repo)):
    """Start a background task to find the Telegram username for a lead.

    Returns immediately with a task_id. Poll GET /leads/{lead_id}/find-telegram/{task_id}
    to check progress. On completion, telegram_alternatives is also persisted to the DB.
    """
    from linauto.config import get_settings
    from linauto.telegram.resolver import find_telegram

    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash or not settings.telegram_session:
        raise HTTPException(
            status_code=503,
            detail=(
                "Telegram credentials not configured. "
                "Set LINAUTO_TELEGRAM_API_ID, LINAUTO_TELEGRAM_API_HASH, "
                "and LINAUTO_TELEGRAM_SESSION on the server."
            ),
        )

    lead = await repo.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    person_name = " ".join(
        p for p in [lead.first_name, lead.last_name] if p
    ).strip() or None
    if not person_name:
        raise HTTPException(status_code=422, detail="Lead has no name — cannot search Telegram")

    task_id = str(uuid.uuid4())
    _find_tg_tasks[task_id] = {"status": "running", "logs": []}

    async def _run(lid: str, name: str, twitter: Optional[str], company: Optional[str]) -> None:
        task = _find_tg_tasks[task_id]
        try:
            find_result = await find_telegram(
                name=name,
                twitter_url=twitter,
                company=company,
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_str=settings.telegram_session,
            )
            task["status"] = "done"
            task["telegram_username"] = find_result.best_match
            task["telegram_alternatives"] = find_result.alternatives
            task["logs"] = find_result.logs

            # Persist alternatives to the DB so they survive page reload.
            # We do NOT auto-save telegram_username — the user confirms via the UI.
            from linauto.db.engine import get_session_factory
            from linauto.db.repository import Repository as Repo
            async with get_session_factory()() as session:
                r = Repo(session)
                lead_obj = await r.get_lead_by_id(lid)
                if lead_obj:
                    # Build the full list: best match first, then alternatives
                    all_found: list[str] = []
                    if find_result.best_match:
                        all_found.append(find_result.best_match)
                    all_found.extend(find_result.alternatives)
                    await r.update_lead(lead_obj, telegram_alternatives=all_found or None)
                    # Log to activity feed
                    await r.log_lead_event(lid, "telegram_found", {
                        "best_match": find_result.best_match,
                        "alternatives": find_result.alternatives,
                        "candidates_checked": len(find_result.logs),
                    })
                    await session.commit()
        except Exception as exc:
            _find_tg_tasks[task_id]["status"] = "error"
            _find_tg_tasks[task_id]["error"] = str(exc)
            _find_tg_tasks[task_id].setdefault("logs", []).append(f"Exception: {exc}")

    asyncio.create_task(_run(
        lead_id,
        person_name,
        getattr(lead, "twitter_url", None),
        getattr(lead, "company", None),
    ))

    return FindTelegramTaskOut(task_id=task_id, status="running")


@router.get("/leads/{lead_id}/find-telegram/{task_id}", response_model=FindTelegramTaskOut)
async def get_find_telegram_status(lead_id: str, task_id: str):
    """Poll the status of a find-telegram background task."""
    task = _find_tg_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return FindTelegramTaskOut(
        task_id=task_id,
        status=task.get("status", "running"),
        telegram_username=task.get("telegram_username"),
        telegram_alternatives=task.get("telegram_alternatives") or [],
        logs=task.get("logs") or [],
        error=task.get("error"),
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

"""Broadcast endpoints — message-only campaigns.

A Broadcast sends a 1–3 message sequence to a snapshot of 1st-degree
connections. Peer to Campaign, but with its own dispatcher and its own
per-account daily counter (shared with post-acceptance follow-ups via
Account.daily_message_limit).
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.api.schemas import (
    BroadcastOut,
    BroadcastCreate,
    BroadcastUpdate,
    BroadcastAddLeadsRequest,
    BroadcastLeadOut,
    BroadcastLeadsPage,
)
from releasi.db.models import (
    ActionType,
    ActionLogStatus,
    ActionLog,
    Broadcast,
    BroadcastStatus,
    Lead,
)
from releasi.db.repository import Repository

from sqlalchemy import select, func

router = APIRouter(dependencies=[Depends(require_api_key)])


async def _enrich(repo: Repository, broadcast: Broadcast) -> BroadcastOut:
    out = BroadcastOut.model_validate(broadcast)
    out.status_counts = await repo.get_broadcast_status_counts(broadcast.id)

    account = await repo.get_account(broadcast.account_id)
    if account:
        out.account_name = account.name

    if broadcast.source_list_id:
        source_list = await repo.get_lead_list(broadcast.source_list_id)
        if source_list:
            out.source_list_name = source_list.name

    # Count successful DIRECT_MESSAGE action_log rows for this broadcast.
    # Different from total_leads: a lead may receive 1–3 messages, or 0 if skipped.
    sent_result = await repo.session.execute(
        select(func.count()).select_from(ActionLog).where(
            ActionLog.broadcast_id == broadcast.id,
            ActionLog.action_type == ActionType.DIRECT_MESSAGE,
            ActionLog.status == ActionLogStatus.SUCCESS,
        )
    )
    out.messages_sent = sent_result.scalar_one() or 0

    return out


async def _enrich_lead(repo: Repository, bl) -> BroadcastLeadOut:
    out = BroadcastLeadOut.model_validate(bl)
    lead = await repo.get_lead_by_id(bl.lead_id)
    if lead:
        parts = [p for p in [lead.first_name, lead.last_name] if p]
        out.lead_name = " ".join(parts) if parts else None
        out.lead_company = lead.company
        out.lead_linkedin_url = lead.linkedin_url
    return out


# ── Broadcast CRUD ─────────────────────────────────────────────────────────

@router.get("/broadcasts", response_model=List[BroadcastOut])
async def list_broadcasts(
    account_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    broadcasts = await repo.list_broadcasts(
        account_id=account_id, include_archived=include_archived
    )
    if status:
        broadcasts = [b for b in broadcasts if b.status.value == status]
    return [await _enrich(repo, b) for b in broadcasts]


@router.get("/broadcasts/{broadcast_id}", response_model=BroadcastOut)
async def get_broadcast(broadcast_id: str, repo: Repository = Depends(get_repo)):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    return await _enrich(repo, broadcast)


@router.post("/broadcasts", response_model=BroadcastOut, status_code=201)
async def create_broadcast(body: BroadcastCreate, repo: Repository = Depends(get_repo)):
    # Validate account exists
    account = await repo.get_account(body.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Exactly one snapshot source must be provided.
    has_list = bool(body.source_list_id)
    has_leads = bool(body.lead_ids)
    if has_list == has_leads:
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of source_list_id or lead_ids",
        )

    if has_list:
        source_list = await repo.get_lead_list(body.source_list_id)
        if not source_list:
            raise HTTPException(status_code=404, detail="Source lead list not found")

    if not body.message_1 or not body.message_1.strip():
        raise HTTPException(status_code=422, detail="message_1 is required")

    broadcast = await repo.create_broadcast(
        account_id=body.account_id,
        name=body.name,
        source_list_id=body.source_list_id,
        message_1=body.message_1,
        message_2=body.message_2,
        message_3=body.message_3,
        delay_between_hours=body.delay_between_hours,
        weekend_enabled=body.weekend_enabled,
    )

    # Snapshot leads into broadcast_leads NOW so total_leads is accurate
    # immediately (and so activation is a pure status flip later).
    if has_list:
        inserted = await repo.snapshot_lead_list_into_broadcast(
            broadcast.id, body.source_list_id
        )
    else:
        inserted = await repo.snapshot_leads_into_broadcast(
            broadcast.id, body.lead_ids
        )

    # If snapshot was empty (list had zero leads, or all lead_ids invalid),
    # roll back — a broadcast with no leads is useless and misleading.
    if inserted == 0:
        await repo.delete_broadcast(broadcast.id)
        raise HTTPException(
            status_code=422,
            detail="No valid leads to snapshot into the broadcast",
        )

    return await _enrich(repo, broadcast)


@router.patch("/broadcasts/{broadcast_id}", response_model=BroadcastOut)
async def update_broadcast(
    broadcast_id: str,
    body: BroadcastUpdate,
    repo: Repository = Depends(get_repo),
):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")

    updates = body.model_dump(exclude_unset=True)

    # Lock message-sequence and pacing fields once the broadcast has started.
    # Changing them mid-flight would silently change what pending leads receive.
    if broadcast.status in (BroadcastStatus.ACTIVE, BroadcastStatus.COMPLETED):
        locked = {"message_1", "message_2", "message_3", "delay_between_hours"}
        if locked & updates.keys():
            raise HTTPException(
                status_code=409,
                detail="Cannot edit message sequence or delay once broadcast is active or completed",
            )

    if updates:
        broadcast = await repo.update_broadcast(broadcast, **updates)
    return await _enrich(repo, broadcast)


@router.delete("/broadcasts/{broadcast_id}", status_code=204)
async def delete_broadcast(broadcast_id: str, repo: Repository = Depends(get_repo)):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    # Only allow hard delete for DRAFT / archived broadcasts; anything else
    # has send history that shouldn't just disappear.
    if broadcast.status not in (BroadcastStatus.DRAFT,) and not broadcast.archived:
        raise HTTPException(
            status_code=409,
            detail="Archive the broadcast first — only DRAFT or archived broadcasts can be hard-deleted",
        )
    await repo.delete_broadcast(broadcast_id)
    return None


# ── Lifecycle transitions ──────────────────────────────────────────────────

@router.post("/broadcasts/{broadcast_id}/activate", response_model=BroadcastOut)
async def activate_broadcast(broadcast_id: str, repo: Repository = Depends(get_repo)):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    if broadcast.archived:
        raise HTTPException(status_code=409, detail="Broadcast is archived")
    if broadcast.total_leads == 0:
        raise HTTPException(
            status_code=422, detail="Broadcast has no leads snapshotted"
        )
    if not broadcast.message_1:
        raise HTTPException(status_code=422, detail="message_1 is required")

    broadcast = await repo.update_broadcast(broadcast, status=BroadcastStatus.ACTIVE)
    return await _enrich(repo, broadcast)


@router.post("/broadcasts/{broadcast_id}/pause", response_model=BroadcastOut)
async def pause_broadcast(broadcast_id: str, repo: Repository = Depends(get_repo)):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    broadcast = await repo.update_broadcast(broadcast, status=BroadcastStatus.PAUSED)
    return await _enrich(repo, broadcast)


@router.post("/broadcasts/{broadcast_id}/archive", response_model=BroadcastOut)
async def archive_broadcast(broadcast_id: str, repo: Repository = Depends(get_repo)):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    # Also pause it if it was active — archived broadcasts shouldn't dispatch.
    updates = {"archived": True}
    if broadcast.status == BroadcastStatus.ACTIVE:
        updates["status"] = BroadcastStatus.PAUSED
    broadcast = await repo.update_broadcast(broadcast, **updates)
    return await _enrich(repo, broadcast)


@router.post("/broadcasts/{broadcast_id}/unarchive", response_model=BroadcastOut)
async def unarchive_broadcast(broadcast_id: str, repo: Repository = Depends(get_repo)):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    broadcast = await repo.update_broadcast(broadcast, archived=False)
    return await _enrich(repo, broadcast)


# ── Per-lead views ─────────────────────────────────────────────────────────

@router.get("/broadcasts/{broadcast_id}/leads", response_model=BroadcastLeadsPage)
async def list_broadcast_leads(
    broadcast_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    repo: Repository = Depends(get_repo),
):
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")

    rows, total = await repo.list_broadcast_leads_paginated(
        broadcast_id, limit=limit, offset=offset, status_filter=status
    )
    items = [await _enrich_lead(repo, r) for r in rows]
    return BroadcastLeadsPage(items=items, total=total, limit=limit, offset=offset)


@router.post("/broadcasts/{broadcast_id}/leads", response_model=BroadcastOut)
async def add_leads_to_broadcast(
    broadcast_id: str,
    body: BroadcastAddLeadsRequest,
    repo: Repository = Depends(get_repo),
):
    """Add more leads to an existing broadcast's snapshot.

    Only allowed while DRAFT — once ACTIVE / COMPLETED, the snapshot is
    frozen so state stays predictable.
    """
    broadcast = await repo.get_broadcast(broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    if broadcast.status != BroadcastStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail="Can only add leads to a DRAFT broadcast",
        )
    if not body.lead_ids:
        raise HTTPException(status_code=422, detail="lead_ids is empty")
    await repo.snapshot_leads_into_broadcast(broadcast_id, body.lead_ids)
    return await _enrich(repo, broadcast)

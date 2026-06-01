"""Global activity feed endpoint — unified view of action_log + lead_events."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.api.schemas import ActivityItem, ActivityPage
from releasi.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/activity", response_model=ActivityPage)
async def list_activity(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    since: Optional[str] = Query(None, description="ISO 8601 datetime, e.g. 2026-01-01T00:00:00"),
    until: Optional[str] = Query(None, description="ISO 8601 datetime, e.g. 2026-12-31T23:59:59"),
    sources: Optional[str] = Query(None, description="Comma-separated: action_log,lead_events"),
    event_types: Optional[str] = Query(None, description="Comma-separated event type names"),
    account_id: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None),
    repo: Repository = Depends(get_repo),
) -> ActivityPage:
    """Unified activity feed across all sources, newest first.

    Merges action_log (LinkedIn dispatching, fundraising imports, errors) with
    lead_events (Telegram enrichment, social data changes) into a single
    paginated timeline.

    Filters:
      since / until   — ISO 8601 datetimes (inclusive)
      sources         — "action_log", "lead_events", or both (default: both)
      event_types     — comma-separated type names (e.g. "connection_request,tg_sweep_searched")
      account_id      — filter action_log rows by account (ignored for lead_events)
      campaign_id     — filter action_log rows by campaign (ignored for lead_events)
    """
    since_dt: Optional[datetime] = None
    until_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            pass
    if until:
        try:
            until_dt = datetime.fromisoformat(until)
        except ValueError:
            pass

    sources_list = [s.strip() for s in sources.split(",")] if sources else None
    types_list   = [t.strip() for t in event_types.split(",")] if event_types else None

    items_raw, total = await repo.list_activity(
        page=page,
        per_page=per_page,
        since=since_dt,
        until=until_dt,
        sources=sources_list,
        event_types=types_list,
        account_id=account_id,
        campaign_id=campaign_id,
    )

    items = [ActivityItem(**item) for item in items_raw]
    pages = max(1, math.ceil(total / per_page))

    return ActivityPage(items=items, total=total, page=page, per_page=per_page, pages=pages)

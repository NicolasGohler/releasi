"""Global activity feed and stats endpoints."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.api.schemas import ActionLogOut
from releasi.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/activity", response_model=List[ActionLogOut])
async def global_activity(
    limit: int = Query(100, le=500),
    repo: Repository = Depends(get_repo),
):
    logs = await repo.list_action_log(limit=limit)
    return [ActionLogOut.model_validate(l) for l in logs]

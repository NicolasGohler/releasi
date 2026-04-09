"""Global activity feed and stats endpoints."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import ActionLogOut
from linauto.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/activity", response_model=List[ActionLogOut])
async def global_activity(
    limit: int = Query(100, le=500),
    repo: Repository = Depends(get_repo),
):
    logs = await repo.list_action_log(limit=limit)
    return [ActionLogOut.model_validate(l) for l in logs]

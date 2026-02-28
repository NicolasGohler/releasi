"""Account endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import (
    AccountOut, AccountCreate, CookieUpdate,
    ActionLogOut, DailyStatOut,
)
from linauto.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/accounts", response_model=List[AccountOut])
async def list_accounts(repo: Repository = Depends(get_repo)):
    accounts = await repo.list_accounts()
    return [AccountOut.model_validate(a) for a in accounts]


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: str, repo: Repository = Depends(get_repo)):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountOut.model_validate(account)


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountCreate, repo: Repository = Depends(get_repo)):
    existing = await repo.get_account_by_name(body.name)
    if existing:
        raise HTTPException(status_code=409, detail="Account name already exists")
    account = await repo.create_account(
        name=body.name,
        li_at_cookie=body.li_at_cookie,
        li_a_cookie=body.li_a_cookie,
        user_agent=body.user_agent,
        timezone=body.timezone,
        proxy_url=body.proxy_url,
        warmup_enabled=body.warmup_enabled,
    )
    return AccountOut.model_validate(account)


@router.put("/accounts/{account_id}/cookie", response_model=AccountOut)
async def update_cookie(
    account_id: str,
    body: CookieUpdate,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    kwargs = {"li_at_cookie": body.li_at_cookie}
    if body.li_a_cookie is not None:
        kwargs["li_a_cookie"] = body.li_a_cookie
    account = await repo.update_account(account, **kwargs)
    return AccountOut.model_validate(account)


@router.get("/accounts/{account_id}/activity", response_model=List[ActionLogOut])
async def account_activity(
    account_id: str,
    limit: int = Query(50, le=200),
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    logs = await repo.list_action_log(account_id=account_id, limit=limit)
    return [ActionLogOut.model_validate(l) for l in logs]


@router.get("/accounts/{account_id}/stats", response_model=List[DailyStatOut])
async def account_stats(
    account_id: str,
    days: int = Query(30, le=90),
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    start = date.today() - timedelta(days=days)
    stats = await repo.get_daily_stats_range(account_id, start, date.today())
    return [DailyStatOut.model_validate(s) for s in stats]

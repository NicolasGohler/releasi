"""Account endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import (
    AccountOut, AccountCreate, AccountUpdate, CookieUpdate,
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
        li_at_cookie=body.li_at_cookie or "",
        li_a_cookie=body.li_a_cookie,
        user_agent=body.user_agent,
        timezone=body.timezone,
        proxy_url=body.proxy_url,
    )
    # If no cookie provided, mark as needing login
    if not body.li_at_cookie:
        await repo.update_account(account, status="cookie_expired")
    return AccountOut.model_validate(account)


@router.put("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: str,
    body: AccountUpdate,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        return AccountOut.model_validate(account)
    account = await repo.update_account(account, **kwargs)
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


@router.post("/accounts/{account_id}/login-session")
async def start_login_session(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from linauto.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()

    if manager.is_active:
        raise HTTPException(
            status_code=409,
            detail=f"A login session is already active for account {manager.active_account_id}. "
                   "Finish it before starting a new one.",
        )

    try:
        novnc_path = await manager.start_session(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start login session: {e}")

    return {"novnc_url": novnc_path, "account_id": account_id}


@router.post("/accounts/{account_id}/login-session/finish")
async def finish_login_session(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from linauto.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()

    if not manager.is_active or manager.active_account_id != account_id:
        raise HTTPException(status_code=400, detail="No active login session for this account")

    try:
        result = await manager.finish_session()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to finish session: {e}")

    if not result["li_at"]:
        return {"success": False, "message": "No li_at cookie found. Did you complete the login?"}

    # Save cookies to database
    update_kwargs = {"li_at_cookie": result["li_at"], "status": "active"}
    if result["li_a"]:
        update_kwargs["li_a_cookie"] = result["li_a"]
    await repo.update_account(account, **update_kwargs)

    return {"success": True, "message": "Cookies extracted and saved successfully"}


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

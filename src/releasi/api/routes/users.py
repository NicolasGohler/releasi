"""User auth + management endpoints.

Slice 1 surface:
  - POST /auth/login        -> validate handle+password, return UserOut
  - GET  /users/me?user_id  -> fetch current user (used by dashboard header)

Both routes sit behind ``require_api_key`` because the dashboard proxy injects
the backend key on every /api/v1/* call. The dashboard's own session cookie
provides the per-user identity — see ``X-User-Id`` plumbing in slice 2.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.api.schemas import LoginRequest, LoginResponse, UserOut
from releasi.auth.passwords import verify_password
from releasi.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, repo: Repository = Depends(get_repo)):
    if await repo.count_users() == 0:
        raise HTTPException(
            status_code=503,
            detail=(
                "No users provisioned. Bootstrap via CLI on the server: "
                "`releasi user add --handle <name> --superadmin --password <pw>`"
            ),
        )
    user = await repo.get_user_by_handle(payload.handle.strip())
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    await repo.touch_user_last_seen(user.id)
    return LoginResponse(user=UserOut.model_validate(user))


@router.get("/users/me", response_model=UserOut)
async def users_me(
    user_id: str = Query(..., description="Caller's user id (signed by proxy in slice 2)"),
    repo: Repository = Depends(get_repo),
):
    user = await repo.get_user(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)

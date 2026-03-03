"""Account endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from linauto.api.auth import require_api_key
from linauto.api.deps import get_repo
from linauto.api.schemas import (
    AccountOut, AccountCreate, AccountUpdate, CookieUpdate,
    ActionLogOut, DailyStatOut,
)
from linauto.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])
# Public router for endpoints that don't require auth (e.g. avatar served via <img> tags)
public_router = APIRouter()


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
    kwargs = {"li_at_cookie": body.li_at_cookie, "status": "active"}
    if body.li_a_cookie is not None:
        kwargs["li_a_cookie"] = body.li_a_cookie
    account = await repo.update_account(account, **kwargs)

    # Evict old pool slot so pool picks up the new cookie cleanly
    try:
        from linauto.linkedin.pool import get_browser_pool
        pool = get_browser_pool()
        await pool.evict(account.id)
    except RuntimeError:
        pass  # Pool not initialized

    return AccountOut.model_validate(account)


@router.post("/accounts/{account_id}/check-connection")
async def check_connection(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Quick session check — uses the pool browser (same fingerprint) if available."""
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    import time
    start = time.monotonic()

    # Try to use the pool (scheduler context) — avoids spawning a separate browser
    try:
        from linauto.linkedin.pool import get_browser_pool
        pool = get_browser_pool()
        context = await pool.acquire(account)
        try:
            page = await context.new_page()
            try:
                from linauto.linkedin.selectors import FEED_URL, LOGIN_URL_PATTERNS
                await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=15000)
                current_url = page.url

                for pattern in LOGIN_URL_PATTERNS:
                    if pattern in current_url:
                        elapsed = int((time.monotonic() - start) * 1000)
                        await repo.update_account(account, status="cookie_expired")
                        return {"valid": False, "reason": "redirected_to_login", "url": current_url, "elapsed_ms": elapsed}

                elapsed = int((time.monotonic() - start) * 1000)
                if account.status == "cookie_expired":
                    await repo.update_account(account, status="active")
                return {"valid": True, "url": current_url, "elapsed_ms": elapsed}
            except Exception as e:
                elapsed = int((time.monotonic() - start) * 1000)
                return {"valid": False, "error": str(e), "elapsed_ms": elapsed}
            finally:
                await page.close()
        finally:
            pool.release(account.id)
    except RuntimeError:
        # Pool not initialized (e.g. no scheduler running) — fall back to ephemeral
        from linauto.linkedin.browser import LinkedInBrowser
        browser = LinkedInBrowser()
        try:
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            elapsed = int((time.monotonic() - start) * 1000)
            if valid:
                if account.status == "cookie_expired":
                    await repo.update_account(account, status="active")
                return {"valid": True, "elapsed_ms": elapsed}
            else:
                await repo.update_account(account, status="cookie_expired")
                return {"valid": False, "reason": "session_invalid", "elapsed_ms": elapsed}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Check failed: {e}")
        finally:
            await browser.close()


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

    # Auto-cleanup any stale session before starting a new one
    if manager.is_active:
        await manager._cleanup()

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

    if not manager.is_active:
        raise HTTPException(status_code=400, detail="No active login session")

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

    # Evict old pool slot so the pool creates a fresh browser with the new cookie
    # on next acquire (avoids stale cookie / fingerprint mismatch)
    try:
        from linauto.linkedin.pool import get_browser_pool
        pool = get_browser_pool()
        await pool.evict(account.id)
    except RuntimeError:
        pass  # Pool not initialized (CLI context)

    return {"success": True, "message": "Cookies extracted and saved successfully"}


@router.post("/accounts/{account_id}/login-session/cancel")
async def cancel_login_session(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from linauto.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()

    if not manager.is_active:
        return {"success": True, "message": "No active session"}

    await manager._cleanup()
    return {"success": True, "message": "Login session cancelled"}


@public_router.get("/accounts/{account_id}/avatar")
async def get_avatar(account_id: str, repo: Repository = Depends(get_repo)):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.avatar_path:
        raise HTTPException(status_code=404, detail="No avatar available")
    path = Path(account.avatar_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Avatar file not found")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/accounts/{account_id}/fetch-avatar")
async def fetch_avatar_now(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """One-time fetch of avatar — uses pool browser if available."""
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Try to use pool (avoids spawning a separate browser fingerprint)
    try:
        from linauto.linkedin.pool import get_browser_pool
        pool = get_browser_pool()
        await pool.acquire(account)
        try:
            slot = pool._slots.get(account.id)
            if slot:
                # validate_session scrapes the avatar
                valid = await slot.browser.validate_session()
                if not valid:
                    raise HTTPException(status_code=400, detail="Session invalid — cookie may be expired")
                avatar_path = await slot.browser.save_avatar(account.id)
                if avatar_path:
                    await repo.update_account(account, avatar_path=avatar_path)
                    return {"success": True, "avatar_path": avatar_path}
                return {"success": False, "message": "Could not find profile photo on page"}
            return {"success": False, "message": "Pool slot not available"}
        finally:
            pool.release(account.id)
    except RuntimeError:
        # Pool not initialized — fall back to ephemeral browser
        from linauto.linkedin.browser import LinkedInBrowser
        browser = LinkedInBrowser()
        try:
            await browser.launch(
                account_id=account.id,
                li_at_cookie=account.li_at_cookie,
                user_agent=account.user_agent,
                proxy_url=account.proxy_url,
                timezone=account.timezone,
            )
            valid = await browser.validate_session()
            if not valid:
                raise HTTPException(status_code=400, detail="Session invalid — cookie may be expired")
            avatar_path = await browser.save_avatar(account.id)
            if avatar_path:
                await repo.update_account(account, avatar_path=avatar_path)
                return {"success": True, "avatar_path": avatar_path}
            return {"success": False, "message": "Could not find profile photo on page"}
        finally:
            await browser.close()


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

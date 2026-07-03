"""Account endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from releasi.api.auth import require_api_key
from releasi.api.deps import get_repo
from releasi.api.schemas import (
    AccountOut, AccountCreate, AccountUpdate, CookieUpdate,
    ActionLogOut, DailyStatOut, ScheduleSlotOut, AccountHealthOut,
    ProxyTestRequest, ProxyTestResponse,
)
from releasi.db.repository import Repository

router = APIRouter(dependencies=[Depends(require_api_key)])
# Public router for endpoints that don't require auth (e.g. avatar served via <img> tags)
public_router = APIRouter()


# ── Proxy URL helpers ───────────────────────────────────────────────────────

def _parse_proxy_url(url: Optional[str]) -> dict:
    """Return parsed components of a proxy URL for display.

    Returns {host, port, username, password_set} — password itself is never
    exposed to clients.
    """
    if not url:
        return {"host": None, "port": None, "username": None, "password_set": False}
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        return {
            "host": p.hostname,
            "port": p.port,
            "username": p.username,
            "password_set": bool(p.password),
        }
    except Exception:
        return {"host": None, "port": None, "username": None, "password_set": False}


def _proxy_password(url: Optional[str]) -> Optional[str]:
    """Extract the plaintext password from an existing proxy URL."""
    if not url:
        return None
    from urllib.parse import urlparse
    try:
        return urlparse(url).password
    except Exception:
        return None


def _assemble_proxy_url(
    host: Optional[str],
    port: Optional[int],
    username: Optional[str],
    password: Optional[str],
) -> Optional[str]:
    """Build http://user:pass@host:port. Returns None if host/port missing."""
    if not host or not port:
        return None
    from urllib.parse import quote
    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    return f"http://{auth}{host}:{port}"


async def _enrich_account(account, repo: Repository) -> AccountOut:
    """Build AccountOut with computed fields like pending_requests + proxy parts."""
    from releasi.config import get_settings
    out = AccountOut.model_validate(account)
    out.pending_requests = await repo.count_pending_requests_for_account(account.id)
    parsed = _parse_proxy_url(account.proxy_url)
    out.proxy_host = parsed["host"]
    out.proxy_port = parsed["port"]
    out.proxy_username = parsed["username"]
    out.proxy_password_set = parsed["password_set"]
    # Expose the global work window so the UI can tell the user whether the
    # account is currently inside its daily send window.
    settings = get_settings()
    out.work_start_hour = settings.work_start_hour
    out.work_end_hour = settings.work_end_hour
    return out


async def _test_proxy(proxy_url: Optional[str]) -> ProxyTestResponse:
    """Ping ipinfo.io through the proxy to verify it works.

    Returns ok=True with IP + country on success. On auth failure, timeout,
    connection refused, etc. returns ok=False with a human-friendly message.
    Uses httpx (no browser) — safe to call freely, costs no LinkedIn traffic.
    """
    import time
    import httpx
    start = time.monotonic()
    try:
        client_kwargs = {"timeout": 10.0}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get("https://ipinfo.io/json")
        elapsed = int((time.monotonic() - start) * 1000)
        if resp.status_code != 200:
            return ProxyTestResponse(
                ok=False, latency_ms=elapsed,
                error=f"Unexpected status {resp.status_code}",
            )
        data = resp.json()
        return ProxyTestResponse(
            ok=True,
            ip=data.get("ip"),
            country=data.get("country"),
            latency_ms=elapsed,
        )
    except httpx.ProxyError as e:
        elapsed = int((time.monotonic() - start) * 1000)
        msg = str(e).lower()
        if "407" in msg or "authentication" in msg or "auth" in msg:
            err = "Authentication failed — check username/password"
        else:
            err = f"Proxy connection failed: {e}"
        return ProxyTestResponse(ok=False, latency_ms=elapsed, error=err)
    except httpx.ConnectError as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProxyTestResponse(
            ok=False, latency_ms=elapsed,
            error=f"Cannot reach proxy host/port: {e}",
        )
    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProxyTestResponse(
            ok=False, latency_ms=elapsed, error="Connection timed out",
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProxyTestResponse(ok=False, latency_ms=elapsed, error=str(e))


@router.get("/accounts", response_model=List[AccountOut])
async def list_accounts(
    include_archived: bool = Query(False),
    repo: Repository = Depends(get_repo),
):
    accounts = await repo.list_accounts(include_archived=include_archived)
    return [await _enrich_account(a, repo) for a in accounts]


@router.post("/accounts/{account_id}/archive", response_model=AccountOut)
async def archive_account(account_id: str, repo: Repository = Depends(get_repo)):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account = await repo.update_account(account, archived=True, status="cookie_expired")
    return AccountOut.model_validate(account)


@router.post("/accounts/{account_id}/unarchive", response_model=AccountOut)
async def unarchive_account(account_id: str, repo: Repository = Depends(get_repo)):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account = await repo.update_account(account, archived=False)
    return AccountOut.model_validate(account)


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: str, repo: Repository = Depends(get_repo)):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return await _enrich_account(account, repo)


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountCreate, repo: Repository = Depends(get_repo)):
    existing = await repo.get_account_by_name(body.name)
    if existing:
        raise HTTPException(status_code=409, detail="Account name already exists")

    # Assemble proxy_url from components if provided; fall back to legacy
    # proxy_url field (for CLI compatibility).
    proxy_url = body.proxy_url or _assemble_proxy_url(
        body.proxy_host, body.proxy_port, body.proxy_username, body.proxy_password
    )

    account = await repo.create_account(
        name=body.name,
        li_at_cookie=body.li_at_cookie or "",
        li_a_cookie=body.li_a_cookie,
        user_agent=body.user_agent,
        timezone=body.timezone,
        proxy_url=proxy_url,
        proxy_country=body.proxy_country,
    )
    # If no cookie provided, mark as needing login
    if not body.li_at_cookie:
        await repo.update_account(account, status="cookie_expired")
    return await _enrich_account(account, repo)


@router.put("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: str,
    body: AccountUpdate,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Build non-proxy kwargs first (name/timezone/limits/proxy_country/withdraw_threshold)
    sent_fields = body.model_fields_set
    proxy_component_fields = {"proxy_host", "proxy_port", "proxy_username", "proxy_password"}
    kwargs: dict = {}
    for field, value in body.model_dump().items():
        if field in proxy_component_fields:
            continue
        if value is None:
            continue
        kwargs[field] = value

    # Reassemble proxy_url if *any* component field was sent.
    proxy_reassembled = False
    if sent_fields & proxy_component_fields:
        existing_parsed = _parse_proxy_url(account.proxy_url)
        existing_pw = _proxy_password(account.proxy_url)

        # For each component: sent value wins; else keep existing.
        host = body.proxy_host if "proxy_host" in sent_fields else existing_parsed["host"]
        port = body.proxy_port if "proxy_port" in sent_fields else existing_parsed["port"]
        username = (
            body.proxy_username if "proxy_username" in sent_fields
            else existing_parsed["username"]
        )
        password = body.proxy_password if "proxy_password" in sent_fields else existing_pw

        # Empty host/port → clear proxy entirely
        if not host or not port:
            kwargs["proxy_url"] = None
        else:
            kwargs["proxy_url"] = _assemble_proxy_url(host, port, username, password)
        proxy_reassembled = True

    if not kwargs:
        return await _enrich_account(account, repo)

    proxy_changed = (
        proxy_reassembled
        or ("proxy_country" in kwargs and kwargs["proxy_country"] != account.proxy_country)
    )
    account = await repo.update_account(account, **kwargs)
    # Evict pool slot when proxy changes so next acquire launches with the new proxy
    if proxy_changed:
        try:
            from releasi.linkedin.pool import get_browser_pool
            pool = get_browser_pool()
            await pool.evict(account.id)
        except RuntimeError:
            pass  # Pool not initialized
    return await _enrich_account(account, repo)


# ── Proxy test ───────────────────────────────────────────────────────────────

# Static path — must be registered before /{account_id} routes to avoid 405
@router.post("/accounts/test-proxy", response_model=ProxyTestResponse)
async def test_proxy_unsaved(body: ProxyTestRequest):
    """Test proxy credentials submitted via form (no persist, no auth to LinkedIn).

    Used by /accounts/new so users can verify creds before saving.
    """
    proxy_url = _assemble_proxy_url(
        body.proxy_host, body.proxy_port, body.proxy_username, body.proxy_password
    )
    if not proxy_url:
        return ProxyTestResponse(ok=False, error="Host and port are required")
    return await _test_proxy(proxy_url)


@router.post("/accounts/{account_id}/test-proxy", response_model=ProxyTestResponse)
async def test_proxy_stored(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Test the proxy currently stored on the account."""
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.proxy_url:
        return ProxyTestResponse(ok=False, error="No proxy configured for this account")
    return await _test_proxy(account.proxy_url)


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
        from releasi.linkedin.pool import get_browser_pool
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
    """Session check via a real browser through the account's proxy.

    NEVER sends a bare ``li_at`` HTTP request from the server IP. LinkedIn
    treats a cookie request from a non-browser user-agent / datacenter IP as a
    stolen-cookie test and invalidates the session — the old implementation did
    exactly that and was a likely cause of premature expiry.

    Strategy (respects the one-context-per-account invariant):
      • Path A — if the pool already has a live slot for this account, reuse it
        (navigate the feed in the existing context). Never opens a 2nd context.
      • Path B — otherwise launch an ephemeral browser through the account's
        residential proxy and validate the feed there.
    """
    import time
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.li_at_cookie:
        return {"valid": False, "reason": "no_cookie", "elapsed_ms": 0}
    # Only probe accounts the system believes hold a working session. An account
    # that is cookie_expired / suspended / paused has no known-good cookie to
    # validate — and one that was never genuinely connected to LinkedIn (login
    # captured a dead li_at, as with the 16-minute account) has nothing to check.
    # Probing it just burns a browser session. Recovery from cookie_expired
    # happens via re-login or the cookie-health sweep, not this endpoint.
    if account.status != "active":
        status_val = getattr(account.status, "value", account.status)
        return {
            "valid": False,
            "reason": "account_not_active",
            "status": status_val,
            "elapsed_ms": 0,
            "message": (
                f"Account status is '{status_val}', not active — no valid cookie "
                "to check. Re-login to restore the session."
            ),
        }

    start = time.monotonic()

    def _ms() -> int:
        return int((time.monotonic() - start) * 1000)

    # ── Path A: reuse the live pool slot if one exists ───────────────────────
    pool = None
    try:
        from releasi.linkedin.pool import get_browser_pool
        pool = get_browser_pool()
    except RuntimeError:
        pool = None  # pool not initialised (CLI / API-only context)

    if pool is not None and pool.has_slot(account.id):
        if pool.is_busy(account.id):
            return {
                "valid": None,
                "reason": "in_use",
                "elapsed_ms": _ms(),
                "message": "Account browser is busy with automation; try again shortly.",
            }
        ctx = await pool.acquire(account)
        try:
            from releasi.linkedin.navigator import LinkedInNavigator
            page = await ctx.new_page()
            try:
                feed = await LinkedInNavigator(page).go_to_feed()
            finally:
                await page.close()
            if not feed.success:
                return {"valid": None, "reason": "network_error", "error": feed.error, "elapsed_ms": _ms()}
            if not feed.session_valid:
                await repo.update_account(account, status="cookie_expired")
                return {"valid": False, "reason": "redirected_to_login", "elapsed_ms": _ms()}
            return {"valid": True, "elapsed_ms": _ms()}
        finally:
            await pool.release_idle(account.id)

    # ── Path B: ephemeral browser through the account's residential proxy ────
    from releasi.linkedin.browser import LinkedInBrowser
    browser = LinkedInBrowser()
    try:
        await browser.launch(
            account_id=account.id,
            li_at_cookie=account.li_at_cookie,
            user_agent=account.user_agent,
            proxy_url=account.proxy_url,
            proxy_country=account.proxy_country,
            timezone=account.timezone,
        )
        valid = await browser.validate_session()
        if valid:
            return {"valid": True, "elapsed_ms": _ms()}
        await repo.update_account(account, status="cookie_expired")
        return {"valid": False, "reason": "redirected_to_login", "elapsed_ms": _ms()}
    except Exception as e:
        return {"valid": False, "reason": "error", "error": str(e), "elapsed_ms": _ms()}
    finally:
        try:
            await browser.close()
        except Exception:
            pass


@router.post("/accounts/{account_id}/vet-proxy")
async def vet_proxy(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Resolve the egress IP seen through the account's proxy and assess its
    reputation (residential vs datacenter/proxy-flagged).

    Catches the kind of low-reputation IP that kills new accounts within hours
    (e.g. the tr account that died in 16 minutes). Run this before warming a new
    account, or when diagnosing a fast expiry.
    """
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.session_telemetry import egress_identity, vet_proxy_ip

    proxy_url = account.proxy_url
    if not proxy_url and account.proxy_country:
        try:
            from releasi.linkedin.browser import _build_proxy_url
            proxy_url = _build_proxy_url(account.id, account.proxy_country)
        except Exception:
            proxy_url = None

    egress = await egress_identity(proxy_url)
    vet = await vet_proxy_ip(egress.get("egress_ip"))
    return {"egress": egress, "vet": vet}


@router.get("/accounts/{account_id}/session-events")
async def list_session_events(
    account_id: str,
    limit: int = Query(200, le=1000),
    repo: Repository = Depends(get_repo),
):
    """Session-health ledger for an account (most-recent first).

    Each row is what LinkedIn saw at one session touch — egress IP/geo, cookie
    fingerprint (li_at_fp changes ⇒ rotation), li_rm presence, fingerprint, and
    outcome. Use it to answer *why* a cookie expired: diff the rows in the hours
    before status flipped to cookie_expired (did the IP/ASN change? did li_rm
    disappear? was the cookie a short-capture?).
    """
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    events = await repo.list_session_events(account_id=account_id, limit=limit)
    return [
        {
            "id": e.id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "job": e.job,
            "result": e.result,
            "egress_ip": e.egress_ip,
            "geo": e.geo,
            "asn": e.asn,
            "li_at_fp": e.li_at_fp,
            "li_at_expires_at": e.li_at_expires_at.isoformat() if e.li_at_expires_at else None,
            "has_li_rm": e.has_li_rm,
            "cookie_names": e.cookie_names,
            "user_agent": e.user_agent,
            "timezone": e.timezone,
            "consecutive_session_errors": e.consecutive_session_errors,
            "detail": e.detail,
        }
        for e in events
    ]


@router.post("/accounts/{account_id}/login-session")
async def start_login_session(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()

    # Auto-cleanup any stale session before starting a new one
    if manager.is_active:
        await manager._cleanup()

    # Build proxy URL so the login browser uses the account's residential proxy,
    # not the datacenter IP. LinkedIn records the login location.
    proxy_url = account.proxy_url
    if not proxy_url and account.proxy_country:
        try:
            from releasi.linkedin.browser import _build_proxy_url
            proxy_url = _build_proxy_url(account.id, account.proxy_country)
        except Exception:
            pass

    try:
        novnc_path = await manager.start_session(account_id, proxy_url=proxy_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start login session: {e}")

    return {"novnc_url": novnc_path, "account_id": account_id}


@router.post("/accounts/{account_id}/login-session/finish")
async def finish_login_session(
    account_id: str,
    background_tasks: BackgroundTasks,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()

    if not manager.is_active:
        raise HTTPException(status_code=400, detail="No active login session")

    try:
        result = await manager.finish_session()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to finish session: {e}")

    if not result["li_at"]:
        return {"success": False, "message": "No li_at cookie found. Did you complete the login?"}

    # Save cookies to database — including the long-term li_rm token, the full
    # cookie jar, and the li_at expiry, so the captured session is complete and
    # the dashboard can surface a short-capture cookie immediately.
    update_kwargs = {"li_at_cookie": result["li_at"], "status": "active"}
    if result.get("li_a"):
        update_kwargs["li_a_cookie"] = result["li_a"]
    if result.get("li_rm"):
        update_kwargs["li_rm_cookie"] = result["li_rm"]
    if result.get("cookies_json"):
        update_kwargs["cookies_json"] = result["cookies_json"]
    li_at_exp_dt = None
    if result.get("li_at_expires_at"):
        from datetime import datetime as _dt
        try:
            li_at_exp_dt = _dt.fromisoformat(result["li_at_expires_at"])
            update_kwargs["li_at_expires_at"] = li_at_exp_dt
        except Exception:
            li_at_exp_dt = None
    await repo.update_account(account, **update_kwargs)

    # Ledger: record the login touch (did we get li_rm / a long-lived cookie?).
    try:
        from releasi.linkedin.session_telemetry import li_at_fingerprint
        await repo.log_session_event(
            account_id=account.id,
            job="login",
            result="ok",
            li_at_fp=li_at_fingerprint(result["li_at"]),
            li_at_expires_at=li_at_exp_dt,
            has_li_rm=bool(result.get("li_rm")),
            detail={"source": "novnc_login"},
        )
    except Exception:
        pass

    # Evict old pool slot so the pool creates a fresh browser with the new cookie
    # on next acquire (avoids stale cookie / fingerprint mismatch)
    try:
        from releasi.linkedin.pool import get_browser_pool
        pool = get_browser_pool()
        await pool.evict(account.id)
    except RuntimeError:
        pass  # Pool not initialized (CLI context)

    # Validate the new session immediately via proxy HTTP check in the background.
    # This confirms the cookie works and the proxy route is healthy, and will
    # auto-recover the account to ACTIVE status if the health check passes.
    async def _validate_after_login(acct_id: str):
        try:
            from releasi.scheduler.runner import check_cookie_health
            await check_cookie_health()
            logger.info("post_login.health_check_done", account_id=acct_id)
        except Exception as e:
            logger.warning("post_login.health_check_failed", account_id=acct_id, error=str(e))

    import structlog as _structlog
    logger = _structlog.get_logger()
    background_tasks.add_task(_validate_after_login, account_id)

    return {"success": True, "message": "Cookies extracted and saved successfully"}


@router.post("/accounts/{account_id}/browse-session")
async def start_browse_session(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Start a noVNC browser session pre-authenticated as the account.

    The account's li_at cookie is injected before navigation so the user
    lands directly on the LinkedIn feed (via the account's proxy). This is
    for manual inspection/debugging — no cookies are saved on close.
    """
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()

    # Auto-cleanup any stale session
    if manager.is_active:
        await manager._cleanup()

    proxy_url = account.proxy_url
    if not proxy_url and account.proxy_country:
        try:
            from releasi.linkedin.browser import _build_proxy_url
            proxy_url = _build_proxy_url(account.id, account.proxy_country)
        except Exception:
            pass

    try:
        novnc_path = await manager.start_session(
            account_id,
            proxy_url=proxy_url,
            li_at_cookie=account.li_at_cookie or None,
            start_url="https://www.linkedin.com/feed/",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start browse session: {e}")

    return {"novnc_url": novnc_path, "account_id": account_id}


@router.get("/accounts/{account_id}/browse-session/status")
async def browse_session_status(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Check if a browse session is currently active for this account."""
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()
    active = manager.is_active and manager._account_id == account_id
    return {"active": active}


@router.post("/accounts/{account_id}/browse-session/close")
async def close_browse_session(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Close an active browse session without saving any cookies."""
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.login_session import LoginSessionManager
    manager = LoginSessionManager.get_instance()

    if not manager.is_active:
        return {"success": True, "message": "No active session"}

    await manager._cleanup()
    return {"success": True, "message": "Browse session closed"}


@router.post("/accounts/{account_id}/login-session/cancel")
async def cancel_login_session(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.login_session import LoginSessionManager
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
        from releasi.linkedin.pool import get_browser_pool
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
        from releasi.linkedin.browser import LinkedInBrowser
        browser = LinkedInBrowser()
        try:
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
                raise HTTPException(status_code=400, detail="Session invalid — cookie may be expired")
            avatar_path = await browser.save_avatar(account.id)
            if avatar_path:
                await repo.update_account(account, avatar_path=avatar_path)
                return {"success": True, "avatar_path": avatar_path}
            return {"success": False, "message": "Could not find profile photo on page"}
        finally:
            await browser.close()


@router.post("/accounts/{account_id}/replan")
async def replan_account(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Reset today's scheduled leads and regenerate the daily plan with current settings."""
    from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
    from sqlalchemy import update as _sql_update
    from releasi.db.models import Lead, LeadStatus, ActionType, ActionLogStatus
    from releasi.scheduler.planner import generate_daily_plan, SlotType
    import structlog as _structlog
    logger = _structlog.get_logger()

    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Continuous-mode accounts have no plan to regenerate — leads stay PENDING
    # and the dispatcher picks them up organically.
    if account.dispatch_mode == "continuous":
        return {"ok": True, "scheduled": 0, "mode": "continuous"}

    campaigns = await repo.get_active_campaigns(account.id)
    total_scheduled = 0

    for campaign in campaigns:
        # Reset today's SCHEDULED assignments back to PENDING
        # Phase 3b: uses repo helper which now writes to CampaignLeadAssignment only
        await repo.reset_stale_scheduled_leads(campaign.id)

        pending = await repo.get_pending_leads_via_assignments(campaign.id)
        if not pending:
            continue

        now = _datetime.utcnow()
        pending_ids = [l.id for l in pending]

        sent_today = await repo.get_daily_requests_sent(account.id)
        remaining_budget = max(0, account.daily_limit - sent_today)

        if remaining_budget == 0:
            logger.info(
                "replan.budget_exhausted",
                account=account.name,
                campaign=campaign.name,
                daily_limit=account.daily_limit,
                sent_today=sent_today,
            )
            continue

        plan = generate_daily_plan(
            account_id=account.id,
            day=_date.today(),
            pending_lead_ids=pending_ids,
            daily_limit=account.daily_limit,
            timezone_str=account.timezone,
            campaign_weekend_enabled=campaign.weekend_enabled,
            effective_start=now + _timedelta(minutes=2),
            remaining_budget=remaining_budget,
        )

        # Schedule only future slots from today's plan.
        today_scheduled = 0
        first_lead_id = None  # track for immediate dispatch
        for slot in plan:
            if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                if slot.scheduled_at <= now:
                    continue  # already past — skip
                await repo.update_lead_schedule(slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED)
                total_scheduled += 1
                today_scheduled += 1
                if first_lead_id is None:
                    first_lead_id = slot.lead_id

        if today_scheduled == 0:
            # It's too late in the day for any connection slots — schedule for
            # tomorrow so leads aren't stuck as PENDING until the next 06:00 run.
            tomorrow = _date.today() + _timedelta(days=1)
            tomorrow_plan = generate_daily_plan(
                account_id=account.id,
                day=tomorrow,
                pending_lead_ids=pending_ids,
                daily_limit=account.daily_limit,
                timezone_str=account.timezone,
                campaign_weekend_enabled=campaign.weekend_enabled,
            )
            for slot in tomorrow_plan:
                if slot.slot_type == SlotType.CONNECTION_REQUEST and slot.lead_id:
                    await repo.update_lead_schedule(slot.lead_id, slot.scheduled_at, LeadStatus.SCHEDULED)
                    total_scheduled += 1
                    if first_lead_id is None:
                        first_lead_id = slot.lead_id

        # Always queue one lead immediately so the next dispatcher cycle (≤5 min)
        # fires a real request, letting you spot issues fast after a settings change.
        if first_lead_id:
            immediate_time = now + _timedelta(seconds=30)
            await repo.update_lead_schedule(first_lead_id, immediate_time, LeadStatus.SCHEDULED)

    return {"ok": True, "scheduled": total_scheduled}


@router.get("/accounts/{account_id}/schedule", response_model=List[ScheduleSlotOut])
async def account_schedule(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Compute today's date boundaries in UTC based on account timezone
    tz = ZoneInfo(account.timezone) if account.timezone else ZoneInfo("UTC")
    now_local = _dt.now(tz)
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local.replace(hour=23, minute=59, second=59)
    day_start_utc = day_start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    day_end_utc = day_end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    slots = await repo.get_todays_schedule(account_id, day_start_utc, day_end_utc)
    return [ScheduleSlotOut(**s) for s in slots]


@router.get("/accounts/{account_id}/health", response_model=AccountHealthOut)
async def account_health(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    stats = await repo.get_account_health_stats(account_id)
    return AccountHealthOut(**stats)


@router.get("/accounts/{account_id}/activity", response_model=List[ActionLogOut])
async def account_activity(
    account_id: str,
    limit: int = Query(100, le=500),
    repo: Repository = Depends(get_repo),
):
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    logs = await repo.list_action_log(account_id=account_id, limit=limit)
    lead_ids = [l.lead_id for l in logs if l.lead_id]
    leads_map = await repo.get_leads_by_ids(lead_ids)
    result = []
    for l in logs:
        out = ActionLogOut.model_validate(l)
        if l.lead_id and l.lead_id in leads_map:
            lead = leads_map[l.lead_id]
            out.lead_first_name = lead.first_name
            out.lead_last_name = lead.last_name
            out.lead_url = lead.linkedin_url
        result.append(out)
    return result


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


# ── Invitation withdrawal ─────────────────────────────────────────────────────

import uuid as _uuid

# In-memory task store: task_id → result dict. Lives for the container lifetime.
_withdrawal_tasks: dict = {}


@router.post("/accounts/{account_id}/invitations/count")
async def get_invitation_count(
    account_id: str,
    repo: Repository = Depends(get_repo),
):
    """Navigate to LinkedIn invitation manager and return the People count. ~5-10s."""
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from releasi.linkedin.pool import get_browser_pool
    from releasi.linkedin.actions import LinkedInActions

    pool = get_browser_pool()
    try:
        pool_context = await pool.acquire(account)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Browser pool unavailable: {e}")

    try:
        page = await pool_context.new_page()
        try:
            actions = LinkedInActions(page)
            count = await actions.get_pending_invitation_count()
        finally:
            await page.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await pool.release_idle(account.id)

    if count == -1:
        raise HTTPException(status_code=503, detail="Session invalid or navigation failed")

    return {"count": count, "account_id": account_id}


@router.post("/accounts/{account_id}/invitations/withdraw")
async def start_withdrawal(
    account_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    repo: Repository = Depends(get_repo),
):
    """
    Start a background withdrawal job. Returns task_id immediately.
    body: { "count": int (1-100), "order": "oldest" | "newest" }
    """
    account = await repo.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    count = int(body.get("count", 10))
    order = body.get("order", "oldest")
    if count < 1 or count > 100:
        raise HTTPException(status_code=422, detail="count must be between 1 and 100")
    if order not in ("oldest", "newest"):
        raise HTTPException(status_code=422, detail="order must be 'oldest' or 'newest'")

    task_id = f"wd-{_uuid.uuid4().hex[:8]}"
    _withdrawal_tasks[task_id] = {"status": "running", "withdrawn": [], "db_updated": 0, "error": None}

    async def _run(tid: str, acct, n: int, ord_: str):
        from releasi.linkedin.pool import get_browser_pool
        from releasi.linkedin.actions import LinkedInActions
        import re as re_
        import logging as _logging
        _log = _logging.getLogger(__name__)

        urls: list = []
        db_updated = 0
        error_msg = None
        try:
            pool = get_browser_pool()
            pool_context = await pool.acquire(acct)
            try:
                page = await pool_context.new_page()
                try:
                    actions = LinkedInActions(page)
                    urls = await actions.withdraw_invitations(n, order=ord_)
                finally:
                    await page.close()
            finally:
                await pool.release_idle(acct.id)

            # Sync withdrawn URLs to DB leads
            for url in urls:
                m = re_.search(r'/in/([^/?#\s]+)', url)
                if not m:
                    continue
                slug = m.group(1).rstrip('/')
                updated = await repo.mark_lead_withdrawn_by_slug(slug)
                if updated:
                    db_updated += 1

        except Exception as e:
            _log.error("withdrawal_task.error tid=%s error=%s", tid, e, exc_info=True)
            error_msg = str(e)

        _withdrawal_tasks[tid] = {
            "status": "error" if error_msg else "done",
            "withdrawn": urls,
            "db_updated": db_updated,
            "error": error_msg,
        }

    background_tasks.add_task(_run, task_id, account, count, order)
    return {"task_id": task_id}


@router.get("/accounts/{account_id}/invitations/withdraw/{task_id}")
async def get_withdrawal_status(account_id: str, task_id: str):
    """Poll for withdrawal task status."""
    task = _withdrawal_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

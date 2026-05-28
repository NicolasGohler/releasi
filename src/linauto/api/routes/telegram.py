"""Telegram resolver endpoint — standalone enrichment, no DB involvement."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from linauto.api.auth import require_api_key
from linauto.api.schemas import TelegramResolveRequest, TelegramResolveResponse

router = APIRouter(dependencies=[Depends(require_api_key)])

_DEFAULT_TIMEOUT = 50   # seconds — well under a typical 60s client timeout
_DEFAULT_MAX_CANDIDATES = 12


@router.post("/telegram/resolve", response_model=TelegramResolveResponse)
async def resolve_telegram(body: TelegramResolveRequest) -> TelegramResolveResponse:
    """Resolve a Telegram username for a person.

    Runs the two-pass resolver (Twitter handle check → name/company pattern
    matching) and returns candidates. No DB reads or writes — pure enrichment.

    Callers should call this once per person sequentially. Set your HTTP
    client timeout to at least 60s. The server caps resolution at max_seconds
    (default 50s) and returns null cleanly rather than holding the connection.

    Optional request fields:
      max_seconds    — hard server-side cap in seconds (default 50, max 90)
      max_candidates — Pass 2 candidate limit (default 12)

    Auth: Bearer token (LINAUTO_API_KEY).
    """
    from linauto.config import get_settings
    from linauto.telegram.resolver import find_telegram

    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash or not settings.telegram_session:
        raise HTTPException(
            status_code=503,
            detail=(
                "Telegram credentials not configured on the server. "
                "Set LINAUTO_TELEGRAM_API_ID, LINAUTO_TELEGRAM_API_HASH, "
                "and LINAUTO_TELEGRAM_SESSION."
            ),
        )

    timeout = min(body.max_seconds or _DEFAULT_TIMEOUT, 90)
    max_candidates = body.max_candidates or _DEFAULT_MAX_CANDIDATES

    try:
        result = await asyncio.wait_for(
            find_telegram(
                name=body.name,
                twitter_url=body.twitter_url,
                company=body.company,
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_str=settings.telegram_session,
                max_candidates=max_candidates,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return TelegramResolveResponse(
            best_match=None,
            alternatives=[],
            logs=[f"Resolution timed out after {timeout}s — no match found in time"],
        )

    return TelegramResolveResponse(
        best_match=result.best_match,
        alternatives=result.alternatives,
        logs=result.logs,
    )

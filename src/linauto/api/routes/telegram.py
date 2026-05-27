"""Telegram resolver endpoint — standalone enrichment, no DB involvement."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from linauto.api.auth import require_api_key
from linauto.api.schemas import TelegramResolveRequest, TelegramResolveResponse

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/telegram/resolve", response_model=TelegramResolveResponse)
async def resolve_telegram(body: TelegramResolveRequest) -> TelegramResolveResponse:
    """Resolve a Telegram username for a person.

    Runs the two-pass resolver (Twitter handle check → name/company pattern
    matching) and returns candidates. No DB reads or writes — pure enrichment.

    Callers should call this once per person sequentially. A single resolution
    takes 5–90s depending on how many username candidates are tried before a
    hit. Set your HTTP client timeout to at least 120s.

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

    result = await find_telegram(
        name=body.name,
        twitter_url=body.twitter_url,
        company=body.company,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        session_str=settings.telegram_session,
    )

    return TelegramResolveResponse(
        best_match=result.best_match,
        alternatives=result.alternatives,
        logs=result.logs,
    )

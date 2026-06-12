"""Telegram resolver endpoints — standalone enrichment, no DB involvement."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException

from releasi.api.auth import require_api_key
from releasi.api.schemas import (
    TelegramResolveRequest,
    TelegramResolveResponse,
    TelegramResolveBatchRequest,
    TelegramResolveBatchResult,
    TelegramResolveBatchStatus,
)

router = APIRouter(dependencies=[Depends(require_api_key)])

# In-memory store for batch jobs.
# { task_id: { status, total, completed, results: [...], error } }
_batch_tasks: dict[str, dict] = {}

_DEFAULT_TIMEOUT = 50   # seconds per person — well under a typical 60s client timeout


def _get_settings_and_check():
    """Return settings or raise 503 if Telegram credentials are missing."""
    from releasi.config import get_settings
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash or not settings.telegram_session:
        raise HTTPException(
            status_code=503,
            detail=(
                "Telegram credentials not configured on the server. "
                "Set RELEASI_TELEGRAM_API_ID, RELEASI_TELEGRAM_API_HASH, "
                "and RELEASI_TELEGRAM_SESSION."
            ),
        )
    return settings


async def _resolve_one(
    body: TelegramResolveRequest,
    settings,
    sleep_between: float = 1.5,
) -> TelegramResolveResponse:
    """Run the resolver for a single person and return a response object."""
    from releasi.telegram.resolver import find_telegram

    timeout = min(body.max_seconds or _DEFAULT_TIMEOUT, 90)

    try:
        result = await asyncio.wait_for(
            find_telegram(
                name=body.name,
                twitter_url=body.twitter_url,
                company=body.company,
                linkedin_url=body.linkedin_url,
                exclude_usernames=body.exclude_usernames,
                max_candidates=body.max_candidates,
                sleep_between=sleep_between,
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                session_str=settings.telegram_session,
            ),
            timeout=timeout,
        )
        return TelegramResolveResponse(
            best_match=result.best_match,
            alternatives=result.alternatives,
            logs=result.logs,
            timed_out=False,
            flood_wait_seconds=result.flood_wait_seconds,
        )
    except asyncio.TimeoutError:
        return TelegramResolveResponse(
            best_match=None,
            alternatives=[],
            logs=[f"Resolution timed out after {timeout}s — no match found in time"],
            timed_out=True,
        )


# ── Single-person endpoint ────────────────────────────────────────────────

@router.post("/telegram/resolve", response_model=TelegramResolveResponse)
async def resolve_telegram(body: TelegramResolveRequest) -> TelegramResolveResponse:
    """Resolve a Telegram username for a single person.

    Runs the two-pass resolver (Twitter handle check → name/company pattern
    matching) and returns candidates. No DB reads or writes — pure enrichment.

    Candidate ordering: company-specific patterns → full-name patterns
    (johndoe, john_doe) → generic short patterns (johnd, jdoe).
    Exits early as soon as a high-confidence match is found (first + last
    name both match on the Telegram profile).

    Optional request fields:
      exclude_usernames — handles already verified as wrong; skipped in both passes
      max_seconds       — hard server-side cap in seconds (default 50, max 90)
      max_candidates    — Pass 2 candidate limit (default: no cap)

    Callers must call sequentially — one person at a time. Set HTTP client
    timeout to at least 60s. Returns timed_out=true rather than an error if
    the server cap is hit.

    Auth: Bearer token (RELEASI_API_KEY).
    """
    settings = _get_settings_and_check()
    return await _resolve_one(body, settings)


# ── Batch endpoint ────────────────────────────────────────────────────────

@router.post("/telegram/resolve/batch", response_model=TelegramResolveBatchStatus)
async def resolve_telegram_batch(body: TelegramResolveBatchRequest) -> TelegramResolveBatchStatus:
    """Start a batch Telegram resolution job.

    Accepts a list of people and processes them sequentially in the background
    (one Telegram session — parallel resolution is not safe). Returns a task_id
    immediately; poll GET /telegram/resolve/batch/{task_id} for progress.

    Results are appended as each person completes — partial results are
    available during the run, not just at the end.

    Each person in the list accepts the same optional fields as the single
    endpoint: exclude_usernames, max_seconds, max_candidates.

    Auth: Bearer token (RELEASI_API_KEY).
    """
    if not body.people:
        raise HTTPException(status_code=422, detail="people list is empty")

    settings = _get_settings_and_check()
    task_id = str(uuid.uuid4())
    _batch_tasks[task_id] = {
        "status": "running",
        "total": len(body.people),
        "completed": 0,
        "results": [],
        "error": None,
    }

    async def _run_batch() -> None:
        task = _batch_tasks[task_id]
        try:
            for person in body.people:
                resp = await _resolve_one(person, settings, sleep_between=2.0)
                if resp.flood_wait_seconds:
                    wait = resp.flood_wait_seconds + 10
                    task["flood_wait_until"] = wait  # seconds remaining, for status polling
                    await asyncio.sleep(wait)
                    task.pop("flood_wait_until", None)
                    resp = await _resolve_one(person, settings, sleep_between=2.0)
                task["results"].append({
                    "name": person.name,
                    "best_match": resp.best_match,
                    "alternatives": resp.alternatives,
                    "logs": resp.logs,
                    "timed_out": resp.timed_out,
                    "flood_wait_seconds": resp.flood_wait_seconds,
                })
                task["completed"] += 1
            task["status"] = "done"
        except Exception as exc:
            task["status"] = "error"
            task["error"] = str(exc)

    asyncio.create_task(_run_batch())

    return TelegramResolveBatchStatus(
        task_id=task_id,
        status="running",
        total=len(body.people),
        completed=0,
    )


@router.get("/telegram/resolve/batch/{task_id}", response_model=TelegramResolveBatchStatus)
async def get_batch_status(task_id: str) -> TelegramResolveBatchStatus:
    """Poll the status of a batch Telegram resolution job.

    Returns partial results as they complete — completed count increments
    with each person resolved. Results list grows in the same order as the
    input people list.

    Auth: Bearer token (RELEASI_API_KEY).
    """
    task = _batch_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Batch task not found")

    return TelegramResolveBatchStatus(
        task_id=task_id,
        status=task["status"],
        total=task["total"],
        completed=task["completed"],
        results=[TelegramResolveBatchResult(**r) for r in task["results"]],
        error=task["error"],
    )

"""FastAPI dependencies — DB session and repository."""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from fastapi import Header

from releasi.db.engine import get_session_factory
from releasi.db.repository import Repository


async def get_repo() -> AsyncGenerator[Repository, None]:
    session = get_session_factory()()
    try:
        yield Repository(session)
    finally:
        await session.close()


async def get_current_user_id(
    x_releasi_user_id: Optional[str] = Header(default=None),
) -> Optional[str]:
    """Resolve the acting dashboard user from the proxy-forwarded header.

    The dashboard's Next.js proxy verifies the signed session cookie server-side
    and injects ``X-Releasi-User-Id``; the browser can't set it (the proxy
    strips any inbound copy). Direct API-key callers and the scheduler don't set
    it, so this returns None → the action is attributed to "System".
    """
    return x_releasi_user_id or None

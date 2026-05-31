"""FastAPI dependencies — DB session and repository."""
from __future__ import annotations

from typing import AsyncGenerator

from releasi.db.engine import get_session_factory
from releasi.db.repository import Repository


async def get_repo() -> AsyncGenerator[Repository, None]:
    session = get_session_factory()()
    try:
        yield Repository(session)
    finally:
        await session.close()

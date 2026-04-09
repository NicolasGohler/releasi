"""Health check endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from linauto.api.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()

"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from linauto.config import get_settings


def _client_ip_key(request: Request) -> str:
    """Prefer X-Forwarded-For (set by the Next.js proxy) over the socket peer.

    After the dashboard proxy refactor, all browser traffic reaches the backend
    via Vercel's edge, so the socket peer is always a Vercel IP. X-Forwarded-For
    (which the proxy route handler forwards from the original client) is the
    only way to rate-limit per real user.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Linauto API",
        version="0.1.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )

    # CORS — fail closed. Browser traffic goes through the Next.js server
    # proxy (same-origin), so CORS is only relevant if someone points another
    # origin at the backend directly. Allow only the explicitly configured
    # origins; no wildcard fallback.
    origins = settings.cors_origins or []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting — 120 req/min per client IP (X-Forwarded-For aware).
    # Scheduler jobs run in-process and never hit HTTP, so they bypass this.
    limiter = Limiter(key_func=_client_ip_key, default_limits=["120/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # Mount routes
    from linauto.api.routes import health, accounts, campaigns, leads, lead_lists, stats

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(accounts.router, prefix="/api/v1")
    app.include_router(accounts.public_router, prefix="/api/v1")
    app.include_router(campaigns.router, prefix="/api/v1")
    app.include_router(leads.router, prefix="/api/v1")
    app.include_router(lead_lists.router, prefix="/api/v1")
    app.include_router(stats.router, prefix="/api/v1")

    return app

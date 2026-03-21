"""Bearer token authentication middleware."""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Endpoints that do not require authentication
PUBLIC_PATHS = frozenset({
    "/health",
    "/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/hologram/state",
    "/api/status",
    "/api/system",
    "/api/consciousness",
    "/api/learning",
    "/api/micro_model",
    "/api/ops",
    "/api/feeds",
    "/api/agent_levels",
    "/api/swarm/scores",
    "/api/monitor/history",
})


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require Bearer token for /api/* routes (except public paths).

    CRITICAL: This middleware NEVER generates keys, NEVER reads env.
    It receives api_key via constructor only.
    WaggleSettings.from_env() is the sole key generator.
    """

    def __init__(self, app, api_key: str) -> None:  # noqa: ANN001
        super().__init__(app)
        if not api_key:
            raise ValueError(
                "api_key must be resolved before middleware setup. "
                "Use WaggleSettings.from_env() to generate a default key."
            )
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        """Check Bearer token for protected routes."""
        path = request.url.path

        # Public endpoints -- no auth required
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # Non-API paths (static files, root, etc.) -- no auth required
        if not path.startswith("/api/"):
            # WebSocket upgrade with token query param
            if path == "/ws":
                token = request.query_params.get("token", "")
                if token != self._api_key:
                    return JSONResponse(
                        {"error": "Unauthorized"},
                        status_code=401,
                    )
                return await call_next(request)
            return await call_next(request)

        # Extract Bearer token from Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = ""

        if token != self._api_key:
            return JSONResponse(
                {"error": "Unauthorized", "detail": "Valid Bearer token required"},
                status_code=401,
            )

        return await call_next(request)

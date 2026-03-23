"""
WaggleDance — API Authentication Middleware
============================================
Bearer token authentication for all /api/* routes.

On first startup, generates a random token and saves to .env as WAGGLE_API_KEY.
Exempt endpoints: /health, /ready, /api/health, /api/status (public).
"""

import logging
import os
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("waggledance.auth")

# Endpoints that don't require authentication
PUBLIC_PATHS = frozenset({
    "/health",
    "/ready",
    "/api/health",
    "/api/status",
    "/api/hologram/state",
})


def get_or_create_api_key(env_path: str = ".env") -> str:
    """Read WAGGLE_API_KEY from env or generate + persist a new one."""
    key = os.environ.get("WAGGLE_API_KEY", "").strip()
    if key:
        return key

    # Try reading from .env file
    env_file = Path(env_path)
    try:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("WAGGLE_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    if key:
                        os.environ["WAGGLE_API_KEY"] = key
                        log.info("Loaded API key from .env file")
                        return key
    except Exception as e:
        log.warning(f"Could not read .env: {e}")

    # Generate new key
    key = secrets.token_urlsafe(32)

    # Persist to .env
    try:
        existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        if "WAGGLE_API_KEY" not in existing:
            separator = "\n" if existing and not existing.endswith("\n") else ""
            with open(env_file, "a", encoding="utf-8") as f:
                f.write(f"{separator}\n# API authentication (auto-generated)\nWAGGLE_API_KEY={key}\n")
            log.info(f"Generated new API key, saved to {env_file}")
    except Exception as e:
        log.warning(f"Could not persist API key to {env_file}: {e}")

    os.environ["WAGGLE_API_KEY"] = key
    return key


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require Bearer token for /api/* routes (except public paths)."""

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Public endpoints — no auth required
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # Non-API paths (static files, root, etc.) — no auth
        if not path.startswith("/api/"):
            # WebSocket upgrade with token query param
            if path == "/ws":
                token = request.query_params.get("token", "")
                if token != self.api_key:
                    return JSONResponse(
                        {"error": "Unauthorized"},
                        status_code=401
                    )
                return await call_next(request)
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = ""

        if token != self.api_key:
            return JSONResponse(
                {"error": "Unauthorized", "detail": "Valid Bearer token required"},
                status_code=401
            )

        return await call_next(request)

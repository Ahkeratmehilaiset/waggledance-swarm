"""Bearer token authentication middleware."""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Endpoints that do not require authentication
PUBLIC_PATHS = frozenset({
    "/health",
    "/healthz",
    "/ready",
    "/readyz",
    "/version",
    "/metrics",
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
    "/api/auth/check",
    "/api/agent_levels",
    "/api/swarm/scores",
    "/api/monitor/history",
    "/api/profile/impact",
    "/api/capabilities/state",
    "/api/learning/state-machine",
})


# RFC 6750 / RFC 7235: a 401 that requires Bearer auth MUST include
# a ``WWW-Authenticate`` header naming the scheme. curl, httpie,
# OAuth client libraries and many reverse proxies use this header to
# decide how to prompt for credentials.
_WWW_AUTH_BEARER = 'Bearer realm="waggledance", charset="UTF-8"'


def _unauthorized(detail: str, *, missing: bool) -> JSONResponse:
    """Build an RFC-compliant 401 response with an operator-friendly
    hint body. ``missing`` differentiates "no Authorization header sent
    at all" from "header sent but the token did not match" so clients
    can log the right diagnostic without guessing."""
    body = {
        "error": "Unauthorized",
        "detail": detail,
        "hint": (
            "Send an 'Authorization: Bearer <api_key>' header. "
            "The api_key is printed to the startup banner and is "
            "available in the .env file as WAGGLE_API_KEY."
        ),
        "reason": "missing_credentials" if missing else "invalid_credentials",
    }
    return JSONResponse(
        body,
        status_code=401,
        headers={"WWW-Authenticate": _WWW_AUTH_BEARER},
    )


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
            # NOTE: WebSocket auth is handled in the route handler
            # (compat_dashboard.websocket_endpoint) because Starlette's
            # BaseHTTPMiddleware does NOT intercept WebSocket upgrades.
            # This HTTP-level check is kept as defence-in-depth for
            # non-WebSocket requests to /ws (e.g. plain GET).
            if path == "/ws":
                token = request.query_params.get("token", "")
                if not token:
                    return _unauthorized(
                        "WebSocket upgrade requires ?token=<api_key>",
                        missing=True,
                    )
                if token != self._api_key:
                    return _unauthorized(
                        "WebSocket token did not match api_key",
                        missing=False,
                    )
                return await call_next(request)
            return await call_next(request)

        # Extract Bearer token from Authorization header
        auth_header = request.headers.get("authorization", "")
        header_present = bool(auth_header)
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = ""

        if token != self._api_key:
            # Fallback: check HttpOnly session cookie
            from waggledance.adapters.http.routes.auth_session import validate_session

            session_id = request.cookies.get("waggle_session", "")
            if validate_session(session_id):
                return await call_next(request)

            if not header_present:
                return _unauthorized(
                    "No Authorization header on a protected /api/ route",
                    missing=True,
                )
            if not auth_header.startswith("Bearer "):
                return _unauthorized(
                    "Authorization header present but scheme is not 'Bearer'",
                    missing=False,
                )
            return _unauthorized(
                "Bearer token did not match the configured api_key",
                missing=False,
            )

        return await call_next(request)

"""HttpOnly session cookie authentication — no API key in the browser.

The long-lived backend API key (WAGGLE_API_KEY) NEVER reaches the browser.
Instead, authenticated dashboard visits set an opaque session cookie
(random token, HttpOnly, SameSite=Strict, 1h TTL).  The hologram page
uses this cookie transparently for chat, WebSocket, and enriched feeds.
"""

import secrets
import threading
import time

from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["auth"])

_sessions: dict[str, float] = {}  # session_id -> expires_at
_lock = threading.Lock()
SESSION_TTL = 3600  # 1 hour


def create_session() -> str:
    """Create a new session. Returns opaque session_id (NOT the API key)."""
    _cleanup()
    sid = secrets.token_urlsafe(32)
    with _lock:
        _sessions[sid] = time.time() + SESSION_TTL
    return sid


def validate_session(sid: str | None) -> bool:
    """Check if session exists and is not expired."""
    if not sid:
        return False
    with _lock:
        exp = _sessions.get(sid)
    return exp is not None and exp > time.time()


def destroy_session(sid: str) -> None:
    """Remove a session."""
    with _lock:
        _sessions.pop(sid, None)


def _cleanup() -> None:
    """Remove expired sessions."""
    now = time.time()
    with _lock:
        expired = [k for k, v in _sessions.items() if v <= now]
        for k in expired:
            del _sessions[k]


# ── Endpoints ────────────────────────────────────────────

@router.post("/api/auth/session")
async def create_session_endpoint(response: Response):
    """Create session cookie. Protected by BearerAuthMiddleware (requires master key).

    Called by dashboard.py server-side — browser never sees the master key.
    """
    sid = create_session()
    response.set_cookie(
        key="waggle_session",
        value=sid,
        httponly=True,
        samesite="strict",
        max_age=SESSION_TTL,
        path="/",
    )
    return {"authenticated": True, "expires_in": SESSION_TTL}


@router.get("/api/auth/check")
async def check_auth(request: Request):
    """Public endpoint. Returns auth status from session cookie."""
    sid = request.cookies.get("waggle_session", "")
    return {"authenticated": validate_session(sid)}


@router.delete("/api/auth/session")
async def logout(request: Request, response: Response):
    """Clear session cookie."""
    sid = request.cookies.get("waggle_session", "")
    if sid:
        destroy_session(sid)
    response.delete_cookie("waggle_session", path="/")
    return {"authenticated": False}

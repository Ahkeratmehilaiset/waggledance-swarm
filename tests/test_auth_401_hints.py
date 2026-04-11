"""Post-gate release-polish tests: F2-002 401 response ergonomics.

The goal is that an operator curling a protected endpoint without a
Bearer header gets an RFC-compliant ``WWW-Authenticate`` response
header AND a JSON body that explicitly names the Authorization
header + the api_key source, so the fix path is one copy-paste away.

These lock in three invariants:

1. Every 401 response from ``BearerAuthMiddleware`` carries a
   ``WWW-Authenticate: Bearer realm=...`` header.
2. The JSON body distinguishes ``missing_credentials`` from
   ``invalid_credentials`` (operators need to tell "curl forgot the
   header" from "wrong key").
3. No branch of the middleware ever leaks the real api_key into the
   response body, headers, or detail string.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.http.middleware.auth import BearerAuthMiddleware


API_KEY = "f2002-sentinel-do-not-leak-zzz"


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/chat")
    async def chat():
        return {"response": "hello"}

    @app.get("/ws")
    async def ws():
        return {"ws": True}

    app.add_middleware(BearerAuthMiddleware, api_key=API_KEY)
    return app


def _client() -> TestClient:
    return TestClient(_make_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# WWW-Authenticate header (RFC 6750 / 7235)
# ---------------------------------------------------------------------------

def test_401_carries_www_authenticate_bearer_header():
    r = _client().post("/api/chat")
    assert r.status_code == 401
    www = r.headers.get("www-authenticate", "")
    assert www.startswith("Bearer"), (
        f"WWW-Authenticate must start with 'Bearer', got: {www!r}"
    )
    assert "realm=" in www


def test_401_www_authenticate_present_on_invalid_token_too():
    r = _client().post(
        "/api/chat",
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").startswith("Bearer")


def test_401_www_authenticate_present_on_wrong_scheme():
    r = _client().post(
        "/api/chat",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").startswith("Bearer")


# ---------------------------------------------------------------------------
# Body shape + explicit hint text
# ---------------------------------------------------------------------------

def test_missing_header_body_reports_missing_reason():
    r = _client().post("/api/chat")
    body = r.json()
    assert body["error"] == "Unauthorized"
    assert body["reason"] == "missing_credentials"
    # The body must name the header the client needs to send.
    assert "Authorization" in body["hint"]
    assert "Bearer" in body["hint"]


def test_wrong_token_body_reports_invalid_reason():
    r = _client().post(
        "/api/chat",
        headers={"Authorization": "Bearer wrong"},
    )
    body = r.json()
    assert body["error"] == "Unauthorized"
    assert body["reason"] == "invalid_credentials"
    assert "Bearer" in body["hint"]


def test_wrong_scheme_body_reports_invalid_reason():
    r = _client().post(
        "/api/chat",
        headers={"Authorization": "Token abc"},
    )
    body = r.json()
    assert body["reason"] == "invalid_credentials"
    # Detail string must mention that the scheme is wrong.
    assert "scheme" in body["detail"].lower() or "bearer" in body["detail"].lower()


# ---------------------------------------------------------------------------
# No-leak invariant — the real api_key never appears in any field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Token something"},
    {"Authorization": API_KEY},  # missing "Bearer " prefix
])
def test_401_never_leaks_api_key(headers):
    r = _client().post("/api/chat", headers=headers)
    assert r.status_code == 401
    # The key must not appear anywhere in the response.
    assert API_KEY not in r.text
    for value in r.headers.values():
        assert API_KEY not in value


# ---------------------------------------------------------------------------
# WebSocket /ws branch — must share the same ergonomic shape
# ---------------------------------------------------------------------------

def test_ws_missing_token_has_same_hint_shape():
    r = _client().get("/ws")
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "Unauthorized"
    assert body["reason"] == "missing_credentials"
    assert body["hint"]
    assert r.headers.get("www-authenticate", "").startswith("Bearer")


def test_ws_wrong_token_has_invalid_reason():
    r = _client().get("/ws?token=wrong")
    assert r.status_code == 401
    body = r.json()
    assert body["reason"] == "invalid_credentials"
    assert API_KEY not in r.text


# ---------------------------------------------------------------------------
# Positive path: correct header still succeeds unchanged
# ---------------------------------------------------------------------------

def test_valid_bearer_still_succeeds():
    r = _client().post(
        "/api/chat",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert r.status_code == 200
    assert r.json() == {"response": "hello"}

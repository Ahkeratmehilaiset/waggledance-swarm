"""Unit tests for BearerAuthMiddleware — token validation, path rules."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient
from fastapi import FastAPI

from waggledance.adapters.http.middleware.auth import BearerAuthMiddleware, PUBLIC_PATHS


def _make_app(api_key: str = "test-key-123") -> FastAPI:
    """Create a minimal FastAPI app with auth middleware."""
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        return {"status": "ready"}

    @app.get("/docs")
    async def docs():
        return {"docs": True}

    @app.post("/api/chat")
    async def chat():
        return {"response": "hello"}

    @app.post("/api/memory/ingest")
    async def ingest():
        return {"status": "stored"}

    @app.get("/static/file.js")
    async def static_file():
        return {"file": "content"}

    @app.get("/ws")
    async def ws():
        return {"ws": True}

    app.add_middleware(BearerAuthMiddleware, api_key=api_key)
    return app


API_KEY = "test-key-123"


class TestPublicPaths:
    """Public paths should not require auth."""

    @pytest.mark.parametrize("path", ["/health", "/ready", "/docs"])
    def test_public_path_no_auth(self, path):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get(path)
        assert r.status_code == 200

    @pytest.mark.parametrize("path", ["/health", "/ready", "/docs"])
    def test_public_path_with_auth_also_ok(self, path):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get(path, headers={"Authorization": f"Bearer {API_KEY}"})
        assert r.status_code == 200

    def test_public_paths_constant(self):
        assert "/health" in PUBLIC_PATHS
        assert "/ready" in PUBLIC_PATHS
        assert "/docs" in PUBLIC_PATHS
        assert "/openapi.json" in PUBLIC_PATHS
        assert "/redoc" in PUBLIC_PATHS


class TestProtectedPaths:
    """API paths require valid Bearer token."""

    def test_api_chat_requires_auth(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.post("/api/chat")
        assert r.status_code == 401

    def test_api_chat_with_valid_token(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.post("/api/chat", headers={"Authorization": f"Bearer {API_KEY}"})
        assert r.status_code == 200

    def test_api_chat_with_wrong_token(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.post("/api/chat", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_api_memory_requires_auth(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.post("/api/memory/ingest")
        assert r.status_code == 401

    def test_no_bearer_prefix_rejected(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.post("/api/chat", headers={"Authorization": API_KEY})
        assert r.status_code == 401

    def test_empty_bearer_rejected(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.post("/api/chat", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

    def test_401_response_body(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.post("/api/chat")
        data = r.json()
        assert "error" in data
        assert data["error"] == "Unauthorized"


class TestNonApiPaths:
    """Non-API paths (static files, root) bypass auth."""

    def test_static_files_no_auth(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get("/static/file.js")
        assert r.status_code == 200


class TestWebSocket:
    """WebSocket auth via query param."""

    def test_ws_with_valid_token(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get(f"/ws?token={API_KEY}")
        assert r.status_code == 200

    def test_ws_with_wrong_token(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get("/ws?token=wrong")
        assert r.status_code == 401

    def test_ws_without_token(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        r = client.get("/ws")
        assert r.status_code == 401


class TestInitValidation:
    """Constructor validation."""

    def test_empty_api_key_raises(self):
        app = FastAPI()
        with pytest.raises(ValueError, match="api_key must be resolved"):
            BearerAuthMiddleware(app, api_key="")

    def test_none_api_key_raises(self):
        app = FastAPI()
        with pytest.raises((ValueError, TypeError)):
            BearerAuthMiddleware(app, api_key=None)

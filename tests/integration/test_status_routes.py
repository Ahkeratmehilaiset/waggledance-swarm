"""Comprehensive tests for /health and /ready endpoints.

Covers: liveness, readiness status, 503 responses, component details,
auth requirements, HTTP methods, response structure.
"""

import pytest
from starlette.testclient import TestClient

from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware

# ---------------------------------------------------------------------------
# Shared client
# ---------------------------------------------------------------------------

_client = None
_api_key = None
_app = None


def _get_client():
    global _client, _api_key, _app
    if _client is None:
        from waggledance.adapters.config.settings_loader import WaggleSettings
        from waggledance.bootstrap.container import Container

        settings = WaggleSettings.from_env()
        container = Container(settings=settings, stub=True)
        _app = container.build_app()
        _client = TestClient(_app, raise_server_exceptions=False)
        _api_key = settings.api_key
    return _client, _api_key


def _reset_rate_limit():
    if _app is None:
        return
    obj = getattr(_app, "middleware_stack", None)
    if obj is None:
        return
    for _ in range(30):
        if isinstance(obj, RateLimitMiddleware):
            obj._buckets.clear()
            return
        obj = getattr(obj, "app", None)
        if obj is None:
            break


def _headers():
    _, api_key = _get_client()
    return {"Authorization": f"Bearer {api_key}"}


# ===================================================================
# HEALTH — /health
# ===================================================================


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_returns_ok_status(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/health")
        assert r.json()["status"] == "ok"

    def test_health_no_auth_required(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/health")  # no Authorization header
        assert r.status_code == 200

    def test_health_with_auth_also_works(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/health", headers=_headers())
        assert r.status_code == 200

    def test_health_response_is_json(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/health")
        assert r.headers["content-type"].startswith("application/json")

    def test_health_response_minimal(self):
        """Health response should be small and fast."""
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/health")
        data = r.json()
        assert "status" in data


# ===================================================================
# READY — /ready
# ===================================================================


class TestReadyEndpoint:
    """Tests for GET /ready."""

    def test_ready_returns_valid_status_code(self):
        """Ready should return 200 (ready) or 503 (not ready)."""
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        assert r.status_code in (200, 503)

    def test_ready_no_auth_required(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")  # no Authorization header
        assert r.status_code in (200, 503)

    def test_ready_response_has_ready_field(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        data = r.json()
        assert "ready" in data
        assert isinstance(data["ready"], bool)

    def test_ready_response_has_components(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        data = r.json()
        assert "components" in data
        assert isinstance(data["components"], list)

    def test_ready_components_have_structure(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        data = r.json()
        for comp in data["components"]:
            assert "name" in comp
            assert "ready" in comp
            assert "message" in comp

    def test_ready_has_uptime_seconds(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        data = r.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    def test_ready_status_code_matches_ready_field(self):
        """HTTP 200 should have ready=True, 503 should have ready=False."""
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        data = r.json()
        if r.status_code == 200:
            assert data["ready"] is True
        else:
            assert data["ready"] is False

    def test_ready_with_auth_header(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready", headers=_headers())
        assert r.status_code in (200, 503)

    def test_ready_response_is_json(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        assert r.headers["content-type"].startswith("application/json")

    def test_ready_components_check_expected_services(self):
        """In stub mode, at least one component should be present."""
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.get("/ready")
        data = r.json()
        component_names = [c["name"] for c in data["components"]]
        assert len(component_names) >= 1


# ===================================================================
# AUTH — authentication edge cases
# ===================================================================


class TestAuthEdgeCases:
    """Auth boundary tests across all protected endpoints."""

    @pytest.mark.parametrize(
        "endpoint,method",
        [
            ("/api/chat", "post"),
            ("/api/memory/ingest", "post"),
            ("/api/memory/search", "post"),
        ],
    )
    def test_protected_endpoints_reject_no_auth(self, endpoint, method):
        _reset_rate_limit()
        client, _ = _get_client()
        fn = getattr(client, method)
        r = fn(endpoint, json={"query": "test", "content": "test"})
        assert r.status_code == 401

    @pytest.mark.parametrize(
        "endpoint,method",
        [
            ("/api/chat", "post"),
            ("/api/memory/ingest", "post"),
            ("/api/memory/search", "post"),
        ],
    )
    def test_protected_endpoints_reject_wrong_token(self, endpoint, method):
        _reset_rate_limit()
        client, _ = _get_client()
        fn = getattr(client, method)
        r = fn(
            endpoint,
            json={"query": "test", "content": "test"},
            headers={"Authorization": "Bearer INVALID_TOKEN_123"},
        )
        assert r.status_code == 401

    @pytest.mark.parametrize(
        "endpoint,method",
        [
            ("/health", "get"),
            ("/ready", "get"),
        ],
    )
    def test_public_endpoints_accept_no_auth(self, endpoint, method):
        _reset_rate_limit()
        client, _ = _get_client()
        fn = getattr(client, method)
        r = fn(endpoint)
        assert r.status_code in (200, 503)

    def test_bearer_prefix_required(self):
        """Token without 'Bearer ' prefix should be rejected."""
        _reset_rate_limit()
        client, api_key = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "test"},
            headers={"Authorization": api_key},  # no "Bearer " prefix
        )
        assert r.status_code == 401

    def test_empty_bearer_token(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "test"},
            headers={"Authorization": "Bearer "},
        )
        assert r.status_code == 401


# ===================================================================
# CHAT — additional chat edge cases
# ===================================================================


class TestChatEdgeCases:
    """Additional chat endpoint coverage not in e2e_chat_200."""

    def test_chat_all_optional_params(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={
                "query": "Hello",
                "language": "en",
                "profile": "HOME",
                "user_id": "test-user-1",
                "session_id": "test-session-1",
                "context_turns": 3,
            },
            headers=_headers(),
        )
        assert r.status_code == 200

    def test_chat_response_structure(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Hello"},
            headers=_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert "response" in data
        assert "source" in data
        assert "confidence" in data
        assert "latency_ms" in data
        assert "cached" in data
        assert "language" in data

    def test_chat_profile_cottage(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Test", "profile": "COTTAGE"},
            headers=_headers(),
        )
        assert r.status_code == 200

    def test_chat_profile_home(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Test", "profile": "HOME"},
            headers=_headers(),
        )
        assert r.status_code == 200

    def test_chat_context_turns_zero(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Test", "context_turns": 0},
            headers=_headers(),
        )
        assert r.status_code == 200

    def test_chat_language_auto(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Mikä on lämpötila?", "language": "auto"},
            headers=_headers(),
        )
        assert r.status_code == 200

    def test_chat_confidence_is_numeric(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Hello"},
            headers=_headers(),
        )
        data = r.json()
        assert isinstance(data["confidence"], (int, float))
        assert 0 <= data["confidence"] <= 1.0

    def test_chat_latency_is_positive(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Hello"},
            headers=_headers(),
        )
        data = r.json()
        assert isinstance(data["latency_ms"], (int, float))
        assert data["latency_ms"] >= 0

    def test_chat_cached_is_bool(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "Hello"},
            headers=_headers(),
        )
        data = r.json()
        assert isinstance(data["cached"], bool)

    def test_chat_rejects_oversized_query(self):
        """Queries exceeding MAX_QUERY_LENGTH must be rejected with 422."""
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "A" * 10_001},
            headers=_headers(),
        )
        assert r.status_code == 422, f"Expected 422, got {r.status_code}"

    def test_chat_accepts_max_length_query(self):
        """A query at exactly MAX_QUERY_LENGTH should be accepted."""
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/chat",
            json={"query": "A" * 10_000},
            headers=_headers(),
        )
        assert r.status_code == 200

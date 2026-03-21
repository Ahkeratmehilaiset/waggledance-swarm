"""User scenario tests — UI-driven integration coverage (stub mode).

Simulates real user paths through the API using TestClient:
chat routing, caching, memory round-trip, language detection,
authentication, and error handling.

All tests run in stub mode (no Ollama, no ChromaDB).
"""

import unittest

from starlette.testclient import TestClient

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container


class _BaseScenario(unittest.TestCase):
    """Shared stub-mode TestClient — created once for the entire module."""

    _container = None
    _client = None
    _api_key = None

    @classmethod
    def setUpClass(cls):
        if _BaseScenario._container is None:
            settings = WaggleSettings.from_env()
            _BaseScenario._container = Container(settings=settings, stub=True)
            _BaseScenario._app = _BaseScenario._container.build_app()
            _BaseScenario._client = TestClient(
                _BaseScenario._app, raise_server_exceptions=False
            )
            _BaseScenario._api_key = settings.api_key
        cls.container = _BaseScenario._container
        cls.client = _BaseScenario._client
        cls.api_key = _BaseScenario._api_key

    def _auth(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def _chat(self, query, **kwargs):
        payload = {"query": query, **kwargs}
        return self.client.post("/api/chat", json=payload, headers=self._auth())


# ── 1. Chat Routing ──────────────────────────────────────────────


class TestChatRouting(_BaseScenario):
    """Routing paths through the chat endpoint."""

    def test_time_query_routes_to_llm(self):
        """Time keyword → llm route, stub returns time-aware response."""
        resp = self._chat("Paljonko kello on?")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["source"], "llm")
        # Stub LLM detects time keywords and returns canned time response
        self.assertIn("time", data["response"].lower())

    def test_system_query_routes_to_llm(self):
        """System keyword → llm route."""
        resp = self._chat("What is the system status?")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["source"], "llm")
        self.assertGreater(data["confidence"], 0.0)

    def test_default_query_routes_to_llm(self):
        """Generic query → llm default route, confidence >= 0.5."""
        resp = self._chat("Kerro mehiläisten hoidosta")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["source"], "llm")
        self.assertGreaterEqual(data["confidence"], 0.5)

    def test_response_has_all_fields(self):
        """Chat response contains all expected fields."""
        resp = self._chat("Hello")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for field in ("response", "source", "confidence", "latency_ms", "cached", "language"):
            self.assertIn(field, data, f"Missing field: {field}")


# ── 2. Cache Scenarios ───────────────────────────────────────────


class TestCacheScenarios(_BaseScenario):
    """HotCache behaviour — auto-caching via API path.

    Cache stores require confidence >= 0.8 AND query_frequency >= 2.
    Time/system queries get confidence=0.8 from routing_policy, so after
    2 queries the 3rd hit is served from cache automatically.
    """

    def test_repeated_time_query_auto_cached(self):
        """Time query 3x → 3rd call is a cache hit (confidence=0.8, freq≥2)."""
        q = "Paljonko kello on nyt?"
        r1 = self._chat(q)
        self.assertFalse(r1.json()["cached"])
        r2 = self._chat(q)
        self.assertFalse(r2.json()["cached"])
        # After 2 calls: freq=2, confidence=0.8 → cache stored on 2nd call
        r3 = self._chat(q)
        self.assertTrue(r3.json()["cached"])
        self.assertEqual(r3.json()["source"], "hotcache")

    def test_different_queries_no_cache(self):
        """Two distinct queries → neither is cached."""
        r1 = self._chat("Unique query alpha 12345")
        r2 = self._chat("Unique query beta 67890")
        self.assertFalse(r1.json()["cached"])
        self.assertFalse(r2.json()["cached"])

    def test_cache_key_is_case_insensitive(self):
        """Cache key is query.strip().lower() — case variations share entry."""
        # Time query to get confidence=0.8 for auto-caching
        q_lower = "what is the time right now?"
        q_mixed = "What Is The Time Right Now?"
        self._chat(q_lower)   # freq=1
        self._chat(q_lower)   # freq=2 → cached
        resp = self._chat(q_mixed)  # lowercase key matches → cache hit
        self.assertTrue(resp.json()["cached"])
        self.assertEqual(resp.json()["source"], "hotcache")


# ── 3. Memory Round-trip ─────────────────────────────────────────


class TestMemoryRoundtrip(_BaseScenario):
    """Ingest → search round-trip via /api/memory endpoints."""

    def test_ingest_returns_stored(self):
        """POST /api/memory/ingest returns id and status=stored."""
        resp = self.client.post(
            "/api/memory/ingest",
            json={"content": "Varroa-kynnys on 3 prosenttia"},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "stored")
        self.assertIn("id", data)

    def test_search_finds_ingested(self):
        """Ingest + search with matching term finds the record."""
        self.client.post(
            "/api/memory/ingest",
            json={"content": "Hunajasato oli 25 kiloa per pesä"},
            headers=self._auth(),
        )
        resp = self.client.post(
            "/api/memory/search",
            json={"query": "hunajasato kiloa"},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertTrue(len(results) > 0)
        contents = [r["content"] for r in results]
        self.assertTrue(
            any("hunajasato" in c.lower() for c in contents),
            f"Expected 'hunajasato' in results, got: {contents}",
        )

    def test_search_nonexistent_returns_empty(self):
        """Search for nonsense term returns empty results."""
        resp = self.client.post(
            "/api/memory/search",
            json={"query": "xyznonexistent999"},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"], [])


# ── 4. Language Detection ────────────────────────────────────────


class TestLanguageDetection(_BaseScenario):
    """Language auto-detection based on FI_CHARS and explicit hints."""

    def test_finnish_detected(self):
        """Query with ä/ö → language=fi."""
        resp = self._chat("Miten hoidetaan varroa-häätö?")
        self.assertEqual(resp.json()["language"], "fi")

    def test_english_detected(self):
        """ASCII-only query → language=en."""
        resp = self._chat("How do I treat varroa mites?")
        self.assertEqual(resp.json()["language"], "en")

    def test_explicit_language_hint(self):
        """Explicit language hint overrides auto-detection."""
        resp = self._chat("hello", language="fi")
        self.assertEqual(resp.json()["language"], "fi")


# ── 5. Auth Scenarios ────────────────────────────────────────────


class TestAuthScenarios(_BaseScenario):
    """Authentication enforcement on protected and public paths."""

    def test_chat_without_token_returns_401(self):
        """POST /api/chat without Authorization → 401."""
        resp = self.client.post("/api/chat", json={"query": "test"})
        self.assertEqual(resp.status_code, 401)

    def test_chat_with_wrong_token_returns_401(self):
        """POST /api/chat with invalid token → 401."""
        resp = self.client.post(
            "/api/chat",
            json={"query": "test"},
            headers={"Authorization": "Bearer wrong-token-12345"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_health_works_without_token(self):
        """GET /health is public — no auth required."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_memory_without_token_returns_401(self):
        """POST /api/memory/ingest without auth → 401."""
        resp = self.client.post(
            "/api/memory/ingest",
            json={"content": "secret data"},
        )
        self.assertEqual(resp.status_code, 401)


# ── 6. Error Handling ────────────────────────────────────────────


class TestErrorHandling(_BaseScenario):
    """Graceful error handling for malformed or incomplete requests."""

    def test_empty_query_handled(self):
        """Empty query string should not cause 500."""
        resp = self._chat("")
        self.assertIn(resp.status_code, (200, 422))

    def test_missing_query_field(self):
        """POST /api/chat with no query field → 422."""
        resp = self.client.post(
            "/api/chat",
            json={},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 422)

    def test_malformed_json(self):
        """POST /api/chat with invalid JSON body → 422."""
        resp = self.client.post(
            "/api/chat",
            content="not json at all",
            headers={**self._auth(), "Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_memory_ingest_missing_content(self):
        """POST /api/memory/ingest with no content field → 422."""
        resp = self.client.post(
            "/api/memory/ingest",
            json={},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()

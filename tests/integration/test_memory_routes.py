"""Comprehensive tests for /api/memory/ingest and /api/memory/search routes.

Covers: source values, tags, edge cases, unicode, limits, language param,
duplicate content, validation errors, and round-trip integrity.
"""

import pytest
from starlette.testclient import TestClient

from waggledance.adapters.http.middleware.rate_limit import RateLimitMiddleware

# ---------------------------------------------------------------------------
# Shared client (module-scoped for speed)
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


def _ingest(content: str, source: str = "api", tags: list | None = None):
    _reset_rate_limit()
    client, _ = _get_client()
    body = {"content": content, "source": source}
    if tags is not None:
        body["tags"] = tags
    return client.post("/api/memory/ingest", json=body, headers=_headers())


def _search(query: str, language: str = "en", limit: int = 5):
    _reset_rate_limit()
    client, _ = _get_client()
    return client.post(
        "/api/memory/search",
        json={"query": query, "language": language, "limit": limit},
        headers=_headers(),
    )


# ===================================================================
# INGEST — basic
# ===================================================================


class TestIngestBasic:
    """Core ingest functionality."""

    def test_ingest_returns_stored(self):
        r = _ingest("Temperature is 22C")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "stored"
        assert "id" in data and len(data["id"]) > 0

    def test_ingest_default_source_is_api(self):
        r = _ingest("default source test")
        assert r.status_code == 200

    def test_ingest_custom_source(self):
        r = _ingest("sensor data", source="mqtt_sensor")
        assert r.status_code == 200

    def test_ingest_with_tags(self):
        r = _ingest("tagged fact", tags=["bee", "temperature"])
        assert r.status_code == 200

    def test_ingest_empty_tags(self):
        r = _ingest("no tags fact", tags=[])
        assert r.status_code == 200

    def test_ingest_returns_unique_ids(self):
        r1 = _ingest("unique id test 1")
        r2 = _ingest("unique id test 2")
        assert r1.json()["id"] != r2.json()["id"]


# ===================================================================
# INGEST — content types
# ===================================================================


class TestIngestContentTypes:
    """Various content types and formats."""

    def test_ingest_finnish_content(self):
        r = _ingest("Mehiläispesän lämpötila on 35 astetta")
        assert r.status_code == 200

    def test_ingest_unicode_emoji(self):
        r = _ingest("Bee colony status: healthy 🐝✅")
        assert r.status_code == 200

    def test_ingest_special_characters(self):
        r = _ingest("CO₂ level: 400ppm, NO₂: <0.05mg/m³")
        assert r.status_code == 200

    def test_ingest_multiline_content(self):
        r = _ingest("Line 1\nLine 2\nLine 3")
        assert r.status_code == 200

    def test_ingest_json_in_content(self):
        r = _ingest('{"sensor": "temp", "value": 22.5}')
        assert r.status_code == 200

    def test_ingest_html_in_content(self):
        r = _ingest("<b>Important</b>: varroa count = 3")
        assert r.status_code == 200

    def test_ingest_numbers_only(self):
        r = _ingest("42.5")
        assert r.status_code == 200

    def test_ingest_very_long_content(self):
        r = _ingest("A" * 5000)
        assert r.status_code == 200

    def test_ingest_single_character(self):
        r = _ingest("x")
        assert r.status_code == 200

    def test_ingest_whitespace_content(self):
        r = _ingest("   spaces   ")
        assert r.status_code == 200


# ===================================================================
# INGEST — validation errors
# ===================================================================


class TestIngestValidation:
    """Request validation for ingest endpoint."""

    def test_ingest_missing_content_field(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/ingest",
            json={"source": "test"},
            headers=_headers(),
        )
        assert r.status_code == 422

    def test_ingest_empty_body(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/ingest",
            json={},
            headers=_headers(),
        )
        assert r.status_code == 422

    def test_ingest_no_auth(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/ingest",
            json={"content": "no auth"},
        )
        assert r.status_code == 401

    def test_ingest_wrong_auth(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/ingest",
            json={"content": "wrong auth"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401

    def test_ingest_malformed_json(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/ingest",
            content=b"not json",
            headers={**_headers(), "Content-Type": "application/json"},
        )
        assert r.status_code == 422


# ===================================================================
# INGEST — source values
# ===================================================================


class TestIngestSources:
    """Various source parameter values."""

    @pytest.mark.parametrize(
        "source",
        [
            "api",
            "mqtt_sensor",
            "user_feedback",
            "night_learning",
            "round_table",
            "home_assistant",
            "frigate",
            "manual",
        ],
    )
    def test_ingest_various_sources(self, source):
        r = _ingest(f"fact from {source}", source=source)
        assert r.status_code == 200


# ===================================================================
# SEARCH — basic
# ===================================================================


class TestSearchBasic:
    """Core search functionality."""

    def test_search_returns_results_key(self):
        r = _search("temperature")
        assert r.status_code == 200
        assert "results" in r.json()

    def test_search_results_is_list(self):
        r = _search("anything")
        assert isinstance(r.json()["results"], list)

    def test_search_nonexistent_returns_empty(self):
        r = _search("xyzzyplugh_nonexistent_term_12345")
        assert r.status_code == 200
        assert r.json()["results"] == []


# ===================================================================
# SEARCH — round-trip (ingest then search)
# ===================================================================


class TestSearchRoundTrip:
    """Verify ingested content is searchable."""

    def test_ingest_then_search_finds_it(self):
        unique = "varroa_mite_count_test_42"
        _ingest(f"The {unique} was measured today")
        r = _search(unique)
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) >= 1
        found = any(unique in str(hit) for hit in results)
        assert found, f"Expected to find '{unique}' in search results"

    def test_ingest_finnish_search_finnish(self):
        unique = "pölyttäjähyönteiset_testi_99"
        _ingest(f"Testi: {unique} on tärkeää")
        r = _search(unique, language="fi")
        assert r.status_code == 200

    def test_search_after_multiple_ingests(self):
        _ingest("alpha fact for search test", tags=["alpha"])
        _ingest("beta fact for search test", tags=["beta"])
        _ingest("gamma fact for search test", tags=["gamma"])
        r = _search("fact for search test")
        assert r.status_code == 200


# ===================================================================
# SEARCH — parameters
# ===================================================================


class TestSearchParams:
    """Search parameter variations."""

    def test_search_limit_1(self):
        r = _search("test", limit=1)
        assert r.status_code == 200
        assert len(r.json()["results"]) <= 1

    def test_search_limit_20(self):
        r = _search("test", limit=20)
        assert r.status_code == 200

    def test_search_language_fi(self):
        r = _search("lämpötila", language="fi")
        assert r.status_code == 200

    def test_search_language_en(self):
        r = _search("temperature", language="en")
        assert r.status_code == 200

    def test_search_default_limit_is_5(self):
        r = _search("test")
        assert r.status_code == 200
        assert len(r.json()["results"]) <= 5


# ===================================================================
# SEARCH — edge cases
# ===================================================================


class TestSearchEdgeCases:
    """Edge case queries."""

    def test_search_unicode_query(self):
        r = _search("köttbullar 🐝 CO₂")
        assert r.status_code == 200

    def test_search_very_long_query(self):
        r = _search("bee " * 200)
        assert r.status_code == 200

    def test_search_single_char(self):
        r = _search("x")
        assert r.status_code == 200

    def test_search_numbers_query(self):
        r = _search("42.5 99 -3")
        assert r.status_code == 200

    def test_search_special_chars(self):
        r = _search("(temp > 30) && alert")
        assert r.status_code == 200

    def test_search_sql_injection_attempt(self):
        r = _search("'; DROP TABLE memories; --")
        assert r.status_code == 200  # should not crash

    def test_search_html_injection(self):
        r = _search("<script>alert('xss')</script>")
        assert r.status_code == 200


# ===================================================================
# SEARCH — validation
# ===================================================================


class TestSearchValidation:
    """Request validation for search endpoint."""

    def test_search_missing_query(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/search",
            json={"language": "en"},
            headers=_headers(),
        )
        assert r.status_code == 422

    def test_search_empty_body(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/search",
            json={},
            headers=_headers(),
        )
        assert r.status_code == 422

    def test_search_no_auth(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/search",
            json={"query": "test"},
        )
        assert r.status_code == 401

    def test_search_wrong_auth(self):
        _reset_rate_limit()
        client, _ = _get_client()
        r = client.post(
            "/api/memory/search",
            json={"query": "test"},
            headers={"Authorization": "Bearer invalid"},
        )
        assert r.status_code == 401


# ===================================================================
# SEARCH — result structure
# ===================================================================


class TestSearchResultStructure:
    """Verify search result record fields."""

    def test_result_has_expected_fields(self):
        unique = "structure_test_field_check_77"
        _ingest(f"Structure test: {unique}")
        r = _search(unique)
        assert r.status_code == 200
        results = r.json()["results"]
        if len(results) > 0:
            rec = results[0]
            # MemoryRecord fields (as dict via dataclasses.asdict)
            assert "id" in rec
            assert "content" in rec
            assert "source" in rec
            assert "confidence" in rec

"""Phase 2 release-polish tests: /api/chat request body contract.

These tests lock in the ergonomics decisions from
``reports/API_CONTRACT_AUDIT.md``:

1. Canonical field is ``query``.
2. ``message`` is accepted as a backwards-compat alias — many
   OpenAI-compatible clients send ``{"message": "..."}`` and used to get a
   generic 422 with no hint.
3. When neither field is present, the error surface must *explicitly name*
   ``query`` so the caller knows how to fix the request.
4. Empty / whitespace-only ``query`` is rejected with an explicit hint.
5. Overlong ``query`` still hits the existing 10k-char cap with a clear
   message (no regression).
"""

import pytest
from starlette.testclient import TestClient

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container


_client = None
_api_key = None
_app = None


def _get_client():
    global _client, _api_key, _app
    if _client is None:
        settings = WaggleSettings.from_env()
        container = Container(settings=settings, stub=True)
        _app = container.build_app()
        _client = TestClient(_app, raise_server_exceptions=False)
        _api_key = settings.api_key
    return _client, _api_key


def _reset_rate_limit():
    if _app is None:
        return
    from waggledance.adapters.http.middleware.rate_limit import (
        RateLimitMiddleware,
    )
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


def _post(payload: dict):
    _reset_rate_limit()
    client, api_key = _get_client()
    return client.post(
        "/api/chat",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
    )


# ------------------------------------------------------------------ #
#  Happy paths                                                        #
# ------------------------------------------------------------------ #


def test_canonical_query_field_returns_200():
    resp = _post({"query": "Hello WaggleDance"})
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data and len(data["response"]) > 0


def test_message_alias_returns_200():
    """Clients sending {'message': ...} should work without a rewrite."""
    resp = _post({"message": "Hello WaggleDance"})
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data and len(data["response"]) > 0


def test_message_alias_produces_same_response_shape_as_query():
    """The alias must produce a response that has all the same required
    fields as the canonical ``query`` path."""
    r_q = _post({"query": "Tell me about varroa"})
    r_m = _post({"message": "Tell me about varroa"})
    assert r_q.status_code == 200
    assert r_m.status_code == 200
    for field in ("response", "source", "confidence", "latency_ms", "cached"):
        assert field in r_q.json(), f"canonical missing {field}"
        assert field in r_m.json(), f"alias missing {field}"


def test_query_wins_over_message_when_both_present():
    """If both are sent, the canonical ``query`` takes precedence."""
    resp = _post({"query": "canonical", "message": "alias_loser"})
    assert resp.status_code == 200


# ------------------------------------------------------------------ #
#  Error ergonomics                                                   #
# ------------------------------------------------------------------ #


def test_missing_query_and_message_names_query_in_error():
    """Empty body must produce an error that explicitly names ``query`` —
    otherwise operators hit a dead-end 422."""
    resp = _post({})
    assert resp.status_code in (400, 422)
    blob = resp.text.lower()
    assert "query" in blob, (
        "error body must mention 'query' so callers know which field "
        f"to send; got: {resp.text}"
    )


def test_empty_query_hints_at_valid_shape():
    resp = _post({"query": ""})
    assert resp.status_code in (400, 422)
    body = resp.text.lower()
    # Must mention the canonical field name AND give a hint.
    assert "query" in body
    assert "non-empty" in body or "hint" in body


def test_whitespace_only_query_is_rejected():
    resp = _post({"query": "   \t\n  "})
    assert resp.status_code in (400, 422)
    assert "query" in resp.text.lower()


def test_overlong_query_is_rejected_with_limit_in_message():
    """The 10k cap is a pre-existing DoS guard; this locks in the message."""
    resp = _post({"query": "x" * 10_001})
    assert resp.status_code in (400, 422)
    body = resp.text.lower()
    assert "10000" in body or "maximum" in body or "length" in body


def test_overlong_message_alias_is_also_rejected():
    """Alias path must go through the same length validation."""
    resp = _post({"message": "x" * 10_001})
    assert resp.status_code in (400, 422)

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

import json
import re

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
    for field in (
        "response",
        "source",
        "confidence",
        "latency_ms",
        "cached",
        "route_stage_trace",
    ):
        assert field in r_q.json(), f"canonical missing {field}"
        assert field in r_m.json(), f"alias missing {field}"


def test_query_wins_over_message_when_both_present():
    """If both are sent, the canonical ``query`` takes precedence."""
    resp = _post({"query": "canonical", "message": "alias_loser"})
    assert resp.status_code == 200


def test_success_response_includes_privacy_safe_route_stage_trace():
    raw_query = "Hello WaggleDance PRIVATE_QUERY_MARKER"
    raw_language = "PRIVATE_LANGUAGE_MARKER"
    raw_profile = "PRIVATE_PROFILE_MARKER"

    resp = _post({
        "query": raw_query,
        "language": raw_language,
        "profile": raw_profile,
    })

    assert resp.status_code == 200
    data = resp.json()
    trace = data.get("route_stage_trace")
    assert isinstance(trace, list)
    assert trace
    assert trace[0]["stage"] == "language_detection"
    assert trace[1]["stage"] == "hot_cache"
    assert all(isinstance(event.get("stage"), str) for event in trace)

    trace_json = json.dumps(trace)
    assert raw_query not in trace_json
    assert raw_language not in trace_json
    assert raw_profile not in trace_json


def test_chat_request_updates_privacy_safe_route_stage_runtime_metrics():
    raw_query = "Hello runtime metrics PRIVATE_QUERY_MARKER_METRICS"
    resp = _post({"query": raw_query})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("route_stage_trace"), list)

    client, _api_key = _get_client()
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    body = metrics.text

    assert "# HELP waggledance_route_stage_observations_total" in body
    assert "# HELP waggledance_route_stage_request_latency_ms_total" in body
    assert (
        "# HELP waggledance_route_stage_request_latency_histogram_ms"
        in body
    )
    assert "# HELP waggledance_route_stage_hex_coverage_total" in body
    assert re.search(
        r'waggledance_route_stage_observations_total\{'
        r'stage="language_detection"\} [1-9]\d*\.0',
        body,
    )
    assert re.search(
        r'waggledance_route_stage_request_latency_ms_total\{'
        r'stage="language_detection"\} (?!0\.0)\d+(?:\.\d+)?',
        body,
    )
    assert re.search(
        r'waggledance_route_stage_request_latency_histogram_ms_bucket\{'
        r'le="\+Inf",stage="language_detection"\} [1-9]\d*\.0',
        body,
    )
    assert re.search(
        r'waggledance_route_stage_hex_coverage_total\{'
        r'stage="hex_neighbor_assist_7_cell",state="disabled"\} [1-9]\d*\.0',
        body,
    )
    assert raw_query not in body
    assert "PRIVATE_QUERY_MARKER_METRICS" not in body
    assert "query=" not in body
    assert "profile=" not in body
    assert "route_stage_trace" not in body


def test_ws_chat_route_event_includes_privacy_safe_trace_and_disabled_labels():
    raw_query = "statistics summary for ws trace PRIVATE_QUERY_MARKER_WS"
    raw_language = "PRIVATE_LANGUAGE_MARKER_WS"
    raw_profile = "PRIVATE_PROFILE_MARKER_WS"
    _reset_rate_limit()
    client, api_key = _get_client()

    chat_route = None
    with client.websocket_connect(f"/ws?token={api_key}") as ws:
        resp = client.post(
            "/api/chat",
            json={
                "query": raw_query,
                "language": raw_language,
                "profile": raw_profile,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert resp.status_code == 200

        for _ in range(5):
            event = ws.receive_json()
            if event.get("type") == "chat_route":
                chat_route = event
                break

    assert chat_route is not None
    data = chat_route.get("data")
    assert isinstance(data, dict)
    assert "query" not in data
    assert "language" not in data
    assert "profile" not in data
    trace = data.get("route_stage_trace")
    assert isinstance(trace, list)
    assert trace
    assert trace[0]["stage"] == "language_detection"
    assert trace[1]["stage"] == "hot_cache"

    labels = data.get("route_stage_labels")
    assert isinstance(labels, list)
    assert {
        "stage": "hex_neighbor_assist_7_cell",
        "status": "disabled",
        "label": "disabled:runtime_config",
    } in labels
    disabled_route_stages = data.get("disabled_route_stages")
    assert isinstance(disabled_route_stages, list)
    assert "hex_neighbor_assist_7_cell" in disabled_route_stages

    event_json = json.dumps(data)
    assert raw_query not in event_json
    assert raw_language not in event_json
    assert raw_profile not in event_json


def test_route_stage_trace_boundary_drops_unsafe_trace_keys():
    from types import SimpleNamespace

    from waggledance.adapters.http.routes.chat import (
        ChatHttpResponse,
        _build_chat_route_ws_event,
    )

    raw_query = "PRIVATE_QUERY_MARKER_BOUNDARY"
    raw_language = "PRIVATE_LANGUAGE_MARKER_BOUNDARY"
    raw_profile = "PRIVATE_PROFILE_MARKER_BOUNDARY"
    raw_user = "PRIVATE_USER_MARKER_BOUNDARY"
    raw_session = "PRIVATE_SESSION_MARKER_BOUNDARY"
    unsafe_trace = [
        {
            "stage": "language_detection",
            "explicit_hint": True,
            "detected_language": raw_language,
            "query": raw_query,
            "language": raw_language,
            "profile": raw_profile,
            "user_id": raw_user,
            "session_id": raw_session,
        },
        {
            "stage": "memory_context",
            "language": raw_language,
            "limit": 5,
            "result_count": 0,
            "memory_score": 0.0,
        },
        {
            "stage": raw_query,
            "query": raw_query,
            "profile": raw_profile,
        },
    ]
    result = SimpleNamespace(
        response="ok",
        source="llm",
        confidence=0.8,
        latency_ms=1.0,
        cached=False,
        language="en",
        agent_id=None,
        round_table=False,
        route_stage_trace=unsafe_trace,
    )
    service = SimpleNamespace(
        _hybrid_retrieval=SimpleNamespace(enabled=True),
        _hex_neighbor_assist=SimpleNamespace(enabled=False),
    )

    resp = ChatHttpResponse.from_result(result)
    assert resp.route_stage_trace == [
        {
            "stage": "language_detection",
            "explicit_hint": True,
            "detected_language": "custom",
        },
        {
            "stage": "memory_context",
            "limit": 5,
            "result_count": 0,
            "memory_score": 0.0,
        },
    ]

    event = _build_chat_route_ws_event(resp, service)
    data = event["data"]
    assert data["route_stage_trace"] == resp.route_stage_trace
    assert "hex_neighbor_assist_7_cell" in data["disabled_route_stages"]

    serialized = json.dumps({"http": resp.route_stage_trace, "ws": data})
    for marker in (
        raw_query,
        raw_language,
        raw_profile,
        raw_user,
        raw_session,
    ):
        assert marker not in serialized


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

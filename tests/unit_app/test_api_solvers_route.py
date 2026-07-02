# SPDX-License-Identifier: BUSL-1.1
"""Tests for the programmatic solver-execute route POST /api/solvers/{case_id}."""
from __future__ import annotations

import json
from unittest import mock

from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.http.routes.solvers import router
from waggledance.core.v3_13_0.chat_dispatch import MAX_PAYLOAD_BYTES


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _post(case_id: str, payload) -> tuple[int, dict]:
    body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
    resp = _client().post(f"/api/solvers/{case_id}", content=body)
    return resp.status_code, resp.json()


def test_runs_registered_solver_and_returns_marker_and_receipt() -> None:
    # AIR-01 with a malformed observation is a deterministic solver refusal
    # (marker preserved), which is a successful run -> 200.
    status, body = _post("AIR-01", {"bogus": True})
    assert status == 200
    assert body["case_id"].startswith("AIR-01")
    assert body["result_marker"] == "INVALID_OBSERVATION_REFUSED"
    assert body["source"] == "v3_13_0_solver_registry"
    assert "magma_receipt" in body


def test_case_id_is_case_insensitive() -> None:
    status, body = _post("air-01", {"bogus": True})
    assert status == 200
    assert body["result_marker"] == "INVALID_OBSERVATION_REFUSED"


def test_unknown_solver_is_404() -> None:
    status, body = _post("NOPE-99", {})
    assert status == 404
    assert body["result_marker"] == "V3_13_SOLVER_INPUT_REFUSED"
    assert body["refusal_reason"] == "unknown_solver"


def test_malformed_json_body_is_400() -> None:
    status, body = _post("AIR-01", "{not-json")
    assert status == 400
    assert body["refusal_reason"] == "payload_json_invalid"
    assert "magma_receipt" in body


def test_invalid_utf8_body_is_400() -> None:
    status, body = _post("AIR-01", b'{"bogus": "\xff"}')
    assert status == 400
    assert body["result_marker"] == "V3_13_SOLVER_INPUT_REFUSED"
    assert body["refusal_reason"] == "payload_json_invalid"
    assert "magma_receipt" in body


def test_non_object_body_is_400() -> None:
    status, body = _post("AIR-01", "[1, 2, 3]")
    assert status == 400
    assert body["refusal_reason"] == "payload_must_be_object"


def test_oversized_body_is_413() -> None:
    big = json.dumps({"x": "a" * (MAX_PAYLOAD_BYTES + 10)})
    status, body = _post("AIR-01", big)
    assert status == 413
    assert body["refusal_reason"] == "payload_too_large"
    assert "magma_receipt" in body


def test_solver_domain_refusal_is_200() -> None:
    # ENG-06 no-fires horizon: the solver ran and returned its own refusal.
    status, body = _post("ENG-06", {
        "burn_log": [{
            "day_utc": "2026-01-01T00:00:00Z", "fire_event_count": 0,
            "peak_chimney_temp_c": 20.0, "average_chimney_temp_c": 18.0,
        }],
        "horizon_start_utc": "2026-01-01T00:00:00Z",
        "horizon_end_utc": "2026-01-01T00:00:00Z",
    })
    assert status == 200
    assert body["result_marker"] == "NO_FIRES_IN_HORIZON_REFUSED"


def test_no_http_transport_reachable_from_body() -> None:
    # A URL in the body must not trigger any network fetch: the dispatch core
    # calls a pure solver entrypoint, never a transport.
    with mock.patch("httpx.Client") as client_cls, \
            mock.patch("httpx.AsyncClient") as aclient_cls:
        status, body = _post("AIR-01", {"url": "http://169.254.169.254/latest"})
        client_cls.assert_not_called()
        aclient_cls.assert_not_called()
    assert status == 200
    receipt = body["magma_receipt"]["summary"]
    assert receipt["network_access"] == "not_permitted"
    assert receipt["transport_modules_used"] == []


def test_every_registered_solver_is_reachable() -> None:
    from waggledance.core.v3_13_0.solver_registry import load_solver_registry

    for solver in load_solver_registry():
        status, body = _post(solver.case_id, {})
        # Empty body reaches the solver (which then validates/refuses); the
        # point is the ROUTE resolves every registered case_id (never 404).
        assert status != 404, f"{solver.case_id} not reachable"
        assert body["source"] == "v3_13_0_solver_registry"


def test_route_registered_in_api_factory_and_auth_gated() -> None:
    from waggledance.adapters.http.api import create_app

    class Settings:
        api_key = "test-key"

    class Container:
        _settings = Settings()

    app = create_app(Container())
    client = TestClient(app, raise_server_exceptions=False)

    # Under /api/* -> requires the bearer token.
    unauth = client.post("/api/solvers/AIR-01", content=json.dumps({"bogus": True}))
    assert unauth.status_code == 401

    ok = client.post(
        "/api/solvers/AIR-01",
        content=json.dumps({"bogus": True}),
        headers={"Authorization": "Bearer test-key"},
    )
    assert ok.status_code == 200
    assert ok.json()["result_marker"] == "INVALID_OBSERVATION_REFUSED"

# SPDX-License-Identifier: BUSL-1.1
"""Tests for the v3.13.0 deterministic solver execution HTTP route."""
from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.http.routes.solvers import (
    SOLVER_EXECUTION_REFUSED,
    SOLVER_REGISTRY_REFUSED,
    router,
)


ENG01_CASE_ID = "ENG-01__spot_electricity_monitor__home"
ENG06_CASE_ID = "ENG-06__cottage_fireplace_advisor__cottage"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _eng01_feed() -> dict:
    return {
        "fetched_at_utc": "2026-01-01T00:00:00Z",
        "horizon_start_utc": "2026-01-01T00:00:00Z",
        "horizon_hours": 3,
        "prices_eur_per_kwh": [
            {
                "hour_utc": "2026-01-01T00:00:00Z",
                "price_eur_per_kwh": 0.2,
            },
            {
                "hour_utc": "2026-01-01T01:00:00Z",
                "price_eur_per_kwh": 0.05,
            },
            {
                "hour_utc": "2026-01-01T02:00:00Z",
                "price_eur_per_kwh": 0.1,
            },
        ],
    }


def _day(offset: int) -> str:
    return f"2026-01-{offset + 1:02d}T00:00:00Z"


def test_lists_registry_solvers() -> None:
    response = _client().get("/api/solvers")

    assert response.status_code == 200
    data = response.json()
    assert data["solver_count"] == 8
    assert ENG01_CASE_ID in {
        solver["case_id"] for solver in data["solvers"]
    }


def test_returns_one_solver_manifest() -> None:
    response = _client().get(f"/api/solvers/{ENG01_CASE_ID}")

    assert response.status_code == 200
    data = response.json()
    assert data["case_id"] == ENG01_CASE_ID
    assert data["write_intent"] == "none"
    assert data["risk_class"] == "informational"


def test_executes_mapping_payload_solver_with_direct_body() -> None:
    response = _client().post(
        f"/api/solvers/{ENG01_CASE_ID}",
        json=_eng01_feed(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["case_id"] == ENG01_CASE_ID
    assert data["write_intent"] == "none"
    assert data["result_marker"] == "OK"
    assert data["result"]["top_3_cheapest_hours_utc"][0] == {
        "hour_utc": "2026-01-01T01:00:00Z",
        "price_eur_per_kwh": 0.05,
        "rank": 1,
    }


def test_executes_iterable_payload_solver_with_parameters() -> None:
    burn_log = [
        {
            "day_utc": _day(0),
            "fire_event_count": 1,
            "peak_chimney_temp_c": 180.0,
            "average_chimney_temp_c": 140.0,
        },
        {
            "day_utc": _day(1),
            "fire_event_count": 2,
            "peak_chimney_temp_c": 210.0,
            "average_chimney_temp_c": 160.0,
        },
    ]

    response = _client().post(
        f"/api/solvers/{ENG06_CASE_ID}",
        json={
            "input": burn_log,
            "parameters": {
                "horizon_start_utc": _day(0),
                "horizon_end_utc": _day(1),
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["case_id"] == ENG06_CASE_ID
    assert data["result_marker"] == "OK"
    assert data["result"]["fire_event_count_30d"] == 3
    assert data["result"]["days_with_fire"] == 2


def test_unknown_solver_case_id_fails_closed() -> None:
    response = _client().post(
        "/api/solvers/case:unknown_solver",
        json={"input": {}},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["result_marker"] == SOLVER_REGISTRY_REFUSED


def test_non_object_parameters_are_rejected_before_execution() -> None:
    response = _client().post(
        f"/api/solvers/{ENG01_CASE_ID}",
        json={"input": _eng01_feed(), "parameters": []},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "result_marker": SOLVER_EXECUTION_REFUSED,
        "reason": "parameters_must_be_object",
    }


def test_solver_execution_route_registered_in_api_factory() -> None:
    from waggledance.adapters.http.api import create_app

    class Settings:
        api_key = "test-key"

    class Container:
        _settings = Settings()

    app = create_app(Container())

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/solvers",
        headers={"Authorization": "Bearer test-key"},
    )

    assert response.status_code == 200
    assert response.json()["solver_count"] == 8

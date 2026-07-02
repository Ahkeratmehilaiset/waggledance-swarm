# SPDX-License-Identifier: BUSL-1.1
"""Tests for the ENG-06 read-only advisory snapshot route."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.http.routes.eng06_advisory import (
    ADVISORY_MAX_BYTES,
    NO_ADVISORY_YET,
    SNAPSHOT_REFUSED,
    get_snapshot_path,
    router,
)


def _client(snapshot_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_snapshot_path] = lambda: snapshot_path
    return TestClient(app, raise_server_exceptions=False)


def _payload() -> dict:
    return {
        "schema_version": "eng06_advisory_card.v1",
        "case_id": "ENG-06__cottage_fireplace_advisor__cottage",
        "result_marker": "OK",
        "status": "ok",
        "write_intent": "none",
        "metrics": {
            "fire_event_count_30d": 4,
            "days_with_fire": 3,
            "average_chimney_temp_c": 89.6,
            "peak_chimney_temp_c": 176.8,
        },
    }


def test_returns_latest_advisory_when_file_present(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(json.dumps(_payload()), encoding="utf-8")

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == _payload()


def test_returns_no_advisory_when_file_absent(tmp_path: Path) -> None:
    response = _client(tmp_path / "missing.json").get(
        "/api/eng06/advisory/latest"
    )

    assert response.status_code == 200
    assert response.json() == {
        "result_marker": NO_ADVISORY_YET,
        "reason": "missing",
    }


def test_returns_no_advisory_when_file_empty(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text("", encoding="utf-8")

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == NO_ADVISORY_YET
    assert response.json()["reason"] == "empty"


def test_refuses_oversized_file(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_bytes(b"{" + (b" " * ADVISORY_MAX_BYTES) + b"}")

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.json() == {
        "result_marker": SNAPSHOT_REFUSED,
        "reason": "size_exceeded",
    }


def test_refuses_malformed_json(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text("{not-json", encoding="utf-8")

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "parse_failed"


def test_refuses_non_object_root(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(json.dumps(["OK"]), encoding="utf-8")

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "not_object"


def test_refuses_missing_result_marker(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "missing_result_marker"


def test_refuses_nan_constant_instead_of_500(tmp_path: Path) -> None:
    # Lead build review #1470: Python json.loads accepts NaN/Infinity
    # constants; the strict JSONResponse encoder then raises -> a 500 on the
    # fail-closed surface. Must refuse instead.
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(
        '{"result_marker": "OK", "metrics": {"x": NaN}}', encoding="utf-8"
    )

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "non_finite_number"


def test_refuses_float_overflow_to_infinity_instead_of_500(tmp_path: Path) -> None:
    # 1e999 overflows to inf WITHOUT the NaN/Infinity constant path.
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(
        '{"result_marker": "OK", "value": 1e999}', encoding="utf-8"
    )

    response = _client(snapshot).get("/api/eng06/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "non_finite_number"


def test_route_registered_in_api_factory() -> None:
    from waggledance.adapters.http.api import create_app

    class Settings:
        api_key = "test-key"

    class Container:
        _settings = Settings()

    app = create_app(Container())

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/eng06/advisory/latest",
        headers={"Authorization": "Bearer test-key"},
    )

    assert response.status_code == 200
    assert response.json()["result_marker"] == NO_ADVISORY_YET

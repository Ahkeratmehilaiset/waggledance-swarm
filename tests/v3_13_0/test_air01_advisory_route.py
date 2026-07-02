# SPDX-License-Identifier: BUSL-1.1
"""Tests for the AIR-01 read-only advisory snapshot route."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.http.routes.air01_advisory import (
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
        "case_id": "AIR-01__indoor_air_quality_advisor__cottage",
        "result_marker": "OK",
        "risk_level": "ok",
        "write_intent": "none",
        "triggered_metrics": [],
    }


def test_returns_latest_advisory_when_file_present(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(json.dumps(_payload()), encoding="utf-8")

    response = _client(snapshot).get("/api/air01/advisory/latest")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == _payload()


def test_returns_no_advisory_when_file_absent(tmp_path: Path) -> None:
    response = _client(tmp_path / "missing.json").get(
        "/api/air01/advisory/latest"
    )

    assert response.status_code == 200
    assert response.json() == {
        "result_marker": NO_ADVISORY_YET,
        "reason": "missing",
    }


def test_returns_no_advisory_when_file_empty(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text("", encoding="utf-8")

    response = _client(snapshot).get("/api/air01/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == NO_ADVISORY_YET
    assert response.json()["reason"] == "empty"


def test_refuses_oversized_file(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_bytes(b"{" + (b" " * ADVISORY_MAX_BYTES) + b"}")

    response = _client(snapshot).get("/api/air01/advisory/latest")

    assert response.status_code == 200
    assert response.json() == {
        "result_marker": SNAPSHOT_REFUSED,
        "reason": "size_exceeded",
    }


def test_refuses_malformed_json(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text("{not-json", encoding="utf-8")

    response = _client(snapshot).get("/api/air01/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "parse_failed"


def test_refuses_non_object_root(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(json.dumps(["OK"]), encoding="utf-8")

    response = _client(snapshot).get("/api/air01/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "not_object"


def test_refuses_missing_result_marker(tmp_path: Path) -> None:
    snapshot = tmp_path / "latest_advisory.json"
    snapshot.write_text(json.dumps({"risk_level": "ok"}), encoding="utf-8")

    response = _client(snapshot).get("/api/air01/advisory/latest")

    assert response.status_code == 200
    assert response.json()["result_marker"] == SNAPSHOT_REFUSED
    assert response.json()["reason"] == "missing_result_marker"


def test_route_registered_in_api_factory() -> None:
    from waggledance.adapters.http.api import create_app

    class Settings:
        api_key = "test-key"

    class Container:
        _settings = Settings()

    app = create_app(Container())

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/air01/advisory/latest",
        headers={"Authorization": "Bearer test-key"},
    )

    assert response.status_code == 200
    assert response.json()["result_marker"] == NO_ADVISORY_YET

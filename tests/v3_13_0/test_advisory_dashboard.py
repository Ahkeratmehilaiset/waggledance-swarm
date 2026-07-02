# SPDX-License-Identifier: Apache-2.0
"""Tests for the read-only advisory status dashboard."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.http.routes.advisory_dashboard import (
    SNAPSHOT_PATHS,
    render_advisories_dashboard_html,
    router,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _write_snapshot(case_id: str, payload: dict) -> None:
    path = Path(SNAPSHOT_PATHS[case_id])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_serves_html_with_no_snapshots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    response = _client().get("/dashboard/advisories")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # All three verticals present, each in the no-advisory state.
    for case_id in ("ENG-01", "AIR-01", "ENG-06"):
        assert case_id in response.text
    assert response.text.count("No advisory yet") == 3


def test_renders_all_three_advisories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_snapshot("ENG-01", {
        "schema_version": "eng01_advisory_card.v1",
        "result_marker": "OK",
        "top_hours": [
            {"rank": 1, "hour_utc": "2026-01-16T02:00:00Z",
             "price_eur_per_kwh": 0.031},
        ],
    })
    _write_snapshot("AIR-01", {
        "result_marker": "AIR_QUALITY_WARNING",
        "risk_level": "warning",
        "device_id": "digheran-cottage-1",
        "triggered_metrics": [{"metric": "pm25_ug_m3"}],
    })
    _write_snapshot("ENG-06", {
        "schema_version": "eng06_advisory_card.v1",
        "result_marker": "OK",
        "metrics": {"fire_event_count_30d": 4, "days_with_fire": 3},
    })

    text = _client().get("/dashboard/advisories").text

    assert "2026-01-16T02:00:00Z" in text
    assert "0.031 EUR/kWh" in text
    assert "AIR_QUALITY_WARNING" in text
    assert "digheran-cottage-1" in text
    assert "pm25_ug_m3" in text
    assert "Fires (30d)" in text
    assert "No advisory yet" not in text


def test_refused_snapshot_renders_refused_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(SNAPSHOT_PATHS["ENG-01"]).parent.mkdir(parents=True, exist_ok=True)
    Path(SNAPSHOT_PATHS["ENG-01"]).write_text("{not-json", encoding="utf-8")

    text = _client().get("/dashboard/advisories").text

    assert "SNAPSHOT_REFUSED" in text
    assert "parse_failed" in text


def test_escapes_malicious_snapshot_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_snapshot("AIR-01", {
        "result_marker": "<script>alert(1)</script>OK",
        "risk_level": "<img src=x onerror=alert(1)>",
    })

    text = _client().get("/dashboard/advisories").text

    assert "<script>alert(1)</script>" not in text
    assert "<img src=x" not in text
    assert "&lt;script&gt;" in text


def test_rendering_is_total_on_garbage_nested_fields(tmp_path, monkeypatch):
    # Totality guard: malformed nested fields must degrade, never 500.
    monkeypatch.chdir(tmp_path)
    _write_snapshot("ENG-01", {"result_marker": "OK", "top_hours": "notalist"})
    _write_snapshot("AIR-01", {"result_marker": "OK", "triggered_metrics": 42,
                               "risk_level": ["x"]})
    _write_snapshot("ENG-06", {"result_marker": "OK", "metrics": "garbage"})

    response = _client().get("/dashboard/advisories")

    assert response.status_code == 200
    assert response.text.count("OK") >= 3


def test_emergency_marker_renders_as_critical_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_snapshot("AIR-01", {"result_marker": "AIR_QUALITY_EMERGENCY"})

    text = _client().get("/dashboard/advisories").text

    assert "AIR_QUALITY_EMERGENCY" in text
    assert "#d03b3b" in text  # critical accent on the card


def test_render_function_handles_missing_verticals_directly():
    html_text = render_advisories_dashboard_html({})
    for case_id in ("ENG-01", "AIR-01", "ENG-06"):
        assert case_id in html_text


def test_route_registered_in_api_factory_without_auth() -> None:
    from waggledance.adapters.http.api import create_app

    class Settings:
        api_key = "test-key"

    class Container:
        _settings = Settings()

    app = create_app(Container())

    # Non-/api path: served WITHOUT a bearer token, like /hologram.
    response = TestClient(app, raise_server_exceptions=False).get(
        "/dashboard/advisories"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

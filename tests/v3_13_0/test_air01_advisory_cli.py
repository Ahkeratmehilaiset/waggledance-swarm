# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
import json

from waggledance.adapters.cli import air01_advisory
from waggledance.core.v3_13_0.air01_sensor_http_transport import (
    Air01SensorHttpResponse,
)

CASE_ID = "AIR-01__indoor_air_quality_advisor__cottage"
SOURCE_URL = "https://aq.example.com/air"


def _digheran_payload() -> dict:
    # pm25 57.25 > 50.0 warning threshold; co 2.1 ppm is fine -> AIR_QUALITY_WARNING
    return {
        "device": {"id": "digheran-cottage-1", "model": "Digheran AQ"},
        "timestamp_utc": "2026-05-15T18:00:00Z",
        "readings": {
            "pm25": {"value": 57.25, "unit": "ug/m3"},
            "co": {"value": 2.1, "unit": "ppm"},
        },
    }


def _run(argv, transport=None):
    out, err = io.StringIO(), io.StringIO()
    code = air01_advisory.main(argv, stdout=out, stderr=err, transport=transport)
    return code, out.getvalue(), err.getvalue()


def test_input_mode_runs_the_solver(tmp_path):
    src = tmp_path / "sensor.json"
    src.write_text(json.dumps(_digheran_payload()), encoding="utf-8")

    code, out, err = _run([
        "--input", str(src),
        "--source-url", SOURCE_URL,
        "--fetched-at-utc", "2026-05-15T18:05:00Z",
    ])

    assert code == 0, err
    payload = json.loads(out)
    assert payload["case_id"] == CASE_ID
    assert payload["result_marker"] == "AIR_QUALITY_WARNING"
    assert payload["risk_level"] == "warning"
    assert payload["write_intent"] == "none"
    assert payload["source_url"] == SOURCE_URL
    assert payload["device_id"] == "digheran-cottage-1"
    assert any(m["metric"] == "pm25_ug_m3" for m in payload["triggered_metrics"])


def test_input_without_source_url_is_refused(tmp_path):
    src = tmp_path / "sensor.json"
    src.write_text(json.dumps(_digheran_payload()), encoding="utf-8")

    code, out, err = _run(["--input", str(src)])

    assert code == 2
    assert out == ""
    assert json.loads(err)["result_marker"] == "INVALID_INPUT_REFUSED"


def test_url_mode_runs_solver_via_injected_transport():
    body = json.dumps(_digheran_payload()).encode("utf-8")

    def fake_transport(url, headers, timeout):  # Air01SensorTransport shape
        return Air01SensorHttpResponse(
            body=body,
            content_type="application/json",
            status_code=200,
            source_url=url,
        )

    code, out, err = _run(
        ["--url", SOURCE_URL, "--fetched-at-utc", "2026-05-15T18:05:00Z"],
        transport=fake_transport,
    )

    assert code == 0, err
    payload = json.loads(out)
    assert payload["case_id"] == CASE_ID
    assert payload["result_marker"] == "AIR_QUALITY_WARNING"
    assert payload["source_url"] == SOURCE_URL


def test_url_mode_refuses_local_host_ssrf():
    # No transport injected: the transport's own URL validation must refuse a
    # local/private host before any fetch (fail-closed, not a live request).
    code, out, err = _run(["--url", "http://127.0.0.1:8080/air"])
    assert code == 2
    assert out == ""
    assert json.loads(err)["result_marker"] == "INVALID_INPUT_REFUSED"


def test_output_is_written_atomically_under_data_air01(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "sensor.json"
    src.write_text(json.dumps(_digheran_payload()), encoding="utf-8")

    code, out, err = _run([
        "--input", str(src),
        "--source-url", SOURCE_URL,
        "--output", "data/air01/latest_advisory.json",
    ])

    assert code == 0, err
    written = tmp_path / "data" / "air01" / "latest_advisory.json"
    assert written.exists()
    assert json.loads(written.read_text("utf-8"))["result_marker"] == (
        "AIR_QUALITY_WARNING"
    )


def test_output_outside_data_air01_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "sensor.json"
    src.write_text(json.dumps(_digheran_payload()), encoding="utf-8")

    code, out, err = _run([
        "--input", str(src),
        "--source-url", SOURCE_URL,
        "--output", "../escape.json",
    ])

    assert code == 2
    assert json.loads(err)["result_marker"] == "INVALID_INPUT_REFUSED"

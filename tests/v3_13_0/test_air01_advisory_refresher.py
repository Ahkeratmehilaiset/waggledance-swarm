# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

import pytest

from waggledance.adapters.feeds.air01_advisory_refresher import (
    LATEST_ADVISORY_SNAPSHOT_RELPATH,
    refresh_air01_latest_advisory,
)
from waggledance.adapters.http.routes.air01_advisory import (
    DEFAULT_SNAPSHOT_PATH,
    get_latest_advisory,
)
from waggledance.core.v3_13_0.air01_sensor_http_transport import (
    Air01SensorHttpResponse,
)

URL = "https://aq.example.com/air"


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


def _transport(url, headers, timeout):  # Air01SensorTransport shape
    return Air01SensorHttpResponse(
        body=json.dumps(_digheran_payload()).encode("utf-8"),
        content_type="application/json",
        status_code=200,
        source_url=url,
    )


def _refresh(*, snapshot_relpath=LATEST_ADVISORY_SNAPSHOT_RELPATH):
    return refresh_air01_latest_advisory(
        url=URL,
        transport=_transport,
        snapshot_relpath=snapshot_relpath,
        allowed_private_hosts=("aq.example.com",),
        fetched_at_utc="2026-05-15T18:05:00Z",
    )


def test_refresher_snapshot_path_matches_route_read_path():
    # Drift guard: the refresher must write exactly the path the route reads.
    assert LATEST_ADVISORY_SNAPSHOT_RELPATH == str(
        DEFAULT_SNAPSHOT_PATH
    ).replace("\\", "/")


def test_refresh_writes_solver_advisory_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    advisory = _refresh()

    # AIR-01 has no separate advisory-card layer: the solver payload is itself
    # the route snapshot contract and carries the result_marker.
    assert advisory["case_id"] == "AIR-01__indoor_air_quality_advisor__cottage"
    assert advisory["result_marker"] == "AIR_QUALITY_WARNING"
    assert advisory["source_url"] == URL

    written = tmp_path / "data" / "air01" / "latest_advisory.json"
    assert written.exists()
    assert json.loads(written.read_text("utf-8")) == advisory


def test_route_serves_the_refresh_written_live_advisory(tmp_path, monkeypatch):
    # The read route serves a refresh-written LIVE advisory, not a hand-written
    # file. Refresh, then read through the route.
    monkeypatch.chdir(tmp_path)
    written = _refresh()

    response = get_latest_advisory(snapshot_path=DEFAULT_SNAPSHOT_PATH)
    served = json.loads(response.body.decode("utf-8"))

    assert served["result_marker"] == "AIR_QUALITY_WARNING"
    assert served == written


def test_refresh_refuses_snapshot_path_outside_data_air01(tmp_path, monkeypatch):
    # snapshot_relpath must stay under data/air01 (reuses the established
    # output-path guard), no path traversal.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="data/air01"):
        _refresh(snapshot_relpath="../escape.json")
    assert not (tmp_path.parent / "escape.json").exists()


def test_route_reports_missing_before_first_refresh(tmp_path):
    # Before any refresh the route must not serve stale/garbage.
    response = get_latest_advisory(snapshot_path=tmp_path / "latest_advisory.json")
    served = json.loads(response.body.decode("utf-8"))
    assert served["result_marker"] != "OK"

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

import pytest

from waggledance.adapters.feeds.eng01_advisory_refresher import (
    LATEST_ADVISORY_SNAPSHOT_RELPATH,
    refresh_eng01_latest_advisory,
)
from waggledance.adapters.http.routes.eng01_advisory import (
    DEFAULT_SNAPSHOT_PATH,
    get_latest_advisory,
)
from waggledance.core.v3_13_0.eng01_price_feed_http_transport import (
    Eng01PriceFeedHttpResponse,
)

URL = "https://prices.example.test/day-ahead.json"
ROWS = [
    {"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0},
    {"hour_utc": "2026-01-16T01:00:00Z", "price": 75.0},
    {"hour_utc": "2026-01-16T02:00:00Z", "price": 125.0},
]


def _transport(url, *_):
    return Eng01PriceFeedHttpResponse(
        body=json.dumps(ROWS).encode("utf-8"),
        content_type="application/json; charset=utf-8",
        status_code=200,
        source_url=url,
    )


def _refresh(*, snapshot_relpath=LATEST_ADVISORY_SNAPSHOT_RELPATH):
    return refresh_eng01_latest_advisory(
        url=URL,
        transport=_transport,
        snapshot_relpath=snapshot_relpath,
        allowed_hosts=("prices.example.test",),
        fetched_at_utc="2026-01-15T20:00:00Z",
        horizon_start_utc="2026-01-16T00:00:00Z",
        horizon_hours=3,
    )


def test_refresher_snapshot_path_matches_route_read_path():
    # Drift guard: the refresher must write exactly the path the route reads.
    assert LATEST_ADVISORY_SNAPSHOT_RELPATH == str(
        DEFAULT_SNAPSHOT_PATH
    ).replace("\\", "/")


def test_refresh_writes_advisory_card_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    card = _refresh()

    # The route/CLI snapshot contract is the rendered card, NOT the raw solver
    # payload (regression: tools build review #1451 found raw payload written).
    assert card["schema_version"] == "eng01_advisory_card.v1"
    assert card["result_marker"] == "OK"
    assert card["case_id"]
    assert card["top_hours"][0]["hour_utc"] == "2026-01-16T01:00:00Z"

    written = tmp_path / "data" / "eng01" / "latest_advisory.json"
    assert written.exists()
    assert json.loads(written.read_text("utf-8")) == card


def test_route_serves_the_refresh_written_live_card(tmp_path, monkeypatch):
    # Lane #8 point: the read route serves a refresh-written LIVE advisory card,
    # not a hand-written file. Refresh, then read through the route.
    monkeypatch.chdir(tmp_path)
    written_card = _refresh()

    response = get_latest_advisory(snapshot_path=DEFAULT_SNAPSHOT_PATH)
    served = json.loads(response.body.decode("utf-8"))

    assert served["schema_version"] == "eng01_advisory_card.v1"
    assert served["result_marker"] == "OK"
    assert served == written_card
    assert served["top_hours"][0]["hour_utc"] == "2026-01-16T01:00:00Z"


def test_refresh_refuses_snapshot_path_outside_data_eng01(tmp_path, monkeypatch):
    # Regression (tools build review #1451): snapshot_relpath must stay under
    # data/eng01 (reuses the established output-path guard), no path traversal.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="data/eng01"):
        _refresh(snapshot_relpath="../escape.json")
    assert not (tmp_path.parent / "escape.json").exists()


def test_route_reports_missing_before_first_refresh(tmp_path):
    # Before any refresh the route must not serve stale/garbage.
    response = get_latest_advisory(snapshot_path=tmp_path / "latest_advisory.json")
    served = json.loads(response.body.decode("utf-8"))
    assert served["result_marker"] != "OK"

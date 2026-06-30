# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

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


def _refresh(snapshot_path):
    return refresh_eng01_latest_advisory(
        url=URL,
        transport=_transport,
        snapshot_path=snapshot_path,
        fetched_at_utc="2026-01-15T20:00:00Z",
        horizon_start_utc="2026-01-16T00:00:00Z",
        horizon_hours=3,
    )


def test_refresher_snapshot_path_matches_route_read_path():
    # Drift guard: the refresher must write exactly the path the route reads.
    assert LATEST_ADVISORY_SNAPSHOT_RELPATH == str(
        DEFAULT_SNAPSHOT_PATH
    ).replace("\\", "/")


def test_refresh_writes_live_snapshot(tmp_path):
    snapshot = tmp_path / "latest_advisory.json"

    result = _refresh(snapshot)

    assert result["result_marker"] == "OK"
    assert snapshot.exists()
    # cheapest of 100/75/125 EUR/MWh is the 01:00 hour
    assert result["top_3_cheapest_hours_utc"][0]["hour_utc"] == (
        "2026-01-16T01:00:00Z"
    )
    # no temp file left behind (atomic write)
    assert list(snapshot.parent.glob(".latest_advisory.json.*.tmp")) == []


def test_route_serves_the_refresh_written_live_advisory(tmp_path):
    # The whole point of lane #8: the read route serves a refresh-written LIVE
    # advisory, not a hand-written file. Refresh, then read through the route.
    snapshot = tmp_path / "latest_advisory.json"
    written = _refresh(snapshot)

    response = get_latest_advisory(snapshot_path=snapshot)
    served = json.loads(response.body.decode("utf-8"))

    assert served["result_marker"] == "OK"
    assert served == written
    assert served["top_3_cheapest_hours_utc"][0]["hour_utc"] == (
        "2026-01-16T01:00:00Z"
    )


def test_route_reports_missing_before_first_refresh(tmp_path):
    # Before any refresh the route must not serve stale/garbage: it reports the
    # snapshot is missing rather than a fake advisory.
    response = get_latest_advisory(snapshot_path=tmp_path / "latest_advisory.json")
    served = json.loads(response.body.decode("utf-8"))
    assert served["result_marker"] != "OK"

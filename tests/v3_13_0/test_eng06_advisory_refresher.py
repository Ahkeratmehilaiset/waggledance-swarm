# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.adapters.feeds.eng06_advisory_refresher import (
    LATEST_ADVISORY_SNAPSHOT_RELPATH,
    refresh_eng06_latest_advisory,
)
from waggledance.adapters.http.routes.eng06_advisory import (
    DEFAULT_SNAPSHOT_PATH,
    get_latest_advisory,
)

ROOT = Path(__file__).resolve().parents[2]
BURN_LOG_SAMPLE = ROOT / "examples" / "eng06" / "burn_log_sample.json"


def _refresh(*, snapshot_relpath=LATEST_ADVISORY_SNAPSHOT_RELPATH):
    return refresh_eng06_latest_advisory(
        burn_log_path=BURN_LOG_SAMPLE,
        snapshot_relpath=snapshot_relpath,
    )


def test_refresher_snapshot_path_matches_route_read_path():
    # Drift guard: the refresher must write exactly the path the route reads.
    assert LATEST_ADVISORY_SNAPSHOT_RELPATH == str(
        DEFAULT_SNAPSHOT_PATH
    ).replace("\\", "/")


def test_refresh_writes_advisory_card_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    card = _refresh()

    # The route snapshot contract is the rendered card, NOT the raw solver
    # payload (same contract discipline as ENG-01, cf. build review #1451).
    assert card["schema_version"] == "eng06_advisory_card.v1"
    assert card["result_marker"] == "OK"
    assert card["case_id"] == "ENG-06__cottage_fireplace_advisor__cottage"
    assert card["write_intent"] == "none"
    assert card["metrics"]["fire_event_count_30d"] == 4
    assert card["metrics"]["days_with_fire"] == 3

    written = tmp_path / "data" / "eng06" / "latest_advisory.json"
    assert written.exists()
    assert json.loads(written.read_text("utf-8")) == card


def test_route_serves_the_refresh_written_live_card(tmp_path, monkeypatch):
    # The read route serves a refresh-written LIVE advisory card, not a
    # hand-written file. Refresh, then read through the route.
    monkeypatch.chdir(tmp_path)
    written_card = _refresh()

    response = get_latest_advisory(snapshot_path=DEFAULT_SNAPSHOT_PATH)
    served = json.loads(response.body.decode("utf-8"))

    assert served["schema_version"] == "eng06_advisory_card.v1"
    assert served["result_marker"] == "OK"
    assert served == written_card


def test_refusal_solve_still_writes_a_refusal_card(tmp_path, monkeypatch):
    # Fail-closed passthrough: a refusing burn log must yield a refusal CARD
    # snapshot (marker preserved), never a crash or a stale OK snapshot.
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "no_fires.json"
    log.write_text(json.dumps({
        "burn_log": [
            {
                "day_utc": "2026-01-01T00:00:00Z",
                "fire_event_count": 0,
                "peak_chimney_temp_c": 20.0,
                "average_chimney_temp_c": 18.0,
            },
        ],
    }), encoding="utf-8")

    card = refresh_eng06_latest_advisory(burn_log_path=log)

    assert card["result_marker"] == "NO_FIRES_IN_HORIZON_REFUSED"
    assert card["status"] == "refused"
    written = tmp_path / "data" / "eng06" / "latest_advisory.json"
    assert json.loads(written.read_text("utf-8")) == card


def test_refresh_refuses_snapshot_path_outside_data_eng06(tmp_path, monkeypatch):
    # snapshot_relpath must stay under data/eng06 (same output-path guard
    # pattern as the ENG-01/AIR-01 verticals), no path traversal.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="data/eng06"):
        _refresh(snapshot_relpath="../escape.json")
    assert not (tmp_path.parent / "escape.json").exists()


def test_refresh_refuses_absolute_snapshot_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="data/eng06"):
        _refresh(snapshot_relpath=str(tmp_path / "abs.json"))


def test_refresh_refuses_non_object_burn_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "list.json"
    log.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        refresh_eng06_latest_advisory(burn_log_path=log)
    assert not (tmp_path / "data" / "eng06").exists()


def test_route_reports_missing_before_first_refresh(tmp_path):
    # Before any refresh the route must not serve stale/garbage.
    response = get_latest_advisory(snapshot_path=tmp_path / "latest_advisory.json")
    served = json.loads(response.body.decode("utf-8"))
    assert served["result_marker"] != "OK"

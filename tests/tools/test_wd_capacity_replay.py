# SPDX-License-Identifier: BUSL-1.1
"""Offline fixtures exercise capacity estimates without touching observer state."""
from datetime import datetime, timedelta, timezone
from itertools import permutations
import json

import pytest

from tools.wd_capacity_replay import main, replay


NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)


def sample(window="seven_day", *, used=40, remaining=6000, age=0, **overrides):
    return {
        "family": "claude",
        "pool": "fixture-pool",
        "window": window,
        "used_percent": used,
        "reset_at": (NOW + timedelta(minutes=remaining)).isoformat(),
        "observed_at": (NOW - timedelta(minutes=age)).isoformat(),
        "source": "fixture-1",
        "confidence": "verified",
        "binding_verified": True,
        **overrides,
    }


def reserve(window="five_hour", *, review=8, renewal=7, in_flight=5, **overrides):
    return {
        "family": "claude",
        "pool": "fixture-pool",
        "window": window,
        "review_percent": review,
        "renewal_percent": renewal,
        "in_flight_percent": in_flight,
        **overrides,
    }


def fixture(*records, reservations=()):
    return {"as_of": NOW.isoformat(), "records": list(records), "reservations": list(reservations)}


def pool(*records, reservations=()):
    report = replay(fixture(*records, reservations=reservations))
    assert report["execution_allowed"] is False
    return report["pools"][0]


def only_window(*records, reservations=()):
    (window,) = pool(*records, reservations=reservations)["windows"]
    return window


def test_shared_pool_is_counted_once_across_lanes():
    report = replay(fixture(
        sample(source="lane-1"),
        sample(source="lane-2"),
        sample(source="lane-3"),
    ))
    (item,) = report["pools"]
    (window,) = item["windows"]
    assert item["nominal_remaining_percent"] == 60
    assert item["verified_allocatable_percent"] == 45
    assert window["reserve_percent"] == 15
    assert window["sources"] == ["lane-1", "lane-2", "lane-3"]


def test_overlapping_windows_bind_to_tightest_cap_and_ramp():
    item = pool(
        sample(used=20),
        sample("five_hour", used=55, remaining=60),
        reservations=[reserve()],
    )
    assert item["nominal_remaining_percent"] == 45
    assert item["verified_allocatable_percent"] == 25
    assert item["ramp"] == "filler-only"
    assert [window["reserve_percent"] for window in item["windows"]] == [20, 15]


def test_weekly_reserve_keeps_floor_and_adds_in_flight():
    window = only_window(
        sample(), reservations=[reserve("seven_day", review=5, renewal=5, in_flight=10)]
    )
    assert window["review_and_renewal_percent"] == 15
    assert window["in_flight_percent"] == 10
    assert window["reserve_percent"] == 25
    assert window["verified_allocatable_percent"] == 35


def test_verified_short_window_still_needs_review_cost():
    window = only_window(sample("five_hour", remaining=120))
    assert window["reserve_percent"] is None
    assert window["verified_allocatable_percent"] is None
    assert window["issues"] == ["review_reservation_unknown"]


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [(76, "normal"), (75, "filler-only"), (21, "filler-only"), (20, "start-nothing")],
)
def test_reset_ramp_boundaries(remaining, expected):
    window = only_window(sample("five_hour", remaining=remaining), reservations=[reserve()])
    assert window["ramp"] == expected


def test_stale_window_preserves_nominal_only():
    window = only_window(sample(age=16))
    assert window["nominal_remaining_percent"] == 60
    assert window["verified_allocatable_percent"] is None
    assert window["ramp"] == "start-nothing"
    assert window["issues"] == ["stale_observation"]


def test_unannounced_reset_blocks_allocatable():
    window = only_window(sample(used=85, age=2), sample(used=1, remaining=9000))
    assert window["nominal_remaining_percent"] == 99
    assert window["verified_allocatable_percent"] is None
    assert window["issues"] == ["unannounced_reset"]


def test_announced_rollover_starts_a_new_meter_baseline():
    window = only_window(sample(used=95, age=2, remaining=-1), sample(used=3, remaining=10000))
    assert window["verified_allocatable_percent"] == 82
    assert window["issues"] == []


def test_false_full_headroom_without_rollover_is_unknown():
    window = only_window(sample(used=65, age=5), sample(used=0))
    assert window["nominal_remaining_percent"] == 100
    assert window["verified_allocatable_percent"] is None
    assert window["issues"] == ["meter_regression"]


def test_suspect_full_meter_is_unknown_and_verified_exhaustion_is_zero():
    suspect = only_window(sample(used=100, confidence="suspect"))
    exhausted = only_window(sample(used=100))
    assert suspect["verified_allocatable_percent"] is None
    assert suspect["issues"] == ["unverified_observation"]
    assert exhausted["verified_allocatable_percent"] == 0
    assert exhausted["ramp"] == "start-nothing"


def test_conflicting_simultaneous_meter_samples_are_order_independent():
    left = sample(used=20, source="lane-1")
    right = sample(used=100, source="lane-2")
    for rows in permutations([left, right]):
        window = only_window(*rows)
        assert window["nominal_remaining_percent"] == 0
        assert window["verified_allocatable_percent"] is None
        assert window["issues"] == ["conflicting_observations"]


def test_unknown_binding_does_not_become_allocatable_or_distinct_capacity():
    result = replay(fixture(
        sample(used=8, binding_verified=False),
        sample(pool="possibly-the-same-pool"),
        sample(family="codex", pool="codex-fixture"),
    ))
    first, second, codex = result["pools"]
    assert first["nominal_remaining_percent"] == 92
    assert first["verified_allocatable_percent"] is None
    assert second["verified_allocatable_percent"] is None
    assert codex["verified_allocatable_percent"] == 45


def test_fixture_with_no_pool_cannot_claim_a_binding():
    window = only_window(sample(pool=None))
    assert window["issues"] == ["unverified_pool_binding"]
    assert window["verified_allocatable_percent"] is None


def test_future_observation_is_not_replayed():
    result = replay(fixture(sample(), sample(age=-1, used=85, binding_verified=False)))
    assert result["future_records_ignored"] == 1
    assert result["pools"][0]["verified_allocatable_percent"] == 45


@pytest.mark.parametrize("used", [True, -1, 101, float("nan"), float("inf"), "42"])
def test_invalid_observation_is_rejected(used):
    with pytest.raises(ValueError, match="used_percent"):
        replay(fixture(sample(used=used)))


def test_cli_prints_deterministic_report_and_leaves_fixture_intact(tmp_path, capsys):
    path = tmp_path / "fixture.json"
    data = json.dumps(fixture(sample()))
    path.write_text(data, encoding="utf-8")
    assert main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["pools"][0]["verified_allocatable_percent"] == 45
    assert path.read_text(encoding="utf-8") == data

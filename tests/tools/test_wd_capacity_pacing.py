# SPDX-License-Identifier: BUSL-1.1
"""Read-only capacity pacer (PR-5): measured pace to reset, shadow recommendations only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import tools.wd_capacity_pacing as pacing
from tools.lane_profile_catalog import load_catalog
from tools.wd_capacity_pacing import main, pace, pace_windows, read_samples, recommend

ROOT = Path(__file__).resolve().parents[2]
CATALOG, DIGEST = load_catalog(ROOT / "configs" / "lane_profile_catalog.json")
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
CURRENT = {"fable-5": "claude-opus-5-5-medium", "claude-rco-1": "claude-sonnet-5-xhigh",
           "claude-rco-2": "claude-sonnet-5-xhigh", "codex-lead-1": "codex-gpt-5.6-sol-medium",
           "codex-tools-1": "codex-gpt-5.6-terra-medium"}


def approved_catalog() -> dict:
    catalog = json.loads(json.dumps(CATALOG))
    for number, profile in enumerate(catalog["capacity_policy"]["profiles"].values()):
        profile.update(approved=True, qualification_ref=f"qual-{number:03d}")
    return catalog


def sample(provider, window, used, *, hours_ago, reset_in_hours, duration=None, limit=None):
    return {"provider": provider, "limit_id": limit or provider, "window": window, "used_percent": float(used),
            "resets_at": (NOW + timedelta(hours=reset_in_hours)).timestamp(),
            "duration_minutes": duration or pacing.KNOWN_WINDOW_MINUTES.get(window, 10080),
            "observed_at": NOW - timedelta(hours=hours_ago)}


def series(provider, window, start, end, *, span_hours=10, reset_in_hours=100.0, duration=None):
    """Two samples: ``start`` percent ``span_hours`` ago, ``end`` percent now."""
    return [sample(provider, window, start, hours_ago=span_hours, reset_in_hours=reset_in_hours, duration=duration),
            sample(provider, window, end, hours_ago=0, reset_in_hours=reset_in_hours, duration=duration)]


def world(*, claude_week=(20, 22), claude_5h=(2, 3), codex=(5, 10), week_reset=100.0, codex_reset=100.0):
    """Defaults: Claude week 22 + 0.2 * 100 = 42 % and Codex 10 + 0.5 * 100 = 60 % at reset (both underused)."""
    return (series("claude", "seven_day", *claude_week, reset_in_hours=week_reset)
            + series("claude", "five_hour", *claude_5h, span_hours=1, reset_in_hours=2)
            + series("codex", "primary", *codex, reset_in_hours=codex_reset))


# ---------------------------------------------------------------- pacing math

def test_forecast_is_used_plus_measured_rate_until_reset():
    paced = pace_windows(series("codex", "primary", 10, 20, span_hours=10, reset_in_hours=50), now=NOW)
    window = paced["codex/codex/primary"]
    assert window["rate_percent_per_hour"] == 1.0
    assert window["forecast_percent_at_reset"] == 70.0 and window["verdict"] == "underused"


@pytest.mark.parametrize("end,reset,verdict", [
    (20, 50, "underused"),      # 20 + 1 * 50 = 70, exactly the underused bound
    (21, 50, "on_pace"),        # 21 + 1.1 * 50 = 76
    (40, 50, "overrun"),        # 40 + 3 * 50 = 190
    (20, 74.9, "on_pace"),      # just under 95
    (20, 75, "overrun"),        # 20 + 75 = 95, exactly the overrun bound
])
def test_verdict_bounds(end, reset, verdict):
    paced = pace_windows(series("codex", "primary", 10, end, span_hours=10, reset_in_hours=reset), now=NOW)
    assert paced["codex/codex/primary"]["verdict"] == verdict


def test_a_used_up_window_is_exhausted():
    paced = pace_windows(series("codex", "primary", 90, 100, reset_in_hours=5), now=NOW)
    assert paced["codex/codex/primary"]["verdict"] == "exhausted"


@pytest.mark.parametrize("rows,reason", [
    ([sample("codex", "primary", 5, hours_ago=0.2, reset_in_hours=10),
      sample("codex", "primary", 6, hours_ago=0, reset_in_hours=10)], "rate_unknown"),        # 12 min span
    ([sample("codex", "primary", 5, hours_ago=0.1, reset_in_hours=10)], "rate_unknown"),     # one fresh sample
    ([sample("codex", "primary", 5, hours_ago=2, reset_in_hours=10),
      sample("codex", "primary", 5, hours_ago=0.3, reset_in_hours=10)], "measurement_stale"),  # 18 min old
    (series("codex", "primary", 5, 6, reset_in_hours=-1), "window_already_reset"),
    ([dict(s, duration_minutes=None) for s in series("claude", "weird", 5, 6)], "window_duration_unknown"),
])
def test_unknown_measurements_never_become_a_verdict(rows, reason):
    window = next(iter(pace_windows(rows, now=NOW).values()))
    assert (window["verdict"], window["reason"]) == ("unknown", reason)


def test_exactly_the_minimum_span_and_freshest_age_still_pace():
    rows = [sample("codex", "primary", 5, hours_ago=0.75, reset_in_hours=10),
            sample("codex", "primary", 6, hours_ago=0.25, reset_in_hours=10)]
    assert pace_windows(rows, now=NOW)["codex/codex/primary"]["verdict"] != "unknown"


def test_only_the_current_window_instance_counts():
    # The previous week ended at 90 %; the new instance started at 0 %. Mixing them would invent a rate.
    old = sample("codex", "primary", 90, hours_ago=12, reset_in_hours=-1)
    new = series("codex", "primary", 0, 4, span_hours=4, reset_in_hours=160)
    window = pace_windows([old] + new, now=NOW)["codex/codex/primary"]
    assert window["samples"] == 2 and window["rate_percent_per_hour"] == 1.0


def test_a_falling_counter_inside_one_instance_is_unknown():
    rows = series("codex", "primary", 30, 20, reset_in_hours=10)
    assert pace_windows(rows, now=NOW)["codex/codex/primary"]["reason"] == "rate_unknown"


# ---------------------------------------------------------------- recommendations

def lanes(samples, catalog=None, current=None, work_mode="production"):
    catalog = catalog or approved_catalog()
    return recommend(catalog, pace_windows(samples, now=NOW), current or CURRENT, work_mode=work_mode)


def test_underused_pools_raise_one_lane_per_pool_in_priority_order():
    result = lanes(world())
    assert (result["claude-rco-1"]["verdict"], result["claude-rco-1"]["target_profile"]) == \
        ("raise", "claude-opus-5-5-xhigh")                       # reviewers first
    assert result["claude-rco-2"]["reasons"] == ["raise_queued_one_step_per_pool"]
    assert result["fable-5"]["verdict"] == "same"
    assert (result["codex-lead-1"]["verdict"], result["codex-lead-1"]["target_profile"]) == \
        ("raise", "codex-gpt-6-sol-high")                        # lead before tools
    assert result["codex-tools-1"]["reasons"] == ["raise_queued_one_step_per_pool"]


def test_an_unapproved_target_is_shown_but_never_raised_and_takes_no_slot():
    result = lanes(world(), catalog=CATALOG)                     # shipped catalog: nothing approved
    for lane in CURRENT:
        assert result[lane]["verdict"] == "park"
        assert result[lane]["reasons"] == ["capacity_would_expire_unused", "target_not_approved"]
    assert result["codex-tools-1"]["target_profile"] == "codex-gpt-6-sol-high"


def test_an_overrun_lowers_producers_and_never_reviewers():
    result = lanes(world(claude_week=(60, 80), week_reset=40))  # 80 + 2 * 40 = 160
    assert (result["fable-5"]["verdict"], result["fable-5"]["target_profile"]) == ("lower", "claude-opus-5-5-medium") \
        or result["fable-5"]["reasons"][-1] == "at_floor"
    assert result["claude-rco-1"]["reasons"] == ["pool_overrun_before_reset", "reviewer_never_lowered"]
    assert result["claude-rco-1"]["verdict"] == "park"


def test_a_producer_above_its_floor_is_lowered_on_overrun():
    current = dict(CURRENT, **{"fable-5": "claude-opus-5-5-xhigh"})
    result = lanes(world(claude_week=(60, 80), week_reset=40), current=current)
    assert (result["fable-5"]["verdict"], result["fable-5"]["target_profile"]) == ("lower", "claude-opus-5-5-medium")


def test_a_producer_at_its_floor_parks_on_overrun():
    result = lanes(world(claude_week=(60, 80), week_reset=40))
    assert result["fable-5"]["reasons"] == ["pool_overrun_before_reset", "at_floor"]


def test_a_short_window_blocks_a_raise_but_never_justifies_one():
    busy_5h = world(claude_5h=(10, 90))                          # 5 h window overruns, week underused
    result = lanes(busy_5h)
    assert result["claude-rco-1"]["verdict"] == "park" and "pool_overrun_before_reset" in result["claude-rco-1"]["reasons"]
    quiet_week = world(claude_week=(40, 44), claude_5h=(0, 0))   # week 44 + 0.4 * 100 = 84: on pace; 5 h idle
    assert lanes(quiet_week)["claude-rco-1"]["verdict"] == "same"


def test_unknown_capacity_parks_every_lane_of_that_pool():
    rows = series("codex", "primary", 5, 10)                     # no Claude samples at all
    result = lanes(rows)
    assert result["fable-5"]["reasons"] == ["capacity_unknown"]
    assert "claude/claude/seven_day" in result["fable-5"]["blocked_by"]
    assert result["codex-lead-1"]["verdict"] == "raise"


def test_an_unverified_current_profile_parks():
    result = lanes(world(), current={"fable-5": "claude-sonnet-5-xhigh"})  # not in fable-5's allowed list
    assert result["fable-5"]["reasons"] == ["current_profile_unverified"]
    assert result["codex-lead-1"]["reasons"] == ["current_profile_unverified"]


def test_a_lane_already_at_its_strongest_stays():
    current = dict(CURRENT, **{"claude-rco-1": "claude-opus-5-5-xhigh"})
    result = lanes(world(), current=current)
    assert (result["claude-rco-1"]["verdict"], result["claude-rco-1"]["reasons"]) == ("same", ["at_strongest"])
    assert result["claude-rco-2"]["verdict"] == "raise"         # the pool slot goes to the next lane


def test_conserve_lowers_producers_first_and_raises_nobody():
    current = dict(CURRENT, **{"fable-5": "claude-opus-5-5-xhigh", "codex-tools-1": "codex-gpt-6-sol-high"})
    result = lanes(world(), current=current, work_mode="conserve")
    assert (result["fable-5"]["verdict"], result["fable-5"]["reasons"]) == ("lower", ["work_mode_conserve"])
    assert result["codex-tools-1"]["verdict"] == "lower"
    assert all(result[lane]["verdict"] != "raise" for lane in result)


def test_planning_keeps_the_same_safety_rules():
    result = lanes(world(claude_week=(60, 80), week_reset=40), work_mode="planning")
    assert result["claude-rco-1"]["verdict"] == "park"          # an overrun still wins over planning


def test_unknown_work_mode_is_refused():
    with pytest.raises(ValueError):
        lanes(world(), work_mode="turbo")


def test_report_is_advisory_and_names_unobserved_providers():
    report = pace(approved_catalog(), DIGEST, world(), CURRENT, now=NOW)
    assert report["execution_allowed"] is False and report["catalog_sha256"] == DIGEST
    assert report["unpaced_providers"] == [{"provider": "grok", "reason": "capacity_unobserved"}]
    assert "per_lane_cost_not_attributed" in report["limitations"]


# ---------------------------------------------------------------- the observer store

def make_store(path: Path, rows: list[dict], *, wal: bool = False) -> Path:
    db = sqlite3.connect(path)
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE observations (sequence INTEGER PRIMARY KEY, provider TEXT NOT NULL, data TEXT NOT NULL)")
    for row in rows:
        db.execute("INSERT INTO observations(provider,data) VALUES (?,?)", (row["provider"], json.dumps(row)))
    db.commit()
    db.close()
    return path


def codex_row(used, at, reset):
    return {"provider": "codex", "observed_at": at.isoformat(),
            "payload": {"rateLimitsByLimitId": {"codex": {"limitId": "codex", "rateLimitReachedType": None,
                                                           "primary": {"usedPercent": used, "resetsAt": reset,
                                                                       "windowDurationMins": 10080}}}}}


def claude_row(week, hour5, at, week_reset, hour_reset):
    return {"provider": "claude", "observed_at": at.isoformat(),
            "payload": {"rate_limits": {"seven_day": {"used_percentage": week, "resets_at": week_reset},
                                        "five_hour": {"used_percentage": hour5, "resets_at": hour_reset}}}}


def test_samples_are_read_from_the_collector_row_format(tmp_path):
    now = datetime.now(timezone.utc)
    reset = int((now + timedelta(days=5)).timestamp())
    store = make_store(tmp_path / "obs.sqlite", [
        codex_row(5, now - timedelta(hours=10), reset), codex_row(15, now - timedelta(minutes=1), reset),
        claude_row(20, 3, now - timedelta(minutes=2), reset, int((now + timedelta(hours=2)).timestamp())),
        dict(codex_row(99, now, reset), reason="collection_failed"),  # a failed poll never counts, payload or not
        {"provider": "grok", "observed_at": now.isoformat()}])
    samples = read_samples(store)
    keys = sorted((s["provider"], s["window"], s["used_percent"]) for s in samples)
    assert keys == [("claude", "five_hour", 3.0), ("claude", "seven_day", 20.0),
                    ("codex", "primary", 5.0), ("codex", "primary", 15.0)]
    assert {s["duration_minutes"] for s in samples if s["window"] == "five_hour"} == {300}


def test_reading_never_changes_the_store(tmp_path):
    now = datetime.now(timezone.utc)
    store = make_store(tmp_path / "obs.sqlite", [codex_row(5, now, int((now + timedelta(days=1)).timestamp()))])
    before = hashlib.sha256(store.read_bytes()).hexdigest()
    read_samples(store)
    assert hashlib.sha256(store.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["obs.sqlite"]


def test_a_wal_store_is_refused(tmp_path):
    store = make_store(tmp_path / "obs.sqlite", [], wal=True)
    with pytest.raises(ValueError, match="WAL"):
        read_samples(store)


@pytest.mark.parametrize("content", [b"", b"not sqlite at all", b"SQLite format 3\x00" + b"\x00" * 84])
def test_non_stores_are_refused(tmp_path, content):
    path = tmp_path / "obs.sqlite"
    path.write_bytes(content)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        read_samples(path)


@pytest.mark.parametrize("payload", [None, [], "x", {"rateLimitsByLimitId": "x"},
                                     {"rateLimitsByLimitId": {"codex": {"limitId": "codex",
                                                                        "primary": {"usedPercent": "5"}}}}])
def test_hostile_rows_are_skipped(tmp_path, payload):
    now = datetime.now(timezone.utc)
    store = make_store(tmp_path / "obs.sqlite", [{"provider": "codex", "observed_at": now.isoformat(),
                                                  "payload": payload}])
    assert read_samples(store) == []


def test_cli_prints_one_report_and_exits_zero(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    reset = int((now + timedelta(days=5)).timestamp())
    store = make_store(tmp_path / "obs.sqlite", [codex_row(5, now - timedelta(hours=10), reset),
                                                 codex_row(6, now - timedelta(minutes=1), reset)])
    code = main(["--store", str(store), "--current-profiles", json.dumps({"codex-lead-1": "codex-gpt-5.6-sol-medium"})])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["execution_allowed"] is False
    assert report["lanes"]["codex-lead-1"]["target_profile"] == "codex-gpt-6-sol-high"


@pytest.mark.parametrize("current", ['[]', '{"nobody": "x"}', 'not json'])
def test_cli_refuses_bad_current_profiles(tmp_path, capsys, current):
    store = make_store(tmp_path / "obs.sqlite", [])
    assert main(["--store", str(store), "--current-profiles", current]) == 2
    assert json.loads(capsys.readouterr().out)["execution_allowed"] is False


def test_cli_refuses_a_missing_store(tmp_path, capsys):
    assert main(["--store", str(tmp_path / "missing.sqlite")]) == 2
    assert "error" in json.loads(capsys.readouterr().out)



def test_a_short_window_on_pace_does_not_block_a_raise():
    # Week underused (22 + 0.2 * 100 = 42 %), five-hour window on pace (60 + 10 * 2 = 80 %):
    # only an overrun of a short window blocks a raise.
    result = lanes(world(claude_5h=(50, 60)))
    assert (result["claude-rco-1"]["verdict"], result["claude-rco-1"]["target_profile"]) ==         ("raise", "claude-opus-5-5-xhigh")

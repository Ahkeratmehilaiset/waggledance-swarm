# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.burn_governor import (
    PROCEED,
    STOP,
    THROTTLE,
    _coerce_positive_number,
    evaluate,
    governor_decision,
    window_spend,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "burn_governor.py"


# --- window_spend ------------------------------------------------------------

def test_window_spend_sums_in_window_for_pool():
    recs = [
        {"pool": "codex", "ts": 950, "output_tokens": 100},   # in
        {"pool": "codex", "ts": 1000, "output_tokens": 50},   # in (at now)
        {"pool": "codex", "ts": 800, "output_tokens": 999},   # before window
        {"pool": "claude", "ts": 950, "output_tokens": 999},  # wrong pool
        {"pool": "codex", "ts": 1100, "output_tokens": 999},  # after now
        "garbage",                                            # malformed
        {"pool": "codex"},                                    # no ts -> skip
    ]
    r = window_spend(recs, "codex", window_start=900, now=1000)
    assert r["spend"] == 150
    assert r["turns"] == 2
    # "garbage" (non-Mapping) + {"pool":"codex"} (no ts) are dropped, surfaced.
    assert r["dropped"] == 2


def test_window_spend_dropped_counts_bad_token_in_window():
    recs = [
        {"pool": "codex", "ts": 950, "output_tokens": 100},   # ok
        {"pool": "codex", "ts": 960, "output_tokens": "lots"},  # in-window, bad tokens
        {"pool": "codex", "ts": 970},                          # in-window, missing tokens
    ]
    r = window_spend(recs, "codex", window_start=900, now=1000)
    assert r["spend"] == 100
    assert r["turns"] == 3       # all three are this-pool in-window turns
    assert r["dropped"] == 2     # the two with unusable token fields (fail-open guard)


# --- seam fixes: data-quality surfacing + cap config errors -------------------

def test_evaluate_surfaces_data_quality_when_records_dropped():
    recs = ["garbage", {"pool": "codex", "ts": 9990, "output_tokens": 10}]
    out = evaluate(recs, {"codex": {"cap": 1000, "window_seconds": 100}}, now=10000)
    assert out["codex"]["dropped"] == 1
    assert "data_quality" in out["codex"]
    assert "LOWER BOUND" in out["codex"]["data_quality"]


def test_string_numeric_cap_is_governed():
    d = governor_decision(850, "1000", window_start=0, now=100, reset_ts=None)
    assert d["governed"] is True
    assert d["decision"] == THROTTLE   # 0.85 >= 0.8, coerced from "1000"


def test_invalid_cap_flags_config_error_not_silent_ungoverned():
    d = governor_decision(500, "abc", window_start=0, now=100, reset_ts=None)
    assert d["decision"] == PROCEED
    assert d["governed"] is False
    assert d.get("config_error") is True
    assert "invalid cap" in d["reason"]


def test_none_cap_is_ungoverned_without_config_error():
    d = governor_decision(500, None, window_start=0, now=100, reset_ts=None)
    assert d["governed"] is False
    assert d.get("config_error") is None
    assert "no cap" in d["reason"]


def test_coerce_positive_number():
    assert _coerce_positive_number(100) == 100.0
    assert _coerce_positive_number("100") == 100.0
    assert _coerce_positive_number("abc") is None
    assert _coerce_positive_number(0) is None
    assert _coerce_positive_number(-5) is None
    assert _coerce_positive_number(True) is None   # bool rejected
    assert _coerce_positive_number(None) is None


# --- governor_decision -------------------------------------------------------

def test_no_cap_is_ungoverned_proceed():
    d = governor_decision(500, None, window_start=0, now=100, reset_ts=None)
    assert d["decision"] == PROCEED
    assert d["governed"] is False


def test_cap_reached_stops():
    d = governor_decision(1000, 1000, window_start=0, now=100, reset_ts=None)
    assert d["decision"] == STOP
    d2 = governor_decision(1200, 1000, window_start=0, now=100, reset_ts=None)
    assert d2["decision"] == STOP


def test_fraction_over_threshold_throttles():
    d = governor_decision(850, 1000, window_start=0, now=100, reset_ts=None, throttle_at=0.8)
    assert d["decision"] == THROTTLE
    assert d["fraction"] == 0.85


def test_projected_exhaustion_before_reset_throttles_even_under_threshold():
    # spend 300/1000 (0.3 < 0.8) but high rate -> projected to exhaust before reset
    d = governor_decision(300, 1000, window_start=9900, now=10000, reset_ts=10600)
    assert d["decision"] == THROTTLE
    assert "projected_exhaustion_ts" in d
    assert d["projected_exhaustion_ts"] < 10600


def test_projection_after_reset_proceeds():
    # low rate -> projected exhaustion is AFTER reset -> fine
    d = governor_decision(100, 1000, window_start=9900, now=10000, reset_ts=10600)
    assert d["decision"] == PROCEED


def test_within_budget_proceeds():
    d = governor_decision(100, 1000, window_start=0, now=100, reset_ts=None)
    assert d["decision"] == PROCEED


# --- evaluate ----------------------------------------------------------------

def test_evaluate_per_pool():
    recs = [
        {"pool": "codex", "ts": 9990, "output_tokens": 950},
        {"pool": "claude", "ts": 9990, "output_tokens": 10},
    ]
    budgets = {
        "codex": {"cap": 1000, "window_seconds": 100, "reset_ts": 10600},
        "claude": {"cap": 1000, "window_seconds": 100},
    }
    out = evaluate(recs, budgets, now=10000)
    assert out["codex"]["decision"] == THROTTLE      # 0.95 >= 0.8
    assert out["claude"]["decision"] == PROCEED
    assert out["codex"]["spend"] == 950


# --- CLI ---------------------------------------------------------------------

def test_cli_stop_exits_one(tmp_path):
    usage = tmp_path / "u.jsonl"
    usage.write_text(json.dumps({"pool": "codex", "ts": 9990, "output_tokens": 1000}) + "\n",
                     encoding="utf-8")
    budgets = tmp_path / "b.json"
    budgets.write_text(json.dumps({"codex": {"cap": 1000, "window_seconds": 100}}), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--usage", str(usage), "--budgets", str(budgets),
         "--now", "10000", "--json"],
        cwd=str(ROOT), text=True, capture_output=True, check=False,
    )
    assert r.returncode == 1, r.stderr
    assert json.loads(r.stdout)["codex"]["decision"] == STOP


def test_cli_proceed_exits_zero(tmp_path):
    usage = tmp_path / "u.jsonl"
    usage.write_text(json.dumps({"pool": "codex", "ts": 9990, "output_tokens": 10}) + "\n",
                     encoding="utf-8")
    budgets = tmp_path / "b.json"
    budgets.write_text(json.dumps({"codex": {"cap": 1000, "window_seconds": 100}}), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--usage", str(usage), "--budgets", str(budgets),
         "--now", "10000"],
        cwd=str(ROOT), text=True, capture_output=True, check=False,
    )
    assert r.returncode == 0, r.stderr

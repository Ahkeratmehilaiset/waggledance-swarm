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

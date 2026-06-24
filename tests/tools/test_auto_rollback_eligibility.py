# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.auto_rollback_eligibility import (
    AUTO_ROLLBACK,
    OPERATOR_ESCALATE,
    decide_rollback,
    is_known_green_consensus,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "auto_rollback_eligibility.py"

GOOD = "a" * 40
BAD = "b" * 40
OTHER = "c" * 40
TREE_GOOD = "deadbeef" * 5  # 40-char tree-hash stand-in


def _registry(green=True, consensus=True):
    return {GOOD: {"ci_green": green, "consensus_approved": consensus}}


def _signal(confirmed=True, required=2, observed=2, source="canary"):
    return {
        "confirmed": confirmed,
        "required_confirmations": required,
        "observed_confirmations": observed,
        "source": source,
    }


def _base(**over):
    d = {
        "offending_sha": BAD,
        "target_sha": GOOD,
        "result_tree": TREE_GOOD,
        "target_tree": TREE_GOOD,
        "known_green_registry": _registry(),
        "failure_signal": _signal(),
        "target_paths": ["waggledance/x.py"],
    }
    d.update(over)
    return d


# --- the one eligible case ---------------------------------------------------

def test_pure_revert_to_known_green_on_confirmed_signal_is_auto():
    r = decide_rollback(_base())
    assert r["decision"] == AUTO_ROLLBACK
    assert r["eligible"] is True
    assert r["receipt"]["target_sha"] == GOOD


# --- fail-closed escalations -------------------------------------------------

def test_target_not_known_green_escalates():
    r = decide_rollback(_base(known_green_registry={}))
    assert r["decision"] == OPERATOR_ESCALATE
    assert "known-green" in r["reason"]


def test_target_green_but_not_consensus_escalates():
    r = decide_rollback(_base(known_green_registry=_registry(green=True, consensus=False)))
    assert r["decision"] == OPERATOR_ESCALATE


def test_tree_mismatch_escalates():
    r = decide_rollback(_base(result_tree="f" * 40))  # != target_tree
    assert r["decision"] == OPERATOR_ESCALATE
    assert "tree" in r["reason"]


def test_unconfirmed_signal_escalates():
    r = decide_rollback(_base(failure_signal=_signal(confirmed=False)))
    assert r["decision"] == OPERATOR_ESCALATE


def test_underconfirmed_signal_escalates():
    # confirmed flag set but not enough observed confirmations (debounce) -> escalate.
    r = decide_rollback(_base(failure_signal=_signal(confirmed=True, required=3, observed=1)))
    assert r["decision"] == OPERATOR_ESCALATE


def test_forbidden_surface_target_escalates():
    for p in ["release-docker.yml", "CHANGELOG-tag", "stage2_cutover.md"]:
        r = decide_rollback(_base(target_paths=[p]))
        assert r["decision"] == OPERATOR_ESCALATE, p


def test_noop_target_equals_offending_escalates():
    r = decide_rollback(_base(target_sha=BAD))
    assert r["decision"] == OPERATOR_ESCALATE


def test_invalid_shas_escalate():
    assert decide_rollback(_base(offending_sha="short"))["decision"] == OPERATOR_ESCALATE
    assert decide_rollback(_base(target_sha=None))["decision"] == OPERATOR_ESCALATE
    assert decide_rollback(_base(target_sha="g" * 40))["decision"] == OPERATOR_ESCALATE  # non-hex


def test_missing_trees_escalate():
    assert decide_rollback(_base(result_tree=None))["decision"] == OPERATOR_ESCALATE
    assert decide_rollback(_base(target_tree=""))["decision"] == OPERATOR_ESCALATE


def test_missing_registry_escalates():
    r = decide_rollback(_base(known_green_registry=None))
    assert r["decision"] == OPERATOR_ESCALATE


def test_non_mapping_input_escalates():
    assert decide_rollback([])["decision"] == OPERATOR_ESCALATE


# --- strict-bool on the known-green predicate --------------------------------

def test_known_green_is_strict_bool_not_truthy_string():
    # "true"/1 must NOT qualify (no fail-open on a coerced truthy value).
    assert is_known_green_consensus(GOOD, {GOOD: {"ci_green": "true", "consensus_approved": "true"}}) is False
    assert is_known_green_consensus(GOOD, {GOOD: {"ci_green": 1, "consensus_approved": 1}}) is False
    assert is_known_green_consensus(GOOD, {GOOD: {"ci_green": True, "consensus_approved": True}}) is True
    assert is_known_green_consensus("short", _registry()) is False


# --- CLI ---------------------------------------------------------------------

def _run(payload, tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--decision-json", str(p), "--json"],
        cwd=str(ROOT), text=True, capture_output=True, check=False,
    )


def test_cli_auto_exit_zero(tmp_path):
    c = _run(_base(), tmp_path)
    assert c.returncode == 0, c.stderr
    assert json.loads(c.stdout)["decision"] == AUTO_ROLLBACK


def test_cli_escalate_exit_three(tmp_path):
    c = _run(_base(known_green_registry={}), tmp_path)
    assert c.returncode == 3
    assert json.loads(c.stdout)["decision"] == OPERATOR_ESCALATE

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.check_status_name_safe import check_status_name
from tools.check_bridge_changes_requested import _is_blocking_status


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_status_name_safe.py"


# --- the wedge this tool exists to prevent ----------------------------------

def test_pr1368_self_block_repro_is_flagged():
    # The exact status NAME fable-5 self-blocked PR #1368 with (2026-06-22): an
    # endorsement whose name embedded block/cleared tokens was read as a veto.
    status = "fable_1368_failclosed_endorse_verify_block_cleared_coverage"
    r = check_status_name(status)
    assert r["safe"] is False
    assert r["would_block"] is True
    assert any("block" in t for t in r["triggers"])
    assert "no_changes_requested" in r["suggestion"]


# --- exact blocking statuses -------------------------------------------------

def test_exact_blocking_statuses_flagged():
    for status in ("changes_requested", "blocked", "rco_block", "block_requested"):
        assert check_status_name(status)["safe"] is False, status


def test_changes_and_requested_token_pair_flagged():
    r = check_status_name("fable_changes_were_requested_by_peer")
    assert r["safe"] is False
    assert any("changes" in t and "requested" in t for t in r["triggers"])


def test_blocking_word_token_flagged():
    r = check_status_name("fable_review_blocking_issue_found")
    assert r["safe"] is False
    assert any("blocking" in t for t in r["triggers"])


# --- safe names --------------------------------------------------------------

def test_normal_status_is_safe():
    r = check_status_name("fable_1371_head_1e5adb2d_folds_rco1_review")
    assert r["safe"] is True
    assert r["would_block"] is False
    assert r["triggers"] == []


def test_approval_status_is_safe():
    assert check_status_name("rco_pass")["safe"] is True
    assert check_status_name("build_consensus_pass")["safe"] is True


def test_explicit_clear_forms_are_safe():
    # These are how you legitimately clear a prior block (not phantom-blocks).
    for status in ("no_changes_requested", "changes_requested_resolved",
                   "changes_requested_retracted"):
        r = check_status_name(status)
        assert r["safe"] is True, status
        assert r["is_clear_form"] is True, status


# --- faithfulness: never drift from the live classifier ----------------------

def test_verdict_matches_live_classifier_predicate():
    # The whole point: this linter's verdict must equal the gate's real predicate
    # for every case, so it can never give false comfort.
    corpus = [
        "changes_requested",
        "changes_requested_resolved",
        "rco_changes_requested_concurrence",
        "blocked",
        "rco_block",
        "block_requested",
        "not_blocked",
        "no_changes_requested",
        "preflight_block_cleared",
        "ack_changes_requested",
        "fable_1368_failclosed_endorse_verify_block_cleared_coverage",
        "fable_pr_review_pass",
        "rco_pass",
        "build_consensus_pass",
        "fable_blocking_issue",
        "fable_changes_requested_in_review",
        "",
    ]
    for status in corpus:
        assert check_status_name(status)["would_block"] == _is_blocking_status(status), status


# --- CLI ---------------------------------------------------------------------

def _run(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_safe_exit_zero():
    completed = _run("--status", "fable_pr_review_pass", "--json")
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["safe"] is True


def test_cli_would_block_exit_three():
    completed = _run("--status", "fable_verify_block_cleared", "--json")
    assert completed.returncode == 3
    assert json.loads(completed.stdout)["safe"] is False


def test_cli_empty_status_exit_two():
    completed = _run("--status", "   ")
    assert completed.returncode == 2

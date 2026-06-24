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

def test_real_phantom_block_repro_is_flagged():
    # The real status NAME that phantom-blocked PR #1372 (2026-06-23): a ready
    # handoff whose name embedded a bare "block" token (no cleared/resolved) was
    # read as a fresh veto. This name STILL phantom-blocks under the post-#1368
    # classifier (verified), so it is the durable repro for the wedge this tool
    # prevents.
    status = "fable_1372_ready_phantom_block_linter"
    r = check_status_name(status)
    assert r["safe"] is False
    assert r["would_block"] is True
    assert any("block" in t for t in r["triggers"])
    assert "no_changes_requested" in r["suggestion"]


def test_block_cleared_form_is_safe_post_1368():
    # #1368 (durable event-type gate) made *_block_cleared_* / resolution forms
    # NON-blocking on purpose: a status saying a block was CLEARED must not be
    # read as a fresh veto. The linter is a faithful wrapper, so it must report
    # such a name as SAFE. (This is the name that originally wedged #1368 pre-fix;
    # post-#1368 it is correctly safe — guarding against a regression that would
    # re-introduce the over-block #1368 removed.)
    status = "fable_1368_failclosed_endorse_verify_block_cleared_coverage"
    assert check_status_name(status)["safe"] is True
    assert check_status_name(status)["would_block"] is False


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
    # Use a name with a bare "block" token (no cleared/resolved) so it still
    # phantom-blocks under the post-#1368 classifier (verified).
    completed = _run("--status", "fable_1372_ready_phantom_block_linter", "--json")
    assert completed.returncode == 3
    assert json.loads(completed.stdout)["safe"] is False


def test_cli_empty_status_exit_two():
    completed = _run("--status", "   ")
    assert completed.returncode == 2

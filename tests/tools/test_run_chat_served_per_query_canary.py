# SPDX-License-Identifier: BUSL-1.1
"""Tests for the per-query receipt-coverage adversarial canary.

Two obligations, and the second is the one that matters:

  1. the canary reports green on an intact contract (liveness), and
  2. the canary is NOT VACUOUSLY GREEN -- it goes red when an expectation is violated, and it
     is red when a case fails for the WRONG reason.

A canary that cannot go red proves nothing, so ``test_canary_goes_red_*`` drives the reporting
path with a deliberately-violated expectation instead of trusting that it would have caught one.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import run_chat_served_per_query_canary as canary

ROOT = Path(__file__).resolve().parents[2]

_FAIL_CLOSED_CASES = {
    "missing_terminal",
    "duplicate_terminal",
    "reused_receipt_ref",
    "forged_content_hash",
    "wrong_query_binding",
    "callback_exception",
}


@pytest.fixture(scope="module")
def report():
    return canary.run_canary()


def test_matrix_is_all_green_and_complete(report):
    """Liveness: the intact contract reports green over the full named matrix."""
    assert report["all_expected"] is True
    assert report["summary"]["failed"] == 0
    assert report["summary"]["failed_cases"] == []
    names = {case["case"] for case in report["cases"]}
    assert names == _FAIL_CLOSED_CASES | {"valid_repeated_query_two_served_events"}
    assert report["summary"]["total"] == len(names)


def test_honest_repeated_query_is_covered(report):
    """A repeated query over two served events, each with its own genuine receipt, is honest
    coverage -- it must NOT be mistaken for a duplicate."""
    case = _case(report, "valid_repeated_query_two_served_events")
    assert case["actual_coverage_present"] is True
    assert case["actual_reason"] is None
    assert case["corpus_size"] == 2
    assert case["verified_bound"] == 2
    assert (case["missing"], case["forged_or_unbound"], case["gapped"]) == (0, 0, 0)
    assert (case["duplicate_terminal"], case["duplicate_query_terminal"]) == (0, 0)


@pytest.mark.parametrize("name", sorted(_FAIL_CLOSED_CASES))
def test_every_adversarial_case_fails_closed(report, name):
    """Each forgery/omission drives coverage_present to False -- never a pass."""
    case = _case(report, name)
    assert case["actual_coverage_present"] is False
    assert case["passed"] is True
    assert case["verified_bound"] < case["corpus_size"]


@pytest.mark.parametrize("name", sorted(_FAIL_CLOSED_CASES))
def test_every_adversarial_case_fails_for_its_named_reason(report, name):
    """Fail-closed is not enough: a case that fails for an unrelated reason would let the real
    forgery clause rot undetected behind a green matrix."""
    case = _case(report, name)
    assert case["actual_reason"] is not None
    assert case["actual_reason"].startswith(case["expected_reason_prefix"])


def test_injective_direction_is_covered(report):
    """The bijection's INJECTIVE direction: two served events sharing ONE receipt_ref. Each has
    exactly one terminal, so the surjective clauses see nothing wrong -- only uniqueness catches
    it. Without this, one receipt would falsely cover two served events."""
    case = _case(report, "reused_receipt_ref")
    assert case["actual_reason"].startswith("duplicate_receipt_ref")
    assert case["duplicate_terminal"] == 0      # surjective clauses are blind here
    assert case["missing"] == 0
    assert case["actual_coverage_present"] is False


def test_report_is_deterministic():
    """Byte-stable across runs: no clock, no randomness, no environment read."""
    first = json.dumps(canary.run_canary(), indent=2, sort_keys=True)
    second = json.dumps(canary.run_canary(), indent=2, sort_keys=True)
    assert first == second


def test_report_declares_measurement_only_authority(report):
    """The canary carries evidence, never authority."""
    assert report["authority"] == "measurement_only"
    assert report["measurement_marker"] == "measurement_not_a_correctness_gate"
    assert report["canary"] == "wd.chat_served_per_query_canary.v1"


def test_canary_goes_red_when_an_expectation_is_violated(monkeypatch):
    """Anti-vacuity: assert the reporting path REALLY fails when a case defies its expectation.
    Drive the honest liveness fixture while claiming it should fail closed -- it passes, so the
    canary must report red."""
    monkeypatch.setattr(canary, "_MATRIX", (
        ("liveness_case_wrongly_expected_to_fail", False, "bijection_unmet",
         canary._case_valid_repeated_query),
    ))
    report = canary.run_canary()
    assert report["all_expected"] is False
    assert report["summary"]["failed"] == 1
    assert report["summary"]["failed_cases"] == ["liveness_case_wrongly_expected_to_fail"]


def test_canary_goes_red_when_a_case_fails_for_the_wrong_reason(monkeypatch):
    """Anti-vacuity: a case that fails closed but for an unrelated reason is still red."""
    monkeypatch.setattr(canary, "_MATRIX", (
        ("missing_terminal_wrong_reason", False, "duplicate_receipt_ref",
         canary._case_missing_terminal),
    ))
    report = canary.run_canary()
    assert report["all_expected"] is False
    assert report["summary"]["failed_cases"] == ["missing_terminal_wrong_reason"]
    case = report["cases"][0]
    assert case["actual_coverage_present"] is False      # it DID fail closed...
    assert case["passed"] is False                       # ...but for the wrong reason -> red


def test_main_emits_parseable_json_and_exits_zero(capsys):
    exit_code = canary.main([])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_expected"] is True
    assert payload["summary"]["failed"] == 0


def test_main_exits_nonzero_when_red(monkeypatch, capsys):
    """Usable as a probe: a red matrix must be observable from the exit code alone."""
    monkeypatch.setattr(canary, "_MATRIX", (
        ("forced_red", False, None, canary._case_valid_repeated_query),
    ))
    assert canary.main([]) == 1
    assert json.loads(capsys.readouterr().out)["all_expected"] is False


def test_cli_runs_offline_and_writes_nothing(tmp_path):
    """End-to-end through the real CLI, from a scratch cwd: JSON on stdout, exit 0, and no file
    is created -- the canary reads and writes nothing."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "run_chat_served_per_query_canary.py"), "--json"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["all_expected"] is True
    assert list(tmp_path.iterdir()) == []


def _case(report, name):
    matches = [case for case in report["cases"] if case["case"] == name]
    assert matches, "case %s missing from the canary matrix" % name
    return matches[0]

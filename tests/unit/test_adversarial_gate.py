# SPDX-License-Identifier: BUSL-1.1
"""Forge-probe tests for the fail-closed adversarial-corpus promotion gate."""
from __future__ import annotations

from waggledance.core.magma.adversarial_gate import (
    verify_adversarial_corpus_gate,
)

SOLVER = "a" * 64
OTHER = "b" * 64
REQUIRED_DEFECT_CLASS_COUNTS = {
    "spec-gaming/reward-hacking": 1,
    "fail-open": 1,
    "hallucinated-success": 1,
}


def _report(
    *,
    cases,
    bound=SOLVER,
    case_count=None,
    top_ok=True,
    defect_class_counts=None,
):
    if case_count is None:
        case_count = len(cases)
    return {
        "eval_version": "magma.adversarial_eval.v0",
        "ok": top_ok,
        "bound_solver_hash": bound,
        "case_count": case_count,
        "corpus_digest": "deadbeef",
        "cases": cases,
        "coverage": {
            "defect_class_counts": (
                REQUIRED_DEFECT_CLASS_COUNTS
                if defect_class_counts is None
                else dict(defect_class_counts)
            )
        },
    }


def _ok_cases(n):
    return [{"case_id": f"c{i}", "ok": True, "status": "full_match"} for i in range(n)]


def test_happy_path_all_caught_bound_passes():
    r = _report(cases=_ok_cases(20))
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is True
    assert result.decision == "adversarial_gate_pass"
    assert result.caught_count == 20 and result.not_caught_count == 0


def test_forged_top_ok_with_one_uncaught_case_refuses():
    # report['ok']=True but a case is not caught -> must re-derive and refuse.
    cases = _ok_cases(19) + [{"case_id": "evil", "ok": False, "status": "mismatch"}]
    r = _report(cases=cases, top_ok=True)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert result.not_caught_count == 1


def test_type_confused_case_count_refuses():
    r = _report(cases=_ok_cases(20))
    r["case_count"] = "20"  # string, not int
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("type confusion" in x for x in result.reasons)


def test_type_confused_case_ok_string_is_not_caught():
    # ok="true" (string) must NOT count as caught.
    cases = _ok_cases(19) + [{"case_id": "x", "ok": "true"}]
    r = _report(cases=cases)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert result.invalid_case_count == 1


def test_binding_mismatch_refuses():
    r = _report(cases=_ok_cases(20), bound=OTHER)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("bound_solver_hash" in x for x in result.reasons)


def test_missing_binding_refuses():
    r = _report(cases=_ok_cases(20))
    del r["bound_solver_hash"]
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False


def test_empty_report_refuses():
    for bad in ({}, None, [], "report"):
        result = verify_adversarial_corpus_gate(report=bad, expected_solver_hash=SOLVER, min_cases=10)
        assert result.ok is False
        assert result.decision == "missing_or_empty_report"


def test_below_floor_refuses():
    r = _report(cases=_ok_cases(5))
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("below floor" in x for x in result.reasons)


def test_cases_not_a_list_refuses():
    r = _report(cases=_ok_cases(20))
    r["cases"] = {"c0": {"ok": True}}  # mapping, not list
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False


def test_case_count_mismatch_refuses():
    r = _report(cases=_ok_cases(20), case_count=42)  # lies about the count
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("!=" in x for x in result.reasons)


def test_missing_required_defect_class_coverage_refuses():
    r = _report(
        cases=_ok_cases(20),
        defect_class_counts={
            "spec-gaming/reward-hacking": 20,
            "fail-open": 20,
        },
    )
    result = verify_adversarial_corpus_gate(
        report=r, expected_solver_hash=SOLVER, min_cases=10
    )
    assert result.ok is False
    assert any(
        "missing required defect_class coverage 'hallucinated-success'" in x
        for x in result.reasons
    )


def test_invalid_expected_solver_hash_refuses():
    r = _report(cases=_ok_cases(20))
    for bad in ("", "   ", None, 123):
        result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=bad, min_cases=10)
        assert result.ok is False


def test_non_bool_min_cases_refuses():
    r = _report(cases=_ok_cases(20))
    for bad in (True, 0, -1, "10"):
        result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=bad)
        assert result.ok is False

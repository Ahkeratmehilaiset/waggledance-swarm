# SPDX-License-Identifier: BUSL-1.1
"""Forge-probe tests for the fail-closed adversarial-corpus promotion gate."""
from __future__ import annotations

import pytest

from waggledance.core.magma.adversarial_corpus_eval import (
    CRITICAL_DEFECT_TYPES,
    REQUIRED_DEFECT_TYPES,
)
from waggledance.core.magma.adversarial_gate import (
    verify_adversarial_corpus_gate,
)

SOLVER = "a" * 64
OTHER = "b" * 64


def _report(*, cases, bound=SOLVER, case_count=None, top_ok=True):
    if case_count is None:
        case_count = len(cases)
    return {
        "eval_version": "magma.adversarial_eval.v0",
        "ok": top_ok,
        "bound_solver_hash": bound,
        "case_count": case_count,
        "corpus_digest": "deadbeef",
        "cases": cases,
    }


def _required_types() -> list[str]:
    return sorted(REQUIRED_DEFECT_TYPES)


def _ok_cases(n: int):
    required = _required_types()
    return [
        {
            "case_id": f"case:adv:test_case:{i:03d}",
            "defect_class": required[i % len(required)],
            "ok": True,
            "status": "full_match",
        }
        for i in range(n)
    ]


def _case_with_defect(*, case_id: str, defect_class: str, ok=True, status="full_match"):
    return {
        "case_id": case_id,
        "defect_class": defect_class,
        "ok": ok,
        "status": status,
    }


def test_happy_path_all_caught_bound_passes():
    r = _report(cases=_ok_cases(30))
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is True
    assert result.decision == "adversarial_gate_pass"
    assert result.caught_count == 30 and result.not_caught_count == 0


def test_forged_top_ok_with_one_uncaught_case_refuses():
    # report['ok']=True but a case is not caught -> must re-derive and refuse.
    cases = _ok_cases(30) + [
        _case_with_defect(
            case_id="case:adv:test_case:900",
            defect_class=_required_types()[0],
            ok=False,
            status="mismatch",
        )
    ]
    r = _report(cases=cases, top_ok=True)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert result.not_caught_count == 1


def test_type_confused_case_count_refuses():
    r = _report(cases=_ok_cases(30))
    r["case_count"] = "20"  # string, not int
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("type confusion" in x for x in result.reasons)


def test_type_confused_case_ok_string_is_not_caught():
    # ok="true" (string) must NOT count as caught.
    cases = _ok_cases(30) + [
        _case_with_defect(
            case_id="case:adv:test_case:901",
            defect_class=_required_types()[0],
            ok="true",
        )
    ]
    r = _report(cases=cases)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert result.invalid_case_count == 1


def test_invalid_defect_class_is_rejected():
    cases = _ok_cases(30) + [
        {
            "case_id": "case:adv:test_case:902",
            "defect_class": "bogus_defect",
            "ok": True,
            "status": "full_match",
        }
    ]
    r = _report(cases=cases)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("missing/invalid defect_class" in reason for reason in result.reasons)
    assert result.invalid_case_count >= 1


@pytest.mark.parametrize(
    "bad_case_id",
    [
        None,
        "",
        " ",
        " case:adv:test_case:001",
        "case:adv:test_case:001 ",
        "case:adv:test_case:001\nsuffix",
        "case:adv:test-case:001",
        "case:adv:x:001",
        "case:adv:test_case:01",
        123,
    ],
)
def test_missing_or_invalid_case_id_refuses(bad_case_id):
    cases = _ok_cases(30)
    cases[0]["case_id"] = bad_case_id
    r = _report(cases=cases)

    result = verify_adversarial_corpus_gate(
        report=r,
        expected_solver_hash=SOLVER,
        min_cases=10,
    )

    assert result.ok is False
    assert any("missing/invalid case_id" in reason for reason in result.reasons)


def test_duplicate_case_ids_cannot_pad_critical_defect_floors():
    cases = []
    for defect_class in _required_types():
        case_id_stem = defect_class.replace("-", "_")
        first_case_id = f"case:adv:{case_id_stem}:001"
        cases.append(
            _case_with_defect(
                case_id=first_case_id,
                defect_class=defect_class,
            )
        )
        cases.append(
            _case_with_defect(
                case_id=(
                    first_case_id
                    if defect_class in CRITICAL_DEFECT_TYPES
                    else f"case:adv:{case_id_stem}:002"
                ),
                defect_class=defect_class,
            )
        )
    r = _report(cases=cases)

    result = verify_adversarial_corpus_gate(
        report=r,
        expected_solver_hash=SOLVER,
        min_cases=10,
    )

    assert result.ok is False
    assert any("duplicate case_id" in reason for reason in result.reasons)
    floor_reason = next(
        reason
        for reason in result.reasons
        if "critical defect classes below caught floor" in reason
    )
    for defect_class in CRITICAL_DEFECT_TYPES:
        assert f"{defect_class}=1" in floor_reason


def test_case_ids_must_be_unique_across_defect_classes():
    cases = _ok_cases(30)
    assert cases[0]["defect_class"] != cases[1]["defect_class"]
    cases[1]["case_id"] = cases[0]["case_id"]
    r = _report(cases=cases)

    result = verify_adversarial_corpus_gate(
        report=r,
        expected_solver_hash=SOLVER,
        min_cases=10,
    )

    assert result.ok is False
    assert any("duplicate case_id" in reason for reason in result.reasons)


def test_missing_required_defect_class_refuses():
    required = _required_types()
    cases = [
        _case_with_defect(
            case_id=f"case:adv:test_case:{i:03d}",
            defect_class=required[i],
        )
        for i in range(len(required) - 1)
    ]
    # Missing one required class while all listed cases are caught.
    # Gate should still refuse due missing required class coverage.
    r = _report(cases=cases)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=2)
    assert result.ok is False
    assert any("required defect classes not caught" in reason for reason in result.reasons)


def test_critical_defect_floor_is_rederived_from_per_case_reports():
    for defect_class in ("governance_bypass", "path_escape"):
        cases = _ok_cases(30)
        removed_one = False
        one_below_floor = []
        for case in cases:
            if case["defect_class"] == defect_class and not removed_one:
                removed_one = True
                continue
            one_below_floor.append(case)
        r = _report(cases=one_below_floor)

        result = verify_adversarial_corpus_gate(
            report=r,
            expected_solver_hash=SOLVER,
            min_cases=10,
        )

        assert result.ok is False
        assert any(
            f"{defect_class}=1" in reason and "critical defect classes below caught floor" in reason
            for reason in result.reasons
        )


def test_binding_mismatch_refuses():
    r = _report(cases=_ok_cases(30), bound=OTHER)
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("bound_solver_hash" in x for x in result.reasons)


def test_missing_binding_refuses():
    r = _report(cases=_ok_cases(30))
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
    class HostileList(list):
        def __len__(self):
            return 0

    for bad_cases in (
        {"c0": {"ok": True}},
        tuple(_ok_cases(30)),
        HostileList(_ok_cases(30)),
    ):
        r = _report(cases=bad_cases)
        result = verify_adversarial_corpus_gate(
            report=r,
            expected_solver_hash=SOLVER,
            min_cases=10,
        )
        assert result.ok is False
        assert any("not a list" in reason for reason in result.reasons)


def test_case_count_mismatch_refuses():
    r = _report(cases=_ok_cases(30), case_count=42)  # lies about the count
    result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=10)
    assert result.ok is False
    assert any("!=" in x for x in result.reasons)


def test_invalid_expected_solver_hash_refuses():
    r = _report(cases=_ok_cases(30))
    for bad in ("", "   ", None, 123):
        result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=bad, min_cases=10)
        assert result.ok is False


def test_non_bool_min_cases_refuses():
    r = _report(cases=_ok_cases(30))
    for bad in (True, 0, -1, "10"):
        result = verify_adversarial_corpus_gate(report=r, expected_solver_hash=SOLVER, min_cases=bad)
        assert result.ok is False

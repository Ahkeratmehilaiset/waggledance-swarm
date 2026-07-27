# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed adversarial-corpus promotion gate verifier (Track T5, step 1).

The MAGMA synthetic adversarial corpus only provides safety if its evaluation
is *enforced* before a solver is promoted. This module is the verification
half: given an adversarial-eval report (as produced by
``tools/run_magma_adversarial_eval.build_adversarial_eval_report``) and the
exact hash of the solver being promoted, it re-derives a pass/fail verdict
fail-closed.

Design follows the RCO T5 conditions (same fail-closed DNA as the T0b
bridge-consensus approver):

1. FAIL-CLOSED — a missing / empty / malformed report, or any case not caught,
   refuses. Absence never default-allows.
2. EXACT-ARTIFACT BINDING — the report must carry ``bound_solver_hash`` equal
   to the solver being promoted; a stale report from a different solver or a
   prior version cannot authorize this promotion.
3. RE-DERIVE THE VERDICT — the pass/fail is recomputed from the per-case
   results, NOT read from the report's top-level ``ok`` flag, and numeric
   fields are type-checked (a forged ``case_count="42"`` or ``ok="true"`` is
   rejected as type-confusion).

This module is intentionally pure (no I/O, no tools/ import): the eval is run
upstream and the report is passed in, so a runtime consumer re-derives the
verdict rather than trusting it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from waggledance.core.magma.adversarial_corpus_eval import (
    ADVERSARIAL_CASE_ID_PATTERN,
    CRITICAL_DEFECT_TYPES,
    MIN_CRITICAL_DEFECT_CASES,
    REQUIRED_DEFECT_TYPES,
)


@dataclass(frozen=True)
class AdversarialGateResult:
    """Verdict of the fail-closed adversarial-corpus gate."""

    ok: bool
    decision: str
    reasons: tuple[str, ...]
    expected_solver_hash: str
    bound_solver_hash: str
    case_count: int
    caught_count: int
    not_caught_count: int
    invalid_case_count: int


def verify_adversarial_corpus_gate(
    *,
    report: Any,
    expected_solver_hash: str,
    min_cases: int,
) -> AdversarialGateResult:
    """Re-derive a fail-closed verdict for promoting ``expected_solver_hash``.

    Returns ``ok=True`` only if ALL hold: the report is a non-empty mapping
    bound to ``expected_solver_hash``; it lists at least ``min_cases`` cases;
    every case has a unique ``case_id`` matching the canonical adversarial-case
    schema pattern and is independently caught (per-case ``ok is True``); the
    declared ``case_count`` is an int equal to the number of case entries; every
    required defect class is caught by at least one distinct case; every
    critical defect class meets the distinct-case caught coverage floor; and
    there are no invalid/type-confused entries. Any deviation refuses.
    """
    reasons: list[str] = []

    if not isinstance(expected_solver_hash, str) or not expected_solver_hash.strip():
        # A gate with no solver to bind to cannot authorize anything.
        return _fail(
            "invalid_expected_solver_hash",
            ["expected_solver_hash must be a non-empty string"],
            expected_solver_hash if isinstance(expected_solver_hash, str) else "",
            "",
            0,
            0,
            0,
            0,
        )
    if not isinstance(min_cases, int) or isinstance(min_cases, bool) or min_cases < 1:
        return _fail(
            "invalid_min_cases",
            ["min_cases must be a positive int"],
            expected_solver_hash,
            "",
            0,
            0,
            0,
            0,
        )

    if not isinstance(report, Mapping) or not report:
        return _fail(
            "missing_or_empty_report",
            ["adversarial-eval report is missing or empty (fail-closed)"],
            expected_solver_hash,
            "",
            0,
            0,
            0,
            0,
        )

    # (2) exact-artifact binding — the report must name the solver being promoted.
    bound = report.get("bound_solver_hash")
    bound_str = bound if isinstance(bound, str) else ""
    if not bound_str:
        reasons.append("report carries no bound_solver_hash (cannot bind to solver)")
    elif bound_str != expected_solver_hash:
        reasons.append(
            "report bound_solver_hash does not match the solver being promoted"
        )

    # (3) re-derive from per-case results; never trust report['ok'].
    cases = report.get("cases")
    if type(cases) is not list:
        reasons.append("report 'cases' is not a list (fail-closed)")
        cases = []

    caught = 0
    not_caught = 0
    invalid = 0
    invalid_shape = 0
    invalid_case_id = 0
    duplicate_case_id = 0
    invalid_defect_class = 0
    invalid_ok = 0
    seen_case_ids: set[str] = set()
    duplicate_case_ids: set[str] = set()
    caught_by_defect_class = {defect: 0 for defect in REQUIRED_DEFECT_TYPES}
    for case in cases:
        if not isinstance(case, Mapping):
            invalid_shape += 1
            invalid += 1
            continue
        case_id = case.get("case_id")
        valid_case_id = (
            isinstance(case_id, str)
            and ADVERSARIAL_CASE_ID_PATTERN.fullmatch(case_id) is not None
        )
        unique_case_id = False
        if not valid_case_id:
            invalid_case_id += 1
        elif case_id in seen_case_ids:
            duplicate_case_id += 1
            duplicate_case_ids.add(case_id)
        else:
            seen_case_ids.add(case_id)
            unique_case_id = True

        case_ok = case.get("ok")
        defect_class = case.get("defect_class")
        valid_defect_class = (
            isinstance(defect_class, str) and defect_class in REQUIRED_DEFECT_TYPES
        )
        case_invalid = not unique_case_id
        if not valid_defect_class:
            invalid_defect_class += 1
            case_invalid = True

        # Strict bool: a missing or non-bool ok (e.g. the string "true") is
        # treated as NOT caught — type-confusion must not pass.
        if case_ok is True:
            if valid_defect_class and unique_case_id:
                caught += 1
                caught_by_defect_class[defect_class] += 1
        elif case_ok is False:
            not_caught += 1
        else:
            # Count a malformed case once even if more than one field is bad.
            invalid_ok += 1
            case_invalid = True

        if case_invalid:
            invalid += 1

    n_cases = len(cases)
    distinct_valid_case_count = len(seen_case_ids)

    # type-confusion guard on the declared count.
    declared = report.get("case_count")
    if not isinstance(declared, int) or isinstance(declared, bool):
        reasons.append("case_count is not an int (type confusion)")
    elif declared != n_cases:
        reasons.append(
            f"case_count ({declared}) != number of case entries ({n_cases})"
        )

    if distinct_valid_case_count < min_cases:
        reasons.append(
            "corpus below floor: "
            f"{distinct_valid_case_count} distinct valid cases "
            f"< required minimum {min_cases}"
        )
    if not_caught > 0:
        reasons.append(f"{not_caught} adversarial case(s) NOT caught")
    if invalid > 0:
        if invalid_shape > 0:
            reasons.append(f"{invalid_shape} case(s) not objects (fail-closed)")
        if invalid_case_id > 0:
            reasons.append(
                f"{invalid_case_id} case(s) with missing/invalid "
                "case_id (fail-closed)"
            )
        if duplicate_case_id > 0:
            reasons.append(
                f"{duplicate_case_id} duplicate case_id occurrence(s) across "
                f"{len(duplicate_case_ids)} value(s) (fail-closed)"
            )
        if invalid_defect_class > 0:
            reasons.append(
                f"{invalid_defect_class} case(s) with missing/invalid "
                "defect_class (fail-closed)"
            )
        if invalid_ok > 0:
            reasons.append(
                f"{invalid_ok} case(s) with missing/invalid 'ok' (fail-closed)"
            )
    missing_defect_classes = [
        defect
        for defect, count in caught_by_defect_class.items()
        if count == 0
    ]
    if missing_defect_classes:
        reasons.append(
            "required defect classes not caught: "
            + ", ".join(sorted(missing_defect_classes))
        )
    critical_below_floor = [
        defect
        for defect in sorted(CRITICAL_DEFECT_TYPES)
        if caught_by_defect_class.get(defect, 0) < MIN_CRITICAL_DEFECT_CASES
    ]
    if critical_below_floor:
        details = ", ".join(
            f"{defect}={caught_by_defect_class.get(defect, 0)}"
            for defect in critical_below_floor
        )
        reasons.append(
            "critical defect classes below caught floor "
            f"{MIN_CRITICAL_DEFECT_CASES}: {details}"
        )

    # Final verdict re-derived: every listed case caught, none invalid, floor met,
    # bound to the right solver. report['ok'] is deliberately NOT consulted.
    ok = (
        not reasons
        and distinct_valid_case_count >= min_cases
        and caught == distinct_valid_case_count
        and not_caught == 0
        and invalid == 0
        and bound_str == expected_solver_hash
    )
    return AdversarialGateResult(
        ok=ok,
        decision="adversarial_gate_pass" if ok else "adversarial_gate_refused",
        reasons=tuple(reasons),
        expected_solver_hash=expected_solver_hash,
        bound_solver_hash=bound_str,
        case_count=n_cases,
        caught_count=caught,
        not_caught_count=not_caught,
        invalid_case_count=invalid,
    )


def _fail(
    decision: str,
    reasons: list[str],
    expected_solver_hash: str,
    bound_solver_hash: str,
    case_count: int,
    caught: int,
    not_caught: int,
    invalid: int,
) -> AdversarialGateResult:
    return AdversarialGateResult(
        ok=False,
        decision=decision,
        reasons=tuple(reasons),
        expected_solver_hash=expected_solver_hash,
        bound_solver_hash=bound_solver_hash,
        case_count=case_count,
        caught_count=caught,
        not_caught_count=not_caught,
        invalid_case_count=invalid,
    )

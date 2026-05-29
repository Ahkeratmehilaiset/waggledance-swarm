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
from typing import Any, Mapping, Sequence


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
    every case is independently caught (per-case ``ok is True``); the declared
    ``case_count`` is an int equal to the number of case entries; and there are
    no invalid/type-confused entries. Any deviation refuses.
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
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        reasons.append("report 'cases' is not a list (fail-closed)")
        cases = []

    caught = 0
    not_caught = 0
    invalid = 0
    for case in cases:
        if not isinstance(case, Mapping):
            invalid += 1
            continue
        case_ok = case.get("ok")
        # Strict bool: a missing or non-bool ok (e.g. the string "true") is
        # treated as NOT caught — type-confusion must not pass.
        if case_ok is True:
            caught += 1
        elif case_ok is False:
            not_caught += 1
        else:
            invalid += 1

    n_cases = len(list(cases))

    # type-confusion guard on the declared count.
    declared = report.get("case_count")
    if not isinstance(declared, int) or isinstance(declared, bool):
        reasons.append("case_count is not an int (type confusion)")
    elif declared != n_cases:
        reasons.append(
            f"case_count ({declared}) != number of case entries ({n_cases})"
        )

    if n_cases < min_cases:
        reasons.append(
            f"corpus below floor: {n_cases} cases < required minimum {min_cases}"
        )
    if not_caught > 0:
        reasons.append(f"{not_caught} adversarial case(s) NOT caught")
    if invalid > 0:
        reasons.append(
            f"{invalid} case(s) with missing/invalid 'ok' (fail-closed)"
        )

    # Final verdict re-derived: every listed case caught, none invalid, floor met,
    # bound to the right solver. report['ok'] is deliberately NOT consulted.
    ok = (
        not reasons
        and n_cases >= min_cases
        and caught == n_cases
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

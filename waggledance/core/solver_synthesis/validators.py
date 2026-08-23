# SPDX-License-Identifier: BUSL-1.1
"""Solver validators — Phase 9 §U3.

Combines syntactic + semantic + property + regression + shadow
validation into a deterministic SolverValidationReport. Backend-
agnostic: same outputs on CPU and (future) GPU batch backends.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from . import SOLVER_SYNTHESIS_SCHEMA_VERSION
from .solver_candidate_store import SolverCandidate


VERDICTS = (
    "pass_all_gates",
    "needs_more_shadow",
    "regression_detected",
    "property_test_failed",
    "syntactic_invalid",
    "semantic_invalid",
    "rejected_low_value",
)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"passed": self.passed, "errors": list(self.errors)}


@dataclass(frozen=True)
class CountedGateResult:
    passed: int
    total: int
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"passed": self.passed, "total": self.total,
                "failures": list(self.failures)}


@dataclass(frozen=True)
class ShadowEvalResult:
    observations: int
    concordance_ratio: float

    def to_dict(self) -> dict:
        return {"observations": self.observations,
                "concordance_ratio": self.concordance_ratio}


@dataclass(frozen=True)
class SolverValidationReport:
    schema_version: int
    candidate_id: str
    syntactic: GateResult
    semantic: GateResult
    property_tests: CountedGateResult
    regression: CountedGateResult
    shadow_evaluation: ShadowEvalResult
    verdict: str
    produced_at_iso: str
    execution_backend: str

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown verdict: {self.verdict!r}")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "syntactic": self.syntactic.to_dict(),
            "semantic": self.semantic.to_dict(),
            "property_tests": self.property_tests.to_dict(),
            "regression": self.regression.to_dict(),
            "shadow_evaluation": self.shadow_evaluation.to_dict(),
            "verdict": self.verdict,
            "produced_at_iso": self.produced_at_iso,
            "execution_backend": self.execution_backend,
        }


# ── Syntactic / semantic / property / regression / shadow ────────-

def syntactic_validate(c: SolverCandidate) -> GateResult:
    errors: list[str] = []
    if not c.solver_name:
        errors.append("solver_name empty")
    if not isinstance(c.spec_or_code, dict):
        errors.append("spec_or_code must be a dict")
    if not c.cell_id:
        errors.append("cell_id empty")
    return GateResult(passed=not errors, errors=tuple(errors))


def semantic_validate(c: SolverCandidate) -> GateResult:
    """Light-weight semantic checks; full SMT is a future Phase Q+
    addition. We verify that any 'invariants' field is non-empty
    string list, etc."""
    errors: list[str] = []
    invariants = c.spec_or_code.get("invariants") or []
    for i in invariants:
        if not isinstance(i, str) or not i.strip():
            errors.append(f"invalid invariant: {i!r}")
    expected = c.spec_or_code.get("expected_output_unit")
    if expected is not None and not isinstance(expected, str):
        errors.append("expected_output_unit must be str or None")
    return GateResult(passed=not errors, errors=tuple(errors))


def _case_passed(case: object) -> bool:
    """Fail-closed pass predicate for a pre-computed test case.

    Only a mapping whose ``"passed"`` value is the literal ``True``
    counts as passed. Truthy stand-ins (``"false"``, ``"true"``,
    ``1``, non-empty containers), non-bool objects, a missing key and
    non-mapping entries are all treated as failures. The value is
    compared by identity, so no ``__bool__`` / truthiness coercion
    path is ever taken.
    """
    if not isinstance(case, Mapping):
        return False
    return case.get("passed") is True


def _case_name(case: object, index: int) -> str:
    if isinstance(case, Mapping):
        return str(case.get("name", ""))
    return f"<malformed:{index}>"


def _count_cases(cases: list[dict] | None) -> CountedGateResult:
    """Count passed cases and name the failures from ONE read per case.

    Each case's verdict is evaluated exactly once and frozen before
    the count and the failure list are derived from it, so a stateful
    or mutating mapping cannot answer ``True`` to the counter and
    ``False`` to the failure list (or vice versa) and leave
    ``passed == total`` next to a non-empty failure list.
    """
    cases = list(cases or [])
    passed = 0
    failures: list[str] = []
    for index, case in enumerate(cases):
        if _case_passed(case):
            passed += 1
        else:
            failures.append(_case_name(case, index))
    return CountedGateResult(passed=passed, total=len(cases),
                                  failures=tuple(failures))


def run_property_tests(c: SolverCandidate,
                            tests: list[dict] | None = None
                            ) -> CountedGateResult:
    """Run declarative property tests. tests is a list of
    {name, predicate, expected} dicts. We don't evaluate Python here;
    we rely on test results having been pre-computed by an external
    harness OR fall back to recording {passed=total} for an empty
    test list (vacuously true).

    A case counts as passed only when its ``"passed"`` value is the
    literal ``True`` (see ``_case_passed``); anything else is a
    named failure, so a harness emitting ``passed: "false"`` can
    never yield ``pass_all_gates``.
    """
    return _count_cases(tests)


def run_regression_tests(c: SolverCandidate,
                                pinned_cases: list[dict] | None = None
                                ) -> CountedGateResult:
    """Same shape as property tests; tracks regressions on previously-
    accepted cases. Same literal-``True`` fail-closed rule."""
    return _count_cases(pinned_cases)


def evaluate_shadow(c: SolverCandidate,
                        observations: int = 0,
                        concordance_ratio: float = 0.0
                        ) -> ShadowEvalResult:
    return ShadowEvalResult(
        observations=observations,
        concordance_ratio=max(0.0, min(1.0, concordance_ratio)),
    )


def decide_verdict(*,
                       syntactic: GateResult,
                       semantic: GateResult,
                       property_tests: CountedGateResult,
                       regression: CountedGateResult,
                       shadow: ShadowEvalResult,
                       min_shadow_observations: int = 50,
                       min_concordance: float = 0.85,
                       ) -> str:
    if not syntactic.passed:
        return "syntactic_invalid"
    if not semantic.passed:
        return "semantic_invalid"
    # Property tests are declarative invariants. A property failure
    # is a structural violation and must preempt regression / shadow
    # gates, which are historical / performance signals. (Codex
    # post-merge blocker 2026-05-09: failed property tests previously
    # produced pass_all_gates because this check was missing.)
    if (property_tests.total > 0
            and property_tests.passed < property_tests.total):
        return "property_test_failed"
    if regression.total > 0 and regression.passed < regression.total:
        return "regression_detected"
    if shadow.observations < min_shadow_observations:
        return "needs_more_shadow"
    if shadow.concordance_ratio < min_concordance:
        return "rejected_low_value"
    return "pass_all_gates"


def validate_candidate(c: SolverCandidate, *,
                            property_tests_input: list[dict] | None = None,
                            regression_input: list[dict] | None = None,
                            shadow_observations: int = 0,
                            shadow_concordance: float = 0.0,
                            produced_at_iso: str = "",
                            execution_backend: str = "cpu",
                            ) -> SolverValidationReport:
    syn = syntactic_validate(c)
    sem = semantic_validate(c)
    prop = run_property_tests(c, property_tests_input)
    regr = run_regression_tests(c, regression_input)
    shadow = evaluate_shadow(c, shadow_observations, shadow_concordance)
    verdict = decide_verdict(
        syntactic=syn, semantic=sem,
        property_tests=prop, regression=regr, shadow=shadow,
    )
    return SolverValidationReport(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        candidate_id=c.candidate_id,
        syntactic=syn, semantic=sem,
        property_tests=prop, regression=regr,
        shadow_evaluation=shadow, verdict=verdict,
        produced_at_iso=produced_at_iso,
        execution_backend=execution_backend,
    )

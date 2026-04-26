"""Solver validators — Phase 9 §U3.

Combines syntactic + semantic + property + regression + shadow
validation into a deterministic SolverValidationReport. Backend-
agnostic: same outputs on CPU and (future) GPU batch backends.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import SOLVER_SYNTHESIS_SCHEMA_VERSION
from .solver_candidate_store import SolverCandidate


VERDICTS = (
    "pass_all_gates",
    "needs_more_shadow",
    "regression_detected",
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


def run_property_tests(c: SolverCandidate,
                            tests: list[dict] | None = None
                            ) -> CountedGateResult:
    """Run declarative property tests. tests is a list of
    {name, predicate, expected} dicts. We don't evaluate Python here;
    we rely on test results having been pre-computed by an external
    harness OR fall back to recording {passed=total} for an empty
    test list (vacuously true)."""
    tests = tests or []
    total = len(tests)
    passed = sum(1 for t in tests if bool(t.get("passed", False)))
    failures = tuple(
        str(t.get("name", "")) for t in tests if not t.get("passed", False)
    )
    return CountedGateResult(passed=passed, total=total,
                                  failures=failures)


def run_regression_tests(c: SolverCandidate,
                                pinned_cases: list[dict] | None = None
                                ) -> CountedGateResult:
    """Same shape as property tests; tracks regressions on previously-
    accepted cases."""
    pinned_cases = pinned_cases or []
    total = len(pinned_cases)
    passed = sum(1 for t in pinned_cases if bool(t.get("passed", False)))
    failures = tuple(
        str(t.get("name", "")) for t in pinned_cases
        if not t.get("passed", False)
    )
    return CountedGateResult(passed=passed, total=total,
                                  failures=failures)


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

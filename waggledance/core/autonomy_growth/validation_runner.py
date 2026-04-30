# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Validation runner for the low-risk autogrowth lane.

A *validation case* is a pair ``(inputs, expected_output)``. The
runner compiles the SolverSpec, executes it once per case, and
compares the executor's output to the expected output. Floats are
compared with a configurable absolute tolerance (default ``1e-9``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Sequence

from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec
from waggledance.core.solver_synthesis.deterministic_solver_compiler import (
    compile_spec,
)

from .solver_executor import ExecutorError, execute_artifact


ValidationCase = Mapping[str, Any]  # {"inputs": {...}, "expected": ...}


@dataclass(frozen=True)
class ValidationOutcome:
    case_count: int
    pass_count: int
    fail_count: int
    failures: List[Mapping[str, Any]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.case_count == 0:
            return 0.0
        return self.pass_count / self.case_count

    @property
    def all_passed(self) -> bool:
        return self.case_count > 0 and self.fail_count == 0


def _equal(actual: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= tolerance
    return actual == expected


def run_validation(
    spec: SolverSpec,
    cases: Sequence[ValidationCase],
    *,
    tolerance: float = 1e-9,
) -> ValidationOutcome:
    """Run validation cases against the compiled artifact.

    The compile step is itself part of the validation: a spec that
    fails to compile is treated as zero passes (caller decides what to
    do with that — typically reject the candidate).
    """

    try:
        compiled = compile_spec(spec)
    except Exception as exc:  # noqa: BLE001 — we map to structured failure
        return ValidationOutcome(
            case_count=len(cases),
            pass_count=0,
            fail_count=len(cases),
            failures=[
                {
                    "case_index": -1,
                    "kind": "compile_error",
                    "error": repr(exc),
                }
            ],
        )

    artifact = compiled.artifact
    fails: List[Mapping[str, Any]] = []
    pass_count = 0
    for i, case in enumerate(cases):
        if "inputs" not in case or "expected" not in case:
            fails.append({
                "case_index": i,
                "kind": "malformed_case",
                "error": "case missing 'inputs' or 'expected'",
            })
            continue
        try:
            actual = execute_artifact(artifact, case["inputs"])
        except ExecutorError as exc:
            fails.append({
                "case_index": i,
                "kind": "executor_error",
                "error": str(exc),
            })
            continue
        if _equal(actual, case["expected"], tolerance):
            pass_count += 1
        else:
            fails.append({
                "case_index": i,
                "kind": "mismatch",
                "actual": actual,
                "expected": case["expected"],
            })
    return ValidationOutcome(
        case_count=len(cases),
        pass_count=pass_count,
        fail_count=len(cases) - pass_count,
        failures=fails,
    )


__all__ = ["ValidationCase", "ValidationOutcome", "run_validation"]

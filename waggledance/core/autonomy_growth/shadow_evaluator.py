# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Shadow evaluator for the low-risk autogrowth lane.

The shadow evaluator runs the candidate's compiled artifact against a
batch of inputs and compares the result to an *oracle* — an
independent reference computation supplied by the caller. The oracle
captures the family's intended semantics in a form that does not go
through the deterministic compiler, so an agreement count above the
policy minimum (default 100%) gives independent evidence the artifact
behaves correctly.

For the six low-risk families the natural oracle is the family's
mathematical formula re-implemented in plain Python. The caller can
also pass a "byte-identity" oracle — re-execute the same artifact
twice and require equal output — which is what
:func:`byte_identity_oracle` provides as a default last-resort check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Mapping, Sequence

from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec
from waggledance.core.solver_synthesis.deterministic_solver_compiler import (
    compile_spec,
)

from .solver_executor import ExecutorError, execute_artifact


ShadowSample = Mapping[str, Any]  # the input record only

OracleFn = Callable[[Mapping[str, Any], Mapping[str, Any]], Any]
"""Oracle signature: (inputs, artifact) -> expected_output."""


@dataclass(frozen=True)
class ShadowOutcome:
    sample_count: int
    agree_count: int
    disagree_count: int
    oracle_kind: str
    disagreements: List[Mapping[str, Any]] = field(default_factory=list)

    @property
    def agreement_rate(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.agree_count / self.sample_count


def byte_identity_oracle(
    inputs: Mapping[str, Any], artifact: Mapping[str, Any]
) -> Any:
    """Fallback oracle: re-execute the artifact.

    Useful as a non-determinism canary, but not a substitute for an
    independent reference. The auto-promotion engine refuses to use
    this oracle on its own.
    """

    return execute_artifact(artifact, inputs)


def _equal(a: Any, b: Any, tolerance: float) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tolerance
    return a == b


def run_shadow_evaluation(
    spec: SolverSpec,
    samples: Sequence[ShadowSample],
    oracle: OracleFn,
    *,
    oracle_kind: str = "external_reference",
    tolerance: float = 1e-9,
) -> ShadowOutcome:
    """Evaluate the candidate against an independent oracle."""

    try:
        compiled = compile_spec(spec)
    except Exception as exc:  # noqa: BLE001
        return ShadowOutcome(
            sample_count=len(samples),
            agree_count=0,
            disagree_count=len(samples),
            oracle_kind=oracle_kind,
            disagreements=[{
                "kind": "compile_error",
                "error": repr(exc),
            }],
        )
    artifact = compiled.artifact
    disagreements: List[Mapping[str, Any]] = []
    agree = 0
    for i, sample in enumerate(samples):
        try:
            actual = execute_artifact(artifact, sample)
        except ExecutorError as exc:
            disagreements.append({
                "sample_index": i,
                "kind": "executor_error",
                "error": str(exc),
            })
            continue
        try:
            expected = oracle(sample, artifact)
        except Exception as exc:  # noqa: BLE001
            disagreements.append({
                "sample_index": i,
                "kind": "oracle_error",
                "error": repr(exc),
            })
            continue
        if _equal(actual, expected, tolerance):
            agree += 1
        else:
            disagreements.append({
                "sample_index": i,
                "kind": "mismatch",
                "actual": actual,
                "expected": expected,
            })
    return ShadowOutcome(
        sample_count=len(samples),
        agree_count=agree,
        disagree_count=len(samples) - agree,
        oracle_kind=oracle_kind,
        disagreements=disagreements,
    )


__all__ = [
    "ShadowSample",
    "ShadowOutcome",
    "OracleFn",
    "byte_identity_oracle",
    "run_shadow_evaluation",
]

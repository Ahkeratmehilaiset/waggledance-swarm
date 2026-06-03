# SPDX-License-Identifier: BUSL-1.1
"""Counterfactual replay (Track T1 / V12 A3) — pure, decision-independent core.

Computes a counterfactual DELTA: run the candidate and the incumbent solver
over the SAME shadow samples and diff their behaviour ("what would change if we
promoted the candidate instead of keeping the incumbent?"). This is
**observability**, not a gate — the auto-promotion decision must never depend on
the delta. The engine wiring (slice 2) emits the delta to the promotion receipt
fail-open (compute failure => promotion proceeds + counterfactual_status=failed),
while the A3 truth label is fail-closed and re-derived from the delta fields.

This module is pure and read-only: it copies the samples (never consumes a
caller iterator the gate shares), runs deterministically, and raises
``CounterfactualReplayError`` on any ill-formed input (the caller decides how to
handle it — for promotion that means fail-open + status=failed). Tools-free.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec
from waggledance.core.solver_synthesis.deterministic_solver_compiler import (
    compile_spec,
)

from .shadow_evaluator import OracleFn
from .solver_executor import ExecutorError, execute_artifact

COUNTERFACTUAL_DELTA_SCHEMA = "magma.counterfactual_delta.v0"

# A3 truth labels — re-derived from delta fields by derive_a3_label, never set
# by hand (RCO truth-boundary: auditor recomputes the label from the receipt).
A3_LABEL_RUNTIME_MEASURED = "RUNTIME_MEASURED"
A3_LABEL_MEASURED_LOCAL_PARTIAL = "MEASURED_LOCAL_PARTIAL"
A3_LABEL_INSUFFICIENT = "INSUFFICIENT"
A3_LABEL_NONDETERMINISTIC_ORACLE = "NONDETERMINISTIC_ORACLE"

# Minimum per-arm sample count to claim RUNTIME_MEASURED (below -> PARTIAL).
DEFAULT_A3_MIN_SAMPLES = 20

COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA = (
    "magma.counterfactual_observability_status.v0"
)
COUNTERFACTUAL_OBSERVABILITY_STATES = (
    "unavailable",
    "skipped",
    "failed",
    "insufficient",
    "measured_local_partial",
    "nondeterministic_oracle",
    "runtime_measured",
)

_A3_LABEL_TO_OBSERVABILITY_STATE = {
    A3_LABEL_INSUFFICIENT: "insufficient",
    A3_LABEL_MEASURED_LOCAL_PARTIAL: "measured_local_partial",
    A3_LABEL_NONDETERMINISTIC_ORACLE: "nondeterministic_oracle",
    A3_LABEL_RUNTIME_MEASURED: "runtime_measured",
}


class CounterfactualReplayError(RuntimeError):
    """Raised when the counterfactual replay cannot run on the given inputs.

    The promotion caller treats this fail-OPEN (promotion proceeds) but records
    counterfactual_status=failed and does NOT claim a RUNTIME_MEASURED label.
    """


def _run_arm(
    artifact: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    oracle: OracleFn,
) -> dict[str, Any]:
    """Run one arm (a compiled artifact) over the samples, read-only.

    Each result is ``{inputs, output, error, oracle_agree}``. Execution errors
    are captured per-sample (not raised) so both arms cover the full sample set;
    a structural problem (bad samples) is raised by the caller instead.
    """
    results: list[dict[str, Any]] = []
    for sample in samples:
        record: dict[str, Any] = {"inputs": dict(sample)}
        try:
            record["output"] = execute_artifact(artifact, sample)
            record["error"] = None
        except ExecutorError:
            record["output"] = None
            record["error"] = "executor_error"
        try:
            expected = oracle(sample, artifact)
            record["oracle_agree"] = (
                record["error"] is None and expected == record["output"]
            )
        except Exception:  # noqa: BLE001 - oracle is caller-supplied
            record["oracle_agree"] = None
        results.append(record)
    return {"results": results}


def _arm_digest(arm: Mapping[str, Any]) -> str:
    return sha256_digest(
        {
            "results": [
                {"inputs": r["inputs"], "output": r["output"], "error": r["error"]}
                for r in arm["results"]
            ]
        }
    )


def compute_counterfactual_delta(
    *,
    shadow_samples: Sequence[Mapping[str, Any]],
    candidate: SolverSpec,
    incumbent: SolverSpec,
    oracle: OracleFn,
    oracle_kind: str = "external_reference",
) -> dict[str, Any]:
    """Run candidate vs incumbent over the same samples; return a re-derivable delta.

    Pure + read-only + deterministic. Raises CounterfactualReplayError on
    ill-formed inputs or compile failure. The returned delta carries per-arm
    results (not just an aggregate), identical sample_set_digest for both arms
    (proof both ran the same inputs), a determinism flag (candidate arm re-run
    twice), and a canonical_digest over all of it.
    """
    if not isinstance(shadow_samples, Sequence) or isinstance(shadow_samples, (str, bytes)):
        raise CounterfactualReplayError("shadow_samples must be a non-string sequence")
    samples = [dict(s) for s in shadow_samples if isinstance(s, Mapping)]
    if len(samples) != len(list(shadow_samples)):
        raise CounterfactualReplayError("every shadow sample must be a mapping")
    if not isinstance(candidate, SolverSpec) or not isinstance(incumbent, SolverSpec):
        raise CounterfactualReplayError("candidate and incumbent must be SolverSpec")

    try:
        cand = compile_spec(candidate)
        inc = compile_spec(incumbent)
    except Exception as exc:  # noqa: BLE001
        raise CounterfactualReplayError(
            f"spec compile failed: {exc.__class__.__name__}"
        ) from exc

    sample_set_digest = sha256_digest({"samples": samples})
    cand_arm = _run_arm(cand.artifact, samples, oracle)
    inc_arm = _run_arm(inc.artifact, samples, oracle)
    # Determinism guard (mirrors I4 compile-twice): re-run the candidate arm and
    # require an identical digest, else the label degrades to NONDETERMINISTIC.
    deterministic = _arm_digest(cand_arm) == _arm_digest(_run_arm(cand.artifact, samples, oracle))

    divergences: list[dict[str, Any]] = []
    for index, (rc, ri) in enumerate(zip(cand_arm["results"], inc_arm["results"])):
        if rc["output"] != ri["output"] or rc["error"] != ri["error"]:
            divergences.append(
                {
                    "index": index,
                    "inputs": rc["inputs"],
                    "candidate_output": rc["output"],
                    "incumbent_output": ri["output"],
                }
            )

    core = {
        "schema_version": COUNTERFACTUAL_DELTA_SCHEMA,
        "candidate_hash": cand.artifact_id,
        "incumbent_hash": inc.artifact_id,
        "sample_count": len(samples),
        "candidate_sample_set_digest": sample_set_digest,
        "incumbent_sample_set_digest": sample_set_digest,
        "oracle_kind": oracle_kind,
        "deterministic": deterministic,
        "divergence_count": len(divergences),
        # Explicit no-delta (RCO: never silently report a spurious zero delta):
        # true when the two solvers are the same artifact or behave identically.
        "no_delta": cand.artifact_id == inc.artifact_id or not divergences,
        "per_arm": {"candidate": cand_arm, "incumbent": inc_arm},
        "divergences": divergences,
    }
    return {**core, "canonical_digest": sha256_digest(core)}


def derive_a3_label(
    delta: Any,
    *,
    min_samples: int = DEFAULT_A3_MIN_SAMPLES,
) -> str:
    """Re-derive the A3 truth label from delta fields (never trust a stored label).

    RUNTIME_MEASURED only if: sample_count >= min_samples AND both arms'
    sample_set_digest match (same inputs) AND the run was deterministic.
    Otherwise the lower, honest label.
    """
    if not isinstance(delta, Mapping):
        return A3_LABEL_INSUFFICIENT
    n = delta.get("sample_count")
    if not isinstance(n, int) or isinstance(n, bool):
        return A3_LABEL_INSUFFICIENT
    if not _same_sample_set(delta):
        return A3_LABEL_INSUFFICIENT
    if delta.get("deterministic") is not True:
        return A3_LABEL_NONDETERMINISTIC_ORACLE
    if n < min_samples:
        return A3_LABEL_MEASURED_LOCAL_PARTIAL
    return A3_LABEL_RUNTIME_MEASURED


def summarize_counterfactual_observability(
    snapshot: Any,
    *,
    min_samples: int = DEFAULT_A3_MIN_SAMPLES,
) -> dict[str, Any]:
    """Return a privacy-safe, read-only observability status.

    ``snapshot`` may be either a raw ``compute_counterfactual_delta`` mapping or
    the sanitized promotion counterfactual summary stored by the autogrowth
    engine. The returned mapping intentionally contains no raw per-arm rows,
    sample inputs, outputs, divergences, solver hashes, or digest strings.
    """
    if not isinstance(snapshot, Mapping):
        return _counterfactual_observability_status(
            source_available=False,
            compute_status="unavailable",
            status="unavailable",
            a3_label=A3_LABEL_INSUFFICIENT,
        )

    if snapshot.get("schema_version") == COUNTERFACTUAL_DELTA_SCHEMA:
        a3_label = derive_a3_label(snapshot, min_samples=min_samples)
        return _counterfactual_observability_status(
            source_available=True,
            compute_status="computed",
            status=_state_for_a3_label(a3_label),
            a3_label=a3_label,
            sample_count=_nonnegative_int(snapshot.get("sample_count")),
            divergence_count=_nonnegative_int(snapshot.get("divergence_count")),
            same_sample_set=_same_sample_set(snapshot),
            deterministic=snapshot.get("deterministic") is True,
            no_delta=snapshot.get("no_delta") is True,
            delta_digest_present=bool(snapshot.get("canonical_digest")),
        )

    compute_status = str(snapshot.get("status") or "unknown")
    a3_label = _known_a3_label(snapshot.get("a3_label"))
    if compute_status == "failed":
        status = "failed"
    elif compute_status == "skipped":
        status = "skipped"
    else:
        status = _state_for_a3_label(a3_label)

    return _counterfactual_observability_status(
        source_available=True,
        compute_status=compute_status,
        status=status,
        a3_label=a3_label,
        sample_count=_nonnegative_int(snapshot.get("sample_count")),
        divergence_count=_nonnegative_int(snapshot.get("divergence_count")),
        same_sample_set=snapshot.get("same_sample_set") is True,
        deterministic=snapshot.get("deterministic") is True,
        no_delta=snapshot.get("no_delta") is True,
        delta_digest_present=bool(snapshot.get("delta_digest")),
    )


def _counterfactual_observability_status(
    *,
    source_available: bool,
    compute_status: str,
    status: str,
    a3_label: str,
    sample_count: int = 0,
    divergence_count: int = 0,
    same_sample_set: bool = False,
    deterministic: bool = False,
    no_delta: bool = False,
    delta_digest_present: bool = False,
) -> dict[str, Any]:
    if status not in COUNTERFACTUAL_OBSERVABILITY_STATES:
        status = "insufficient" if source_available else "unavailable"
    return {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": bool(source_available),
        "compute_status": compute_status,
        "status": status,
        "a3_label": a3_label,
        "sample_count": sample_count,
        "divergence_count": divergence_count,
        "same_sample_set": bool(same_sample_set),
        "deterministic": bool(deterministic),
        "no_delta": bool(no_delta),
        "delta_digest_present": bool(delta_digest_present),
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }


def _state_for_a3_label(label: str) -> str:
    return _A3_LABEL_TO_OBSERVABILITY_STATE.get(label, "insufficient")


def _known_a3_label(value: Any) -> str:
    if isinstance(value, str) and value in _A3_LABEL_TO_OBSERVABILITY_STATE:
        return value
    return A3_LABEL_INSUFFICIENT


def _same_sample_set(snapshot: Mapping[str, Any]) -> bool:
    candidate_digest = snapshot.get("candidate_sample_set_digest")
    incumbent_digest = snapshot.get("incumbent_sample_set_digest")
    return (
        isinstance(candidate_digest, str)
        and isinstance(incumbent_digest, str)
        and bool(candidate_digest.strip())
        and bool(incumbent_digest.strip())
        and candidate_digest == incumbent_digest
    )


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)

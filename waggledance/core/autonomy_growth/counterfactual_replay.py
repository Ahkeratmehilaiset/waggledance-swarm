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

import math
from typing import Any, Mapping, Sequence

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec
from waggledance.core.solver_synthesis.deterministic_solver_compiler import (
    compile_spec,
)

from .shadow_evaluator import OracleFn
from .solver_executor import ExecutorError, execute_artifact

COUNTERFACTUAL_DELTA_SCHEMA = "magma.counterfactual_delta.v0"

# Per-divergence oracle-agreement classification. A divergence is an
# *improvement* when the candidate agrees with the external oracle where the
# incumbent does not, a *regression* in the mirror case, and *neutral*
# otherwise (neither agrees, both agree, or the oracle was inconclusive for
# either arm). These are observability labels only — the auto-promotion
# decision must never depend on them (see module docstring); they are
# re-derivable from the per-arm ``oracle_agree`` fields the delta already
# carries, so an auditor can recompute every count from the receipt.
DIVERGENCE_IMPROVEMENT = "improvement"
DIVERGENCE_REGRESSION = "regression"
DIVERGENCE_NEUTRAL = "neutral"

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
COUNTERFACTUAL_OBSERVABILITY_DIRECTIONS = (
    "unknown",
    "not_applicable",
    "net_neutral",
    "net_improvement",
    "net_regression",
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


def _classify_divergence_oracle(
    candidate_agree: Any,
    incumbent_agree: Any,
) -> str:
    """Label one divergence by which arm agreed with the external oracle.

    ``improvement``: candidate agrees, incumbent does not (promoting fixes it).
    ``regression``:  incumbent agrees, candidate does not (promoting breaks it).
    ``neutral``:     both agree, neither agrees, or either agreement is unknown
                     (oracle raised / inconclusive). Treats only an explicit
                     ``True`` as agreement, so a missing/``None`` value never
                     counts as a silent pass.
    """
    cand_ok = candidate_agree is True
    inc_ok = incumbent_agree is True
    if cand_ok and not inc_ok:
        return DIVERGENCE_IMPROVEMENT
    if inc_ok and not cand_ok:
        return DIVERGENCE_REGRESSION
    return DIVERGENCE_NEUTRAL


def _arm_digest(arm: Mapping[str, Any]) -> str:
    return sha256_digest(
        {
            "results": [
                {"inputs": r["inputs"], "output": r["output"], "error": r["error"]}
                for r in arm["results"]
            ]
        }
    )


def _arm_oracle_agreements(arm: Mapping[str, Any]) -> int:
    """Count samples in one arm where the output agreed with the oracle.

    Only an explicit ``oracle_agree is True`` counts — a ``None`` (oracle
    raised) or ``False`` does not, so the absolute agreement rate never
    silently inflates.
    """
    return sum(1 for r in arm["results"] if r.get("oracle_agree") is True)


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
    twice), and a canonical_digest over all of it. Each divergence is tagged
    with both arms' oracle agreement and an ``oracle_classification``
    (improvement/regression/neutral), aggregated into improvement_count /
    regression_count / neutral_divergence_count (these always sum to
    divergence_count). It also reports each arm's ABSOLUTE oracle-agreement
    rate over all samples (candidate_oracle_agreement_rate /
    incumbent_oracle_agreement_rate) and the candidate's
    oracle_agreement_advantage. All of it is observability only — never a gate.
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
    improvement_count = 0
    regression_count = 0
    neutral_divergence_count = 0
    for index, (rc, ri) in enumerate(zip(cand_arm["results"], inc_arm["results"])):
        if rc["output"] != ri["output"] or rc["error"] != ri["error"]:
            classification = _classify_divergence_oracle(
                rc["oracle_agree"], ri["oracle_agree"]
            )
            if classification == DIVERGENCE_IMPROVEMENT:
                improvement_count += 1
            elif classification == DIVERGENCE_REGRESSION:
                regression_count += 1
            else:
                neutral_divergence_count += 1
            divergences.append(
                {
                    "index": index,
                    "inputs": rc["inputs"],
                    "candidate_output": rc["output"],
                    "incumbent_output": ri["output"],
                    "candidate_oracle_agree": rc["oracle_agree"],
                    "incumbent_oracle_agree": ri["oracle_agree"],
                    "oracle_classification": classification,
                }
            )

    sample_count = len(samples)
    candidate_oracle_agreements = _arm_oracle_agreements(cand_arm)
    incumbent_oracle_agreements = _arm_oracle_agreements(inc_arm)
    candidate_oracle_agreement_rate = (
        candidate_oracle_agreements / sample_count if sample_count else 0.0
    )
    incumbent_oracle_agreement_rate = (
        incumbent_oracle_agreements / sample_count if sample_count else 0.0
    )

    core = {
        "schema_version": COUNTERFACTUAL_DELTA_SCHEMA,
        "candidate_hash": cand.artifact_id,
        "incumbent_hash": inc.artifact_id,
        "sample_count": sample_count,
        "candidate_sample_set_digest": sample_set_digest,
        "incumbent_sample_set_digest": sample_set_digest,
        "oracle_kind": oracle_kind,
        "deterministic": deterministic,
        "divergence_count": len(divergences),
        # Oracle-agreement breakdown of the divergences (observability only,
        # never a promotion gate). The three counts always sum to
        # divergence_count and are re-derivable from per_arm oracle_agree.
        "improvement_count": improvement_count,
        "regression_count": regression_count,
        "neutral_divergence_count": neutral_divergence_count,
        # ABSOLUTE oracle-agreement of each arm over ALL samples (not just the
        # divergences): the candidate's and incumbent's standalone correctness
        # against the external oracle, plus the candidate's advantage. This is
        # the "is the candidate better in absolute terms" signal (re-derivable
        # from per_arm oracle_agree; observability only, never a gate).
        "candidate_oracle_agreements": candidate_oracle_agreements,
        "incumbent_oracle_agreements": incumbent_oracle_agreements,
        "candidate_oracle_agreement_rate": candidate_oracle_agreement_rate,
        "incumbent_oracle_agreement_rate": incumbent_oracle_agreement_rate,
        "oracle_agreement_advantage": (
            candidate_oracle_agreement_rate - incumbent_oracle_agreement_rate
        ),
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
        divergence_count = _optional_nonnegative_int(
            snapshot.get("divergence_count")
        )
        improvement_count = _optional_nonnegative_int(
            snapshot.get("improvement_count")
        )
        regression_count = _optional_nonnegative_int(
            snapshot.get("regression_count")
        )
        neutral_divergence_count = _optional_nonnegative_int(
            snapshot.get("neutral_divergence_count")
        )
        no_delta = snapshot.get("no_delta") is True
        return _counterfactual_observability_status(
            source_available=True,
            compute_status="computed",
            status=_state_for_a3_label(a3_label),
            a3_label=a3_label,
            sample_count=_nonnegative_int(snapshot.get("sample_count")),
            divergence_count=divergence_count or 0,
            improvement_count=improvement_count or 0,
            regression_count=regression_count or 0,
            neutral_divergence_count=neutral_divergence_count or 0,
            oracle_agreement_advantage=_finite_float(
                snapshot.get("oracle_agreement_advantage")
            ),
            net_oracle_agreement_direction=_net_oracle_agreement_direction(
                no_delta=no_delta,
                divergence_count=divergence_count,
                improvement_count=improvement_count,
                regression_count=regression_count,
                neutral_divergence_count=neutral_divergence_count,
            ),
            same_sample_set=_same_sample_set(snapshot),
            deterministic=snapshot.get("deterministic") is True,
            no_delta=no_delta,
            delta_digest_present=bool(snapshot.get("canonical_digest")),
            controls_present=snapshot.get("controls_present") is True,
            runtime_authority_granted=(
                snapshot.get("runtime_authority_granted") is True
            ),
            external_writes_applied=snapshot.get("external_writes_applied") is True,
            payload_fields_exported=snapshot.get("payload_fields_exported") is True,
        )

    compute_status = str(snapshot.get("status") or "unknown")
    a3_label = _known_a3_label(snapshot.get("a3_label"))
    if compute_status == "failed":
        status = "failed"
    elif compute_status == "skipped":
        status = "skipped"
    else:
        status = _state_for_a3_label(a3_label)

    divergence_count = _optional_nonnegative_int(snapshot.get("divergence_count"))
    improvement_count = _optional_nonnegative_int(
        snapshot.get("improvement_count")
    )
    regression_count = _optional_nonnegative_int(snapshot.get("regression_count"))
    neutral_divergence_count = _optional_nonnegative_int(
        snapshot.get("neutral_divergence_count")
    )
    no_delta = snapshot.get("no_delta") is True
    return _counterfactual_observability_status(
        source_available=True,
        compute_status=compute_status,
        status=status,
        a3_label=a3_label,
        sample_count=_nonnegative_int(snapshot.get("sample_count")),
        divergence_count=divergence_count or 0,
        improvement_count=improvement_count or 0,
        regression_count=regression_count or 0,
        neutral_divergence_count=neutral_divergence_count or 0,
        oracle_agreement_advantage=_finite_float(
            snapshot.get("oracle_agreement_advantage")
        ),
        net_oracle_agreement_direction=_net_oracle_agreement_direction(
            no_delta=no_delta,
            divergence_count=divergence_count,
            improvement_count=improvement_count,
            regression_count=regression_count,
            neutral_divergence_count=neutral_divergence_count,
        ),
        same_sample_set=snapshot.get("same_sample_set") is True,
        deterministic=snapshot.get("deterministic") is True,
        no_delta=no_delta,
        delta_digest_present=bool(snapshot.get("delta_digest")),
        controls_present=snapshot.get("controls_present") is True,
        runtime_authority_granted=snapshot.get("runtime_authority_granted") is True,
        external_writes_applied=snapshot.get("external_writes_applied") is True,
        payload_fields_exported=snapshot.get("payload_fields_exported") is True,
    )


def _counterfactual_observability_status(
    *,
    source_available: bool,
    compute_status: str,
    status: str,
    a3_label: str,
    sample_count: int = 0,
    divergence_count: int = 0,
    improvement_count: int = 0,
    regression_count: int = 0,
    neutral_divergence_count: int = 0,
    oracle_agreement_advantage: float = 0.0,
    net_oracle_agreement_direction: str = "unknown",
    same_sample_set: bool = False,
    deterministic: bool = False,
    no_delta: bool = False,
    delta_digest_present: bool = False,
    controls_present: bool = False,
    runtime_authority_granted: bool = False,
    external_writes_applied: bool = False,
    payload_fields_exported: bool = False,
) -> dict[str, Any]:
    if status not in COUNTERFACTUAL_OBSERVABILITY_STATES:
        status = "insufficient" if source_available else "unavailable"
    if net_oracle_agreement_direction not in COUNTERFACTUAL_OBSERVABILITY_DIRECTIONS:
        net_oracle_agreement_direction = "unknown"
    return {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": bool(source_available),
        "compute_status": compute_status,
        "status": status,
        "a3_label": a3_label,
        "sample_count": _nonnegative_int(sample_count),
        "divergence_count": _nonnegative_int(divergence_count),
        "improvement_count": _nonnegative_int(improvement_count),
        "regression_count": _nonnegative_int(regression_count),
        "neutral_divergence_count": _nonnegative_int(neutral_divergence_count),
        "oracle_agreement_advantage": _finite_float(oracle_agreement_advantage),
        "net_oracle_agreement_direction": net_oracle_agreement_direction,
        "same_sample_set": bool(same_sample_set),
        "deterministic": bool(deterministic),
        "no_delta": bool(no_delta),
        "delta_digest_present": bool(delta_digest_present),
        "controls_present": bool(controls_present),
        "runtime_authority_granted": bool(runtime_authority_granted),
        "external_writes_applied": bool(external_writes_applied),
        "payload_fields_exported": bool(payload_fields_exported),
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


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _finite_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    return numeric


def _net_oracle_agreement_direction(
    *,
    no_delta: bool,
    divergence_count: int | None,
    improvement_count: int | None,
    regression_count: int | None,
    neutral_divergence_count: int | None,
) -> str:
    if no_delta or divergence_count == 0:
        return "not_applicable"
    if (
        divergence_count is None
        or improvement_count is None
        or regression_count is None
        or neutral_divergence_count is None
    ):
        return "unknown"
    if (
        improvement_count + regression_count + neutral_divergence_count
        != divergence_count
    ):
        return "unknown"
    if improvement_count > regression_count:
        return "net_improvement"
    if regression_count > improvement_count:
        return "net_regression"
    return "net_neutral"

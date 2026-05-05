# SPDX-License-Identifier: BUSL-1.1
"""Phase 18B - Runtime gap miner (mainline).

Converts a sequence of runtime gap signals into structured GapCandidate
records, each with a verdict in the six-element GapVerdict enum.

The contract is fail-closed:

* A signal with family_kind not in the six-family low-risk allowlist
  and not "builder_handoff" is rejected as OUT_OF_FAMILY_REJECTED.
* A signal with risk_label="high_risk" is rejected as HIGH_RISK_REJECTED.
* A signal with family_kind="builder_handoff" is quarantined as
  BUILDER_HANDOFF_QUARANTINED with no_auto_promotion=true.
* A cluster below evidence/confidence thresholds is INSUFFICIENT_EVIDENCE.
* Duplicate candidate_ids inside one run are DUPLICATE_SUPPRESSED.
* Otherwise: ALLOWLISTED_SOLVER_SPEC and a deterministic solver spec
  is emitted by ``candidate_to_solver_spec``.

No provider call. No builder call. No cloud API. No model pull.
No Stage-2 flip. No HUMAN_APPROVAL collected. No allowlist widening.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Mapping, Sequence

from waggledance.core.autonomy_growth.gap_candidate import (
    GapCandidate,
    GapMiningResult,
    GapVerdict,
)


ALLOWED_FAMILIES: tuple[str, ...] = (
    "scalar_unit_conversion",
    "lookup_table",
    "threshold_rule",
    "interval_bucket_classifier",
    "linear_arithmetic",
    "bounded_interpolation",
)

# Risk labels recognized by the verdict pipeline.
LOW_RISK = "low_risk"
MEDIUM_RISK = "medium_risk"
HIGH_RISK = "high_risk"


@dataclass(frozen=True)
class GapMiningConfig:
    """Verdict thresholds and policy switches for ``mine_runtime_gaps``."""

    min_signals_for_candidate: int = 2
    min_confidence: float = 0.55
    high_risk_block: bool = True
    suppress_duplicates: bool = True
    enable_builder_handoff_quarantine: bool = True

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Deterministic candidate ID
# ---------------------------------------------------------------------------

def _canonical_json(value: Any) -> str:
    """Stable JSON serialization for SHA-256 input."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                       default=str, ensure_ascii=True)


def _candidate_id_for(family_kind: str,
                       feature_dict: Mapping[str, Any]) -> str:
    """Deterministic 16-hex-char SHA-256 prefix derived from family +
    canonical feature key. Same input → same id across runs."""
    payload = family_kind + "|" + _canonical_json(dict(feature_dict))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def _cluster_key(signal: Mapping[str, Any]) -> tuple[str, str, str]:
    """Two signals belong to the same cluster iff their family_kind +
    canonical feature_dict + cluster_window match.

    `cluster_window` lets callers split two waves of the same gap into
    two separate clusters (e.g., today's window vs yesterday's). Same
    family+features but different windows → two clusters that share
    the same candidate_id (since candidate_id derives from family +
    features only). The second cluster's verdict is then
    DUPLICATE_SUPPRESSED. Signals without a cluster_window default to
    the empty string, so legacy callers keep one-cluster-per-features
    behaviour.
    """
    fam = str(signal.get("family_kind", ""))
    feat = _canonical_json(signal.get("feature_dict", {}))
    window = str(signal.get("cluster_window", ""))
    return (fam, feat, window)


def _aggregate_cluster(signals: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate per-signal hints into a single cluster summary.

    Confidence aggregation: max of `confidence_hint` across signals
    (the strongest evidence wins; clusters of weak signals stay weak).
    Risk: any HIGH_RISK signal escalates the cluster to HIGH_RISK.
    Evidence refs: union of all signals' `evidence_ref`s.
    Signal ids: list of `signal_id` for traceability.
    """
    fam = str(signals[0].get("family_kind", ""))
    feat = dict(signals[0].get("feature_dict", {}))

    confidences = [float(s.get("confidence_hint", 0.0)) for s in signals
                    if s.get("confidence_hint") is not None]
    confidence = max(confidences) if confidences else 0.0

    risks = [str(s.get("risk_label", LOW_RISK)) for s in signals]
    if HIGH_RISK in risks:
        risk = HIGH_RISK
    elif MEDIUM_RISK in risks:
        risk = MEDIUM_RISK
    else:
        risk = LOW_RISK

    evidence_refs: list[str] = []
    seen_refs: set[str] = set()
    for s in signals:
        ref = s.get("evidence_ref")
        if ref and ref not in seen_refs:
            evidence_refs.append(str(ref))
            seen_refs.add(str(ref))

    signal_ids = [str(s.get("signal_id", "")) for s in signals
                   if s.get("signal_id")]

    return {
        "family_kind": fam,
        "feature_dict": feat,
        "confidence": confidence,
        "risk_label": risk,
        "evidence_refs": tuple(evidence_refs),
        "signal_count": len(signals),
        "signal_ids": signal_ids,
        "raw_queries": [s.get("raw_query") for s in signals
                          if s.get("raw_query") is not None],
        "miss_reasons": list({str(s.get("miss_reason", ""))
                               for s in signals
                               if s.get("miss_reason")}),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def mine_runtime_gaps(
    signals: Sequence[Mapping[str, Any]],
    *,
    config: GapMiningConfig | None = None,
) -> GapMiningResult:
    """Mine runtime gap signals into verdicted GapCandidate records.

    Determinism: input order is preserved by stable cluster ordering
    (first-seen cluster comes first). Each candidate_id is a SHA-256
    prefix; same input → same ids → same output.

    Fail-closed: any signal that does not pass the verdict pipeline
    is recorded as a candidate with a non-spec verdict, NOT silently
    dropped.
    """
    cfg = config or GapMiningConfig()

    # Group by cluster_key, preserving first-seen ordering.
    clusters: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    cluster_order: list[tuple[str, str]] = []
    for sig in signals:
        key = _cluster_key(sig)
        if key not in clusters:
            clusters[key] = []
            cluster_order.append(key)
        clusters[key].append(sig)

    seen_ids: set[str] = set()
    candidates: list[GapCandidate] = []
    counters: dict[str, int] = {v.value: 0 for v in GapVerdict}

    for key in cluster_order:
        cluster_signals = clusters[key]
        agg = _aggregate_cluster(cluster_signals)
        fam = agg["family_kind"]
        candidate_id = _candidate_id_for(fam, agg["feature_dict"])

        # Provenance is identical for every candidate so downstream
        # tools always know where it came from.
        provenance = {
            "source": "phase18b_gap_mining",
            "signal_count": agg["signal_count"],
            "signal_ids": agg["signal_ids"],
            "miss_reasons": agg["miss_reasons"],
            "config_snapshot": cfg.snapshot(),
        }

        # Verdict pipeline (priority order documented in the design doc).
        verdict, reason, handoff_payload = _decide_verdict(
            agg=agg, candidate_id=candidate_id, seen_ids=seen_ids,
            cfg=cfg,
        )

        cand = GapCandidate(
            candidate_id=candidate_id,
            family_kind=fam,
            feature_dict=agg["feature_dict"],
            evidence_refs=agg["evidence_refs"],
            confidence=agg["confidence"],
            risk_label=agg["risk_label"],
            provenance=provenance,
            signal_count=agg["signal_count"],
            verdict=verdict,
            rejection_reason=reason,
            builder_handoff_payload=handoff_payload,
        )
        candidates.append(cand)
        counters[verdict.value] += 1
        if verdict == GapVerdict.ALLOWLISTED_SOLVER_SPEC:
            seen_ids.add(candidate_id)

    counters["signals_total"] = len(signals)
    counters["candidates_total"] = len(candidates)

    return GapMiningResult(
        candidates=tuple(candidates),
        counters=counters,
        config_snapshot=cfg.snapshot(),
    )


def _decide_verdict(*,
                     agg: Mapping[str, Any],
                     candidate_id: str,
                     seen_ids: set[str],
                     cfg: GapMiningConfig,
                     ) -> tuple[GapVerdict, str | None, Mapping[str, Any] | None]:
    """Apply the verdict pipeline. Returns (verdict, reason, handoff)."""
    fam = agg["family_kind"]
    risk = agg["risk_label"]

    # 1. High-risk block.
    if cfg.high_risk_block and risk == HIGH_RISK:
        return (
            GapVerdict.HIGH_RISK_REJECTED,
            "risk_label=high_risk; six-family low-risk allowlist policy",
            None,
        )

    # 2. Out-of-family rejection (but builder_handoff is its own bucket).
    if fam not in ALLOWED_FAMILIES and fam != "builder_handoff":
        return (
            GapVerdict.OUT_OF_FAMILY_REJECTED,
            f"family_kind={fam!r} not in six-family allowlist",
            None,
        )

    # 3. Builder handoff quarantine.
    if fam == "builder_handoff":
        if not cfg.enable_builder_handoff_quarantine:
            return (
                GapVerdict.OUT_OF_FAMILY_REJECTED,
                "builder_handoff quarantine disabled by config",
                None,
            )
        handoff = build_quarantined_builder_handoff_for(
            candidate_id=candidate_id,
            agg=agg,
        )
        return (
            GapVerdict.BUILDER_HANDOFF_QUARANTINED,
            "out_of_six_family_allowlist_but_not_high_risk",
            handoff,
        )

    # 4. Insufficient evidence.
    if (agg["signal_count"] < cfg.min_signals_for_candidate
            or agg["confidence"] < cfg.min_confidence):
        return (
            GapVerdict.INSUFFICIENT_EVIDENCE,
            f"signal_count={agg['signal_count']} or confidence="
            f"{agg['confidence']:.3f} below thresholds",
            None,
        )

    # 5. Duplicate suppression.
    if cfg.suppress_duplicates and candidate_id in seen_ids:
        return (
            GapVerdict.DUPLICATE_SUPPRESSED,
            f"candidate_id={candidate_id} already emitted this run",
            None,
        )

    # 6. Otherwise the candidate is allowlisted.
    return (GapVerdict.ALLOWLISTED_SOLVER_SPEC, None, None)


def candidate_to_solver_spec(
    candidate: GapCandidate,
) -> dict[str, Any] | None:
    """Convert an ALLOWLISTED candidate into a deterministic solver
    spec. Returns None for any other verdict (fail-closed)."""
    if candidate.verdict != GapVerdict.ALLOWLISTED_SOLVER_SPEC:
        return None
    if candidate.family_kind not in ALLOWED_FAMILIES:
        # Defense in depth: a non-allowlisted family should never
        # have reached this verdict.
        return None
    return {
        "spec_id": candidate.candidate_id,
        "candidate_id": candidate.candidate_id,
        "family_kind": candidate.family_kind,
        "feature_dict": dict(candidate.feature_dict),
        "training_examples": _training_examples_for(candidate),
        "evidence_refs": list(candidate.evidence_refs),
        "confidence": candidate.confidence,
        "risk_label": candidate.risk_label,
        "promotion_allowed": True,
        "expected_artifact_type": "deterministic_low_risk_solver",
        "provenance": dict(candidate.provenance),
    }


def _training_examples_for(candidate: GapCandidate) -> list[dict[str, Any]]:
    """Return a small bundle of deterministic input/expected_output
    pairs derived from the candidate's feature_dict.

    The mainline expectation is that downstream solver bootstrap will
    use these to register a deterministic low-risk solver. They are
    intentionally minimal (1-3 pairs) and family-aware. No probabilistic
    sampling, no model call.
    """
    fam = candidate.family_kind
    fd = dict(candidate.feature_dict)
    if fam == "scalar_unit_conversion":
        return [{"inputs": {"value": 1.0}, "expected_output_kind": "number",
                  "rule_hint": fd.get("rule")}]
    if fam == "lookup_table":
        return [{"inputs": {"key": fd.get("example_key")},
                  "expected_output_kind": "string",
                  "table_hint": fd.get("table_name")}]
    if fam == "threshold_rule":
        return [{"inputs": {"value": fd.get("example_value")},
                  "expected_output_kind": "boolean_or_label",
                  "threshold_hint": fd.get("threshold")}]
    if fam == "interval_bucket_classifier":
        return [{"inputs": {"value": fd.get("example_value")},
                  "expected_output_kind": "bucket_label",
                  "buckets_hint": fd.get("buckets")}]
    if fam == "linear_arithmetic":
        return [{"inputs": fd.get("example_inputs", {}),
                  "expected_output_kind": "number",
                  "operator_hint": fd.get("operator")}]
    if fam == "bounded_interpolation":
        return [{"inputs": {"x": fd.get("example_x")},
                  "expected_output_kind": "number",
                  "endpoints_hint": fd.get("endpoints")}]
    return []


def build_quarantined_builder_handoff_for(*,
                                            candidate_id: str,
                                            agg: Mapping[str, Any],
                                            ) -> dict[str, Any]:
    """Internal helper used by the verdict pipeline."""
    return {
        "handoff_id": candidate_id,
        "reason": "out_of_six_family_allowlist_but_not_high_risk",
        "quarantined_payload": {
            "feature_dict": dict(agg["feature_dict"]),
            "raw_queries": list(agg.get("raw_queries", [])),
            "miss_reasons": list(agg.get("miss_reasons", [])),
            "evidence_refs": list(agg.get("evidence_refs", ())),
            "signal_ids": list(agg.get("signal_ids", [])),
        },
        "no_auto_promotion": True,
        "no_provider_call": True,
        "no_builder_call_in_proof": True,
        "no_cloud_api": True,
        "promotion_allowed": False,
        "next_step_for_operator": (
            "review the payload manually; if a low-risk family port is "
            "feasible, author the spec by hand and submit through the "
            "existing Phase 9 14-stage promotion ladder"
        ),
    }


def build_quarantined_builder_handoff(
    candidate: GapCandidate,
) -> dict[str, Any]:
    """Public API: build a builder-handoff payload for any candidate.

    Useful when callers want to inspect the canonical handoff shape
    independent of the verdict pipeline.
    """
    return build_quarantined_builder_handoff_for(
        candidate_id=candidate.candidate_id,
        agg={
            "feature_dict": dict(candidate.feature_dict),
            "raw_queries": [],
            "miss_reasons": [],
            "evidence_refs": list(candidate.evidence_refs),
            "signal_ids": list(
                candidate.provenance.get("signal_ids", [])
            ),
        },
    )

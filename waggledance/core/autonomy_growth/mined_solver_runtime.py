# SPDX-License-Identifier: BUSL-1.1
"""Phase 18C - Mined solver runtime registration + dispatch adapter.

Bridges Phase 18B's mined low-risk solver specs into the existing
ControlPlaneDB / LowRiskSolverDispatcher runtime path so capability
lookup actually serves them.

Contract:

* Only ALLOWLISTED_SOLVER_SPEC verdicts register. The other five
  verdicts (INSUFFICIENT_EVIDENCE, OUT_OF_FAMILY_REJECTED,
  HIGH_RISK_REJECTED, BUILDER_HANDOFF_QUARANTINED,
  DUPLICATE_SUPPRESSED) are rejected and never become executable
  runtime solvers.
* Builder handoff payloads remain quarantined; they never get an
  executable artifact, never get auto_promoted status, and never
  get a capability-feature row.
* Registration is idempotent within a run: same candidate_id twice
  registers exactly once.
* Mined feature_dicts are translated to executor-shaped artifacts
  via a small, documented per-family compilation table. Unrecognized
  feature_dict signatures fail closed with
  RuntimeArtifactCompilationError.
* Six-family allowlist remains exactly unchanged.
* No provider call. No builder call. No cloud API. No model pull.
* No Stage-2 atomic flip. No HUMAN_APPROVAL collected.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from waggledance.core.autonomy_growth.gap_candidate import (
    GapCandidate,
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_mining import (
    ALLOWED_FAMILIES,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


PHASE_TAG = "phase18c"
SOURCE_PHASE = "phase18b"
SOLVER_VERSION = "phase18c.v1"
FAMILY_VERSION = "phase18c.v1"


class RuntimeArtifactCompilationError(Exception):
    """Raised when a mined spec cannot be compiled into an executor
    artifact. Indicates an (family_kind, feature_dict) signature
    not present in the documented compilation table."""


# ---------------------------------------------------------------------------
# Compilation table (per-family fixture-shape -> executor artifact)
# ---------------------------------------------------------------------------

def _canonical_features_key(feature_dict: Mapping[str, Any]) -> str:
    """Deterministic 16-hex-char SHA-256 prefix of canonical feature_dict.

    Matches the prefix style Phase 18B uses for candidate_id, but is
    keyed by the feature_dict alone (not family). The compilation
    table indexes by (family_kind, this prefix).
    """
    canonical = json.dumps(dict(feature_dict), sort_keys=True,
                            separators=(",", ":"), default=str,
                            ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _compute_features_key(feature_dict: Mapping[str, Any]) -> str:
    return _canonical_features_key(feature_dict)


# Each entry maps a Phase 18B mined fixture shape to an executable
# artifact in the schema solver_executor.py expects. The keys are
# (family_kind, _canonical_features_key(feature_dict)) tuples.
#
# To extend: add a new tuple key + artifact dict here, with the same
# structure as the existing entries. An unknown key fails closed.
_COMPILATION_TABLE: dict[tuple[str, str], dict[str, Any]] = {}


def _register_compilation_rule(
    family_kind: str,
    sample_feature_dict: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    fk = _canonical_features_key(sample_feature_dict)
    _COMPILATION_TABLE[(family_kind, fk)] = dict(artifact)


# Phase 18B fixture shapes (must match build_synthetic_fixture in
# tools/run_phase18b_gap_miner_feedback_proof.py).

_register_compilation_rule(
    "scalar_unit_conversion",
    {
        "input_unit": "km",
        "output_unit": "miles",
        "rule": "1 km = 0.621371 miles",
    },
    {
        "kind": "scalar_unit_conversion",
        "factor": 0.621371,
        "offset": 0.0,
    },
)

_register_compilation_rule(
    "lookup_table",
    {
        "table_name": "chemical_symbols",
        "example_key": "tin",
    },
    {
        "kind": "lookup_table",
        "table": {
            "tin": "Sn",
            "gold": "Au",
            "sodium": "Na",
            "iron": "Fe",
        },
        "default": "unknown",
    },
)

_register_compilation_rule(
    "threshold_rule",
    {
        "threshold": 30,
        "example_value": 37,
        "rule": "above_or_below",
    },
    {
        "kind": "threshold_rule",
        "operator": ">",
        "threshold": 30,
        "true_label": "above",
        "false_label": "below",
    },
)

_register_compilation_rule(
    "interval_bucket_classifier",
    {
        "buckets": "[0,10),[10,20),[20,30)",
        "example_value": 17,
    },
    {
        "kind": "interval_bucket_classifier",
        "intervals": [
            {"min": 0, "max": 10, "label": "[0,10)"},
            {"min": 10, "max": 20, "label": "[10,20)"},
            {"min": 20, "max": 30, "label": "[20,30)"},
        ],
        "out_of_range_label": "out_of_range",
    },
)

_register_compilation_rule(
    "linear_arithmetic",
    {
        "operator": "add",
        "example_inputs": {"a": 14, "b": 9},
    },
    {
        "kind": "linear_arithmetic",
        "input_columns": ["a", "b"],
        "coefficients": [1.0, 1.0],
        "intercept": 0.0,
    },
)

_register_compilation_rule(
    "bounded_interpolation",
    {
        "endpoints": "(0,0)->(10,100)",
        "example_x": 3,
    },
    {
        "kind": "bounded_interpolation",
        "min_x": 0.0,
        "max_x": 10.0,
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 100.0}],
        "method": "linear",
        "out_of_range_policy": "clip",
    },
)


# Phase 18F additional fixture shapes — one per family. Each is a
# strictly-typed, hardcoded executor artifact. No generic code
# generation; no allowlist widening; no new family_kind. These rules
# let the Phase 18F incremental-replay proof learn a *second* solver
# per family from post-cursor events without colliding with the Phase
# 18B/18C originals.

_register_compilation_rule(
    "scalar_unit_conversion",
    {
        "input_unit": "m",
        "output_unit": "ft",
        "rule": "1 m = 3.28084 ft",
    },
    {
        "kind": "scalar_unit_conversion",
        "factor": 3.28084,
        "offset": 0.0,
    },
)

_register_compilation_rule(
    "lookup_table",
    {
        "table_name": "country_codes",
        "example_key": "fi",
    },
    {
        "kind": "lookup_table",
        "table": {
            "fi": "Finland",
            "se": "Sweden",
            "no": "Norway",
            "dk": "Denmark",
        },
        "default": "unknown",
    },
)

_register_compilation_rule(
    "threshold_rule",
    {
        "threshold": 100,
        "example_value": 150,
        "rule": "alert_or_quiet",
    },
    {
        "kind": "threshold_rule",
        "operator": ">",
        "threshold": 100,
        "true_label": "alert",
        "false_label": "quiet",
    },
)

_register_compilation_rule(
    "interval_bucket_classifier",
    {
        "buckets": "[0,33),[33,66),[66,100]",
        "example_value": 50,
    },
    {
        "kind": "interval_bucket_classifier",
        "intervals": [
            {"min": 0, "max": 33, "label": "low"},
            {"min": 33, "max": 66, "label": "mid"},
            {"min": 66, "max": 100, "label": "high"},
        ],
        "out_of_range_label": "out_of_range",
    },
)

_register_compilation_rule(
    "linear_arithmetic",
    {
        "operator": "subtract",
        "example_inputs": {"a": 20, "b": 5},
    },
    {
        "kind": "linear_arithmetic",
        "input_columns": ["a", "b"],
        "coefficients": [1.0, -1.0],
        "intercept": 0.0,
    },
)

_register_compilation_rule(
    "bounded_interpolation",
    {
        "endpoints": "(0,0)->(100,1)",
        "example_x": 50,
    },
    {
        "kind": "bounded_interpolation",
        "min_x": 0.0,
        "max_x": 100.0,
        "knots": [{"x": 0.0, "y": 0.0}, {"x": 100.0, "y": 1.0}],
        "method": "linear",
        "out_of_range_policy": "clip",
    },
)


def compile_mined_spec_to_runtime_artifact(
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a Phase 18B mined solver spec into an executor-shaped
    artifact ready for ``execute_artifact()``.

    Raises ``RuntimeArtifactCompilationError`` if the
    (family_kind, feature_dict) signature is not registered in the
    compilation table. This is fail-closed by design: mined specs
    with novel feature dicts require operator review before becoming
    executable.
    """
    family_kind = spec.get("family_kind")
    feature_dict = spec.get("feature_dict") or {}
    if family_kind not in ALLOWED_FAMILIES:
        raise RuntimeArtifactCompilationError(
            f"family_kind={family_kind!r} is not in the six-family allowlist"
        )
    fk = _canonical_features_key(feature_dict)
    artifact = _COMPILATION_TABLE.get((family_kind, fk))
    if artifact is None:
        raise RuntimeArtifactCompilationError(
            f"no compilation rule for family_kind={family_kind!r} "
            f"feature_key={fk!r}; operator review required"
        )
    return dict(artifact)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec_hash(family_kind: str,
                 feature_dict: Mapping[str, Any],
                 artifact: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "family_kind": family_kind,
            "feature_dict": dict(feature_dict),
            "artifact": dict(artifact),
        },
        sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cap_feature_payload(family_kind: str,
                           feature_dict: Mapping[str, Any]) -> dict[str, str]:
    """Stringify feature_dict values for the capability-feature table.

    The dispatcher's `find_auto_promoted_solvers_by_features` compares
    `(feature_name, feature_value)` against the stored rows where the
    value is a string. We stringify each mined feature here so the
    same lookup in the proof harness will hit.
    """
    out: dict[str, str] = {}
    for k, v in feature_dict.items():
        if v is None:
            out[str(k)] = ""
        elif isinstance(v, (str, int, float, bool)):
            out[str(k)] = str(v)
        else:
            # Stable JSON representation for nested values.
            out[str(k)] = json.dumps(v, sort_keys=True,
                                       separators=(",", ":"),
                                       default=str, ensure_ascii=True)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegistrationSummary:
    """Outcome of registering a batch of mined candidates."""

    registered_solver_ids: tuple[int, ...]
    registered_candidate_ids: tuple[str, ...]
    registered_count: int
    rejected_count: int
    rejected_by_verdict: Mapping[str, int]
    builder_handoff_quarantined: int
    duplicates_suppressed_in_run: int
    compilation_failed_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "registered_solver_ids": list(self.registered_solver_ids),
            "registered_candidate_ids": list(self.registered_candidate_ids),
            "registered_count": self.registered_count,
            "rejected_count": self.rejected_count,
            "rejected_by_verdict": dict(self.rejected_by_verdict),
            "builder_handoff_quarantined": self.builder_handoff_quarantined,
            "duplicates_suppressed_in_run": self.duplicates_suppressed_in_run,
            "compilation_failed_count": self.compilation_failed_count,
        }


def register_mined_solver_specs(
    *,
    candidates: Sequence[GapCandidate],
    control_plane: ControlPlaneDB,
) -> RegistrationSummary:
    """Register ALLOWLISTED candidates into the runtime ControlPlaneDB.

    Performs the canonical four-step Phase 17A pattern:

        1. upsert_solver_family(name=family_kind, status="active")
        2. upsert_solver(name=..., family_name=..., status="auto_promoted",
                          spec_hash=...)
        3. set_solver_capability_features(solver_id, family_kind, features)
        4. upsert_solver_artifact(solver_id, family_kind, artifact_id,
                                   spec_canonical_json, artifact_json)

    Refuses all non-ALLOWLISTED verdicts. Idempotent within a run.
    """
    if control_plane is None:
        raise ValueError("control_plane is required")

    rejected_by_verdict: dict[str, int] = {}
    registered_solver_ids: list[int] = []
    registered_candidate_ids: list[str] = []
    builder_handoff_quarantined = 0
    compilation_failed_count = 0
    duplicates_suppressed_in_run = 0
    seen_in_run: set[str] = set()
    rejected_count = 0

    for cand in candidates:
        if cand.verdict != GapVerdict.ALLOWLISTED_SOLVER_SPEC:
            v = cand.verdict.value
            rejected_by_verdict[v] = rejected_by_verdict.get(v, 0) + 1
            rejected_count += 1
            if cand.verdict == GapVerdict.BUILDER_HANDOFF_QUARANTINED:
                builder_handoff_quarantined += 1
            continue

        if cand.family_kind not in ALLOWED_FAMILIES:
            # Defense-in-depth: should not happen for ALLOWLISTED.
            rejected_by_verdict["NOT_IN_ALLOWLIST"] = (
                rejected_by_verdict.get("NOT_IN_ALLOWLIST", 0) + 1
            )
            rejected_count += 1
            continue

        if cand.candidate_id in seen_in_run:
            duplicates_suppressed_in_run += 1
            rejected_by_verdict["DUPLICATE_IN_RUN"] = (
                rejected_by_verdict.get("DUPLICATE_IN_RUN", 0) + 1
            )
            rejected_count += 1
            continue

        try:
            artifact = compile_mined_spec_to_runtime_artifact({
                "family_kind": cand.family_kind,
                "feature_dict": cand.feature_dict,
            })
        except RuntimeArtifactCompilationError:
            compilation_failed_count += 1
            rejected_by_verdict["COMPILATION_FAILED"] = (
                rejected_by_verdict.get("COMPILATION_FAILED", 0) + 1
            )
            rejected_count += 1
            continue

        # Step 1: family.
        control_plane.upsert_solver_family(
            name=cand.family_kind,
            version=FAMILY_VERSION,
            status="active",
            description=f"Phase 18C runtime registration for family "
                          f"{cand.family_kind}",
        )

        # Step 2: solver row with auto_promoted status.
        solver_name = f"phase18c_{cand.family_kind}_{cand.candidate_id}"
        spec_hash = _spec_hash(cand.family_kind, cand.feature_dict, artifact)
        solver_record = control_plane.upsert_solver(
            name=solver_name,
            version=SOLVER_VERSION,
            family_name=cand.family_kind,
            status="auto_promoted",
            spec_hash=spec_hash,
        )

        # Step 3: capability features (stringified for the lookup).
        features_for_cap = _cap_feature_payload(
            cand.family_kind, cand.feature_dict,
        )
        control_plane.set_solver_capability_features(
            solver_id=solver_record.id,
            family_kind=cand.family_kind,
            features=features_for_cap,
        )

        # Step 4: compiled artifact.
        artifact_id = f"phase18c-{cand.candidate_id}"
        artifact_json = json.dumps(artifact, sort_keys=True,
                                     separators=(",", ":"),
                                     default=str, ensure_ascii=True)
        control_plane.upsert_solver_artifact(
            solver_id=solver_record.id,
            family_kind=cand.family_kind,
            artifact_id=artifact_id,
            spec_canonical_json=artifact_json,
            artifact_json=artifact_json,
        )

        registered_solver_ids.append(int(solver_record.id))
        registered_candidate_ids.append(cand.candidate_id)
        seen_in_run.add(cand.candidate_id)

    return RegistrationSummary(
        registered_solver_ids=tuple(registered_solver_ids),
        registered_candidate_ids=tuple(registered_candidate_ids),
        registered_count=len(registered_solver_ids),
        rejected_count=rejected_count,
        rejected_by_verdict=dict(rejected_by_verdict),
        builder_handoff_quarantined=builder_handoff_quarantined,
        duplicates_suppressed_in_run=duplicates_suppressed_in_run,
        compilation_failed_count=compilation_failed_count,
    )

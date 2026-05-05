# SPDX-License-Identifier: BUSL-1.1
"""Phase 18B — runtime gap miner + solver feedback loop tests.

Covers the core verdict pipeline (six allowlist verdicts), determinism
of candidate IDs, provenance presence, fail-closed rejection of
unknown family / high risk / insufficient evidence, duplicate
suppression, builder-handoff quarantine, solver-spec shape, and the
proof harness's release-gate contract. Also carries forward the
Phase 18A bundle validation as a regression gate.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from waggledance.core.autonomy_growth.gap_candidate import (
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_mining import (
    ALLOWED_FAMILIES,
    GapMiningConfig,
    build_quarantined_builder_handoff,
    candidate_to_solver_spec,
    mine_runtime_gaps,
)


# ---------------------------------------------------------------------------
# Allowlisted happy path
# ---------------------------------------------------------------------------

def _signal(*, signal_id: str, family_kind: str,
              feature_dict: dict, confidence: float = 0.78,
              risk_label: str = "low_risk",
              evidence_ref: str = "phase17b/test_track/missed_x",
              cluster_window: str | None = "W0") -> dict:
    s = {
        "signal_id": signal_id,
        "family_kind": family_kind,
        "feature_dict": feature_dict,
        "raw_query": "stub query",
        "miss_reason": "stub_miss_reason",
        "confidence_hint": confidence,
        "risk_label": risk_label,
        "evidence_ref": evidence_ref,
        "occurred_at_utc": "2026-05-05T14:00:00Z",
    }
    if cluster_window is not None:
        s["cluster_window"] = cluster_window
    return s


def test_mines_allowlisted_candidates_from_runtime_signals():
    """One signal cluster per six-family allowlist family produces six
    ALLOWLISTED_SOLVER_SPEC verdicts."""
    signals = []
    for i, fam in enumerate(ALLOWED_FAMILIES):
        for j in range(2):
            signals.append(_signal(
                signal_id=f"s_{i}_{j}",
                family_kind=fam,
                feature_dict={"k": fam},
            ))
    result = mine_runtime_gaps(signals)
    assert len(result.candidates) == 6
    assert all(c.verdict == GapVerdict.ALLOWLISTED_SOLVER_SPEC
                 for c in result.candidates)
    assert result.counters[GapVerdict.ALLOWLISTED_SOLVER_SPEC.value] == 6


def test_candidate_ids_are_deterministic():
    signals = [_signal(signal_id=f"s{i}", family_kind="threshold_rule",
                          feature_dict={"threshold": 30})
                for i in range(2)]
    r1 = mine_runtime_gaps(signals)
    r2 = mine_runtime_gaps(signals)
    assert r1.candidates[0].candidate_id == r2.candidates[0].candidate_id


def test_every_candidate_has_provenance():
    signals = [_signal(signal_id=f"s{i}", family_kind="lookup_table",
                          feature_dict={"table_name": "x"})
                for i in range(2)]
    result = mine_runtime_gaps(signals)
    for c in result.candidates:
        assert c.provenance.get("source") == "phase18b_gap_mining"
        assert c.provenance.get("signal_count") >= 1
        assert "signal_ids" in c.provenance
        assert "config_snapshot" in c.provenance


# ---------------------------------------------------------------------------
# Fail-closed verdicts
# ---------------------------------------------------------------------------

def test_rejects_unknown_family():
    signals = [_signal(signal_id="s_unknown",
                          family_kind="multi_step_reasoning",
                          feature_dict={"task": "chain_of_thought"})]
    result = mine_runtime_gaps(signals)
    assert result.candidates[0].verdict == GapVerdict.OUT_OF_FAMILY_REJECTED
    assert result.counters[GapVerdict.OUT_OF_FAMILY_REJECTED.value] == 1


def test_rejects_high_risk_candidate():
    signals = [
        _signal(signal_id=f"s_hr_{i}",
                  family_kind="scalar_unit_conversion",
                  feature_dict={"input_unit": "rad", "output_unit": "deg"},
                  risk_label="high_risk")
        for i in range(2)
    ]
    result = mine_runtime_gaps(signals)
    assert result.candidates[0].verdict == GapVerdict.HIGH_RISK_REJECTED
    assert result.counters[GapVerdict.HIGH_RISK_REJECTED.value] == 1


def test_rejects_insufficient_evidence():
    """Single-signal cluster (signal_count < 2) is INSUFFICIENT_EVIDENCE."""
    signals = [_signal(signal_id="s_lonely",
                          family_kind="linear_arithmetic",
                          feature_dict={"operator": "div"},
                          confidence=0.40)]
    result = mine_runtime_gaps(signals)
    assert result.candidates[0].verdict == GapVerdict.INSUFFICIENT_EVIDENCE
    assert result.counters[GapVerdict.INSUFFICIENT_EVIDENCE.value] == 1


def test_suppresses_duplicates():
    """Two clusters with same family+features but different cluster_window
    → second is DUPLICATE_SUPPRESSED."""
    fd = {"input_unit": "km", "output_unit": "miles"}
    signals = [
        _signal(signal_id="s_w0_0", family_kind="scalar_unit_conversion",
                  feature_dict=fd, cluster_window="W0"),
        _signal(signal_id="s_w0_1", family_kind="scalar_unit_conversion",
                  feature_dict=fd, cluster_window="W0"),
        _signal(signal_id="s_w1_0", family_kind="scalar_unit_conversion",
                  feature_dict=fd, cluster_window="W1"),
        _signal(signal_id="s_w1_1", family_kind="scalar_unit_conversion",
                  feature_dict=fd, cluster_window="W1"),
    ]
    result = mine_runtime_gaps(signals)
    verdicts = [c.verdict for c in result.candidates]
    assert verdicts.count(GapVerdict.ALLOWLISTED_SOLVER_SPEC) == 1
    assert verdicts.count(GapVerdict.DUPLICATE_SUPPRESSED) == 1
    # Both candidates must share the same candidate_id since features match.
    assert result.candidates[0].candidate_id == result.candidates[1].candidate_id


def test_builder_handoff_is_quarantined_and_no_auto_promotion():
    signals = [
        _signal(signal_id=f"s_b_{i}", family_kind="builder_handoff",
                  feature_dict={"handoff_kind": "free_form"})
        for i in range(2)
    ]
    result = mine_runtime_gaps(signals)
    assert result.candidates[0].verdict == GapVerdict.BUILDER_HANDOFF_QUARANTINED
    payload = result.candidates[0].builder_handoff_payload
    assert payload is not None
    assert payload["no_auto_promotion"] is True
    assert payload["no_provider_call"] is True
    assert payload["no_builder_call_in_proof"] is True
    assert payload["no_cloud_api"] is True
    assert payload["promotion_allowed"] is False


# ---------------------------------------------------------------------------
# Solver spec contract
# ---------------------------------------------------------------------------

def test_candidate_to_solver_spec_requires_allowlist():
    signals = [_signal(signal_id="s_oof",
                          family_kind="multi_step_reasoning",
                          feature_dict={"task": "x"})]
    result = mine_runtime_gaps(signals)
    spec = candidate_to_solver_spec(result.candidates[0])
    assert spec is None


def test_candidate_to_solver_spec_shape():
    signals = [_signal(signal_id=f"s_{i}",
                          family_kind="scalar_unit_conversion",
                          feature_dict={"input_unit": "kg", "output_unit": "lb",
                                          "rule": "1 kg = 2.205 lb"})
                for i in range(2)]
    result = mine_runtime_gaps(signals)
    spec = candidate_to_solver_spec(result.candidates[0])
    assert spec is not None
    expected_keys = {
        "spec_id", "candidate_id", "family_kind", "feature_dict",
        "training_examples", "evidence_refs", "confidence",
        "risk_label", "promotion_allowed", "expected_artifact_type",
        "provenance",
    }
    assert expected_keys.issubset(spec.keys())
    assert spec["promotion_allowed"] is True
    assert spec["expected_artifact_type"] == "deterministic_low_risk_solver"
    assert spec["family_kind"] == "scalar_unit_conversion"
    assert isinstance(spec["training_examples"], list)
    assert spec["spec_id"] == spec["candidate_id"]


def test_build_quarantined_builder_handoff_public_api():
    """Public helper builds a canonical handoff regardless of verdict."""
    signals = [_signal(signal_id="s_x",
                          family_kind="multi_step_reasoning",
                          feature_dict={"task": "y"})]
    result = mine_runtime_gaps(signals)
    handoff = build_quarantined_builder_handoff(result.candidates[0])
    assert handoff["no_auto_promotion"] is True
    assert "quarantined_payload" in handoff
    assert handoff["promotion_allowed"] is False


# ---------------------------------------------------------------------------
# Proof harness
# ---------------------------------------------------------------------------

@pytest.fixture
def proof_doc(tmp_path: Path) -> dict:
    """Run the proof harness once into tmp; return the parsed JSON."""
    name = "run_phase18b_gap_miner_feedback_proof"
    if name in sys.modules:
        del sys.modules[name]
    harness = importlib.import_module(name)
    proof = harness.build_proof(out_dir=tmp_path)
    return proof


def test_proof_json_shape(proof_doc):
    expected_keys = {
        "phase", "benchmark_version", "started_utc", "finished_utc",
        "git_sha", "python_version", "platform",
        "is_synthetic_fixture", "fixture_size", "config_snapshot",
        "counters", "candidates", "solver_specs", "solver_specs_total",
        "promoted_or_registered_solver_total", "capability_lookup_status",
        "exact_api_blocker", "allowlist_unchanged",
        "provider_jobs_delta", "builder_jobs_delta",
        "no_stage2_flip", "no_human_approval",
        "no_model_pull_or_download", "no_cloud_api_calls",
        "no_live_builder_execution",
        "no_raw_intelligence_superiority_claim",
        "no_cross_vendor_ranking_claim",
        "signals_total", "candidates_total",
        "allowlisted_candidates_total", "insufficient_evidence_total",
        "out_of_family_rejected_total", "high_risk_rejected_total",
        "builder_handoff_quarantined_total", "duplicates_suppressed_total",
        "forbidden_claims_absent", "forbidden_substring_hits",
        "release_gates", "release_gate_pass",
    }
    missing = expected_keys - set(proof_doc.keys())
    assert not missing, f"missing keys: {missing}"
    assert proof_doc["phase"] == "phase18b_gap_miner_feedback"
    assert proof_doc["benchmark_version"] == "phase18b.v1"


def test_proof_release_gate_passes(proof_doc):
    assert proof_doc["release_gate_pass"] is True
    gates = proof_doc["release_gates"]
    assert all(gates.values()), f"failing gates: {gates}"


def test_provider_builder_delta_zero(proof_doc):
    assert proof_doc["provider_jobs_delta"] == 0
    assert proof_doc["builder_jobs_delta"] == 0


def test_allowlist_unchanged(proof_doc):
    """ALLOWED_FAMILIES tuple matches the canonical six-family allowlist."""
    assert proof_doc["allowlist_unchanged"] is True
    assert ALLOWED_FAMILIES == (
        "scalar_unit_conversion", "lookup_table", "threshold_rule",
        "interval_bucket_classifier", "linear_arithmetic",
        "bounded_interpolation",
    )


def test_no_stage2_no_human_approval_flags(proof_doc):
    assert proof_doc["no_stage2_flip"] is True
    assert proof_doc["no_human_approval"] is True


def test_proof_threshold_minimums(proof_doc):
    """Master prompt P4 minimums."""
    assert proof_doc["signals_total"] >= 30
    assert proof_doc["allowlisted_candidates_total"] >= 6
    assert proof_doc["solver_specs_total"] >= 6
    assert proof_doc["insufficient_evidence_total"] >= 3
    assert proof_doc["out_of_family_rejected_total"] >= 2
    assert proof_doc["high_risk_rejected_total"] >= 1
    assert proof_doc["builder_handoff_quarantined_total"] >= 1
    assert proof_doc["duplicates_suppressed_total"] >= 1


# ---------------------------------------------------------------------------
# Phase 18A bundle validation (carry-forward gate)
# ---------------------------------------------------------------------------

def test_phase18a_bundle_still_validates():
    """Phase 18A evidence bundle must continue to validate; if Phase 18B
    accidentally broke checksums, schemas, or release lineage, this
    test catches it."""
    name = "validate_phase18a_benchmark_bundle"
    if name in sys.modules:
        del sys.modules[name]
    validator = importlib.import_module(name)
    bundle_dir = (
        ROOT / "docs" / "runs"
        / "phase18a_benchmark_externalization_2026_05_05"
        / "export_bundle"
    )
    ok, errors = validator.validate_bundle(bundle_dir)
    assert ok, f"Phase 18A bundle no longer validates: {errors}"


# ---------------------------------------------------------------------------
# Forbidden vocabulary / docs scrub
# ---------------------------------------------------------------------------

def test_no_forbidden_docs_claims_in_phase18b_outputs(proof_doc):
    """The proof JSON's own forbidden_claims_absent flag must be true,
    and the rendered MD must not contain any forbidden substring."""
    assert proof_doc["forbidden_claims_absent"] is True
    assert proof_doc["forbidden_substring_hits"]["json_hits"] == []
    assert proof_doc["forbidden_substring_hits"]["md_hits"] == []

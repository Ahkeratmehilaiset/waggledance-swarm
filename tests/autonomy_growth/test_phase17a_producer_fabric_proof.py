# SPDX-License-Identifier: BUSL-1.1
"""Phase 17A — Producer Fabric Proof integration tests.

These tests run ``tools/run_phase17a_producer_fabric_proof.py`` against
a temp directory and assert the resulting ``producer_fabric_proof.json``
upholds the contract documented in
``docs/runs/phase17a_producer_fabric_scale_2026_05_04/implementation_plan.md``:

* All four phase8.5 producer modules (curiosity, self_model, dream, hive)
  run end-to-end without provider or builder calls.
* Their outputs are consumed by the existing main IR adapters
  (`from_curiosity`, `from_self_model`, `from_dream`, `from_hive`).
* Six negative cases reject unsafe / malformed input.
* The proof is deterministic when invoked with identical inputs.

The tests are intentionally hermetic — they import the orchestrator
module and call ``main()`` rather than spawning a subprocess, so they
run quickly inside the autonomy_growth test suite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Make the orchestrator importable as a module via its file path.
ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "phase17a_producer_fabric_proof_artifacts"


@pytest.fixture
def proof(out_dir: Path) -> dict:
    """Run the orchestrator once; return the proof JSON."""
    # Save and restore argv so the orchestrator's argparse sees only
    # the args we want.
    saved = sys.argv[:]
    try:
        sys.argv = [
            "run_phase17a_producer_fabric_proof.py",
            "--out-dir", str(out_dir),
            "--corpus-size", "30",
        ]
        # Import lazily to avoid module-import side effects at collection.
        import run_phase17a_producer_fabric_proof as orch  # type: ignore
        # Drop any cached module so re-import is clean per pytest run.
        rc = orch.main()
        assert rc == 0, "orchestrator exited non-zero"
    finally:
        sys.argv = saved

    proof_path = out_dir / "producer_fabric_proof.json"
    assert proof_path.is_file(), "producer_fabric_proof.json missing"
    return json.loads(proof_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Producer-fabric correctness
# ---------------------------------------------------------------------------

def test_proof_emits_phase_marker(proof: dict) -> None:
    assert proof["phase"] == "phase17a_producer_fabric"
    assert proof["schema_version"] == 1


def test_proof_corpus_total_at_least_30(proof: dict) -> None:
    assert proof["corpus_total"] >= 30


def test_proof_runs_all_four_producers(proof: dict) -> None:
    assert proof["producers_run"] == ["curiosity", "self_model",
                                          "dream", "hive"]


def test_proof_emits_ir_objects_across_kinds(proof: dict) -> None:
    """At least one IR object per consumer-side adapter kind, and a
    positive total."""
    per_kind = proof["ir_objects_per_kind"]
    # Producer fabric should produce at least these IR kinds.
    expected_kinds = {
        "curiosity",
        "self_model",
        "dream_curriculum",
        "dream_meta_proposal",
        "hive_proposals",
        "review_bundle",
    }
    assert expected_kinds.issubset(set(per_kind.keys())), \
        f"missing IR kinds: {expected_kinds - set(per_kind.keys())}"
    assert proof["ir_objects_emitted_total"] > 0
    # Curiosity should emit at least one IR object per corpus entry.
    assert per_kind["curiosity"] >= proof["corpus_total"]
    # Self-model should emit at least one IR object per tension or blind
    # spot. Phase 17A fixture always produces 6 tensions + 8 blind spots.
    assert per_kind["self_model"] >= 14


def test_proof_negative_cases_all_pass(proof: dict) -> None:
    cases = proof["negative_cases"]
    assert proof["negative_cases_total"] == 6
    assert proof["negative_cases_passed"] == 6, \
        f"failing negative cases: " + ", ".join(
            c["case"] for c in cases if not c.get("passed")
        )

    # Each named negative case must be present and pass.
    expected_cases = {
        "missing_curiosity_log",
        "malformed_self_model",
        "high_risk_proposal_not_promoted",
        "human_approval_in_offline_proof",
        "stage2_flip_request_in_offline_proof",
        "unknown_family_rejected",
    }
    case_names = {c["case"] for c in cases}
    assert case_names == expected_cases
    for c in cases:
        assert c["passed"], f"negative case failed: {c}"


# ---------------------------------------------------------------------------
# Inner-loop invariants (carry-forward from Phase 11–16D RULE 7)
# ---------------------------------------------------------------------------

def test_proof_provider_jobs_delta_zero(proof: dict) -> None:
    assert proof["provider_jobs_delta_during_proof"] == 0


def test_proof_builder_jobs_delta_zero(proof: dict) -> None:
    assert proof["builder_jobs_delta_during_proof"] == 0


def test_proof_no_provider_credentials_required(proof: dict) -> None:
    assert proof["no_provider_credentials_required"] is True


def test_proof_no_runtime_network_required(proof: dict) -> None:
    assert proof["no_runtime_network_required"] is True


def test_proof_no_human_approval_collected(proof: dict) -> None:
    assert proof["no_human_approval_collected"] is True


def test_proof_no_stage2_flip_executed(proof: dict) -> None:
    assert proof["no_stage2_flip_executed"] is True


def test_proof_no_allowlist_widening(proof: dict) -> None:
    assert proof["no_allowlist_widening"] is True
    expected = (
        "scalar_unit_conversion",
        "lookup_table",
        "threshold_rule",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "bounded_interpolation",
    )
    assert tuple(proof["allowed_families"]) == expected


# ---------------------------------------------------------------------------
# Artifact emission
# ---------------------------------------------------------------------------

def test_proof_emits_per_producer_artifact_files(proof: dict,
                                                       out_dir: Path) -> None:
    artifacts = proof["produced_artifacts"]
    expected_artifacts = {
        "curiosity_log.json",
        "self_model_snapshot.json",
        "dream_curriculum.json",
        "hive_proposals_and_review_bundle.json",
    }
    assert set(artifacts.keys()) == expected_artifacts
    for name, path in artifacts.items():
        p = Path(path)
        assert p.is_file(), f"missing artifact {name}: {path}"
        # All artifacts must be valid JSON.
        json.loads(p.read_text(encoding="utf-8"))


def test_proof_curiosity_log_uses_only_allowed_families(out_dir: Path,
                                                              proof: dict
                                                              ) -> None:
    cur_log_path = Path(proof["produced_artifacts"]["curiosity_log.json"])
    rows = json.loads(cur_log_path.read_text(encoding="utf-8"))
    seen_families = {r.get("_phase17a_family_kind") for r in rows
                      if r.get("_phase17a_family_kind") is not None}
    expected = {
        "scalar_unit_conversion",
        "lookup_table",
        "threshold_rule",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "bounded_interpolation",
    }
    assert seen_families == expected
    assert len(seen_families) == 6  # exactly the allowlist


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_proof_deterministic_across_two_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive runs against the same args produce identical
    pinned_input_manifest_sha256 and identical curiosity_log content.

    Wall-clock fields (started_at_utc, finished_at_utc) intentionally
    excluded from the determinism check.
    """
    saved = sys.argv[:]

    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"
    timestamps = iter(
        f"2026-05-10T08:59:{second:02d}Z" for second in range(10)
    )

    try:
        for od in (out_a, out_b):
            sys.argv = [
                "run_phase17a_producer_fabric_proof.py",
                "--out-dir", str(od),
                "--corpus-size", "30",
            ]
            import run_phase17a_producer_fabric_proof as orch  # noqa: F811
            monkeypatch.setattr(orch, "_utc_iso_now", lambda: next(timestamps))
            assert orch.main() == 0
    finally:
        sys.argv = saved

    proof_a = json.loads((out_a / "producer_fabric_proof.json")
                            .read_text(encoding="utf-8"))
    proof_b = json.loads((out_b / "producer_fabric_proof.json")
                            .read_text(encoding="utf-8"))

    # Pinned input manifest sha must be identical.
    assert proof_a["pinned_input_manifest_sha256"] == \
           proof_b["pinned_input_manifest_sha256"]
    # IR object counts must be identical.
    assert proof_a["ir_objects_per_kind"] == proof_b["ir_objects_per_kind"]
    assert proof_a["ir_objects_emitted_total"] == \
           proof_b["ir_objects_emitted_total"]
    assert proof_a["corpus_total"] == proof_b["corpus_total"]

    # Curiosity log payload must be byte-for-byte identical.
    cur_a = (out_a / "curiosity_log.json").read_text(encoding="utf-8")
    cur_b = (out_b / "curiosity_log.json").read_text(encoding="utf-8")
    assert cur_a == cur_b


# ---------------------------------------------------------------------------
# IR adapter consumer-shape regression
# ---------------------------------------------------------------------------

def test_curiosity_log_shape_consumed_by_main_adapter(out_dir: Path,
                                                            proof: dict
                                                            ) -> None:
    """Re-load the curiosity_log.json that the orchestrator wrote and
    pass it through the existing main IR adapter to confirm shape
    compatibility (no field renames, no missing keys)."""
    from waggledance.core.ir.adapters import from_curiosity as ad
    from waggledance.core.ir.cognition_ir import Provenance

    rows = json.loads(
        Path(proof["produced_artifacts"]["curiosity_log.json"])
        .read_text(encoding="utf-8")
    )
    prov = Provenance(
        branch_name="phase17a/test",
        base_commit_hash="phase17a-test",
        pinned_input_manifest_sha256="phase17a-test",
        produced_by="phase17a_test",
        source_session="A_curiosity",
        fixture_fallback_used=False,
    )
    ir = ad.adapt_curiosity_log(rows, prov)
    assert len(ir) == proof["corpus_total"]


def test_self_model_shape_consumed_by_main_adapter(proof: dict) -> None:
    from waggledance.core.ir.adapters import from_self_model as ad
    from waggledance.core.ir.cognition_ir import Provenance

    sm = json.loads(
        Path(proof["produced_artifacts"]["self_model_snapshot.json"])
        .read_text(encoding="utf-8")
    )
    prov = Provenance(
        branch_name="phase17a/test",
        base_commit_hash="phase17a-test",
        pinned_input_manifest_sha256="phase17a-test",
        produced_by="phase17a_test",
        source_session="B_self_model",
        fixture_fallback_used=False,
    )
    ir = ad.adapt_self_model(sm, prov)
    # 6 tensions + 8 blind spots = 14 IR objects.
    assert len(ir) == 14


def test_hive_proposals_shape_consumed_by_main_adapter(proof: dict) -> None:
    from waggledance.core.ir.adapters import from_hive as ad
    from waggledance.core.ir.cognition_ir import Provenance

    hive = json.loads(
        Path(proof["produced_artifacts"]
             ["hive_proposals_and_review_bundle.json"])
        .read_text(encoding="utf-8")
    )
    prov = Provenance(
        branch_name="phase17a/test",
        base_commit_hash="phase17a-test",
        pinned_input_manifest_sha256="phase17a-test",
        produced_by="phase17a_test",
        source_session="D_hive_proposes",
        fixture_fallback_used=False,
    )
    ir = ad.adapt_hive_proposals(hive, prov)
    # We don't assert an exact count; just that >0 and matches proof.
    assert len(ir) == proof["ir_objects_per_kind"]["hive_proposals"]

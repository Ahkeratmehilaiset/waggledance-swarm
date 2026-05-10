# SPDX-License-Identifier: BUSL-1.1
"""Phase 17A — 10k Solver Scale Proof tests.

These tests run ``tools/run_solver_scale_proof.py`` against a small
synthetic descriptor count (240) inside a tmp directory and assert
every contract documented in
``docs/runs/phase17a_producer_fabric_scale_2026_05_04/implementation_plan.md``.

Why 240 (not 10000) inside the test:

    * 10000 descriptors take ~150 s on this hardware in build phase.
      Tests must stay under the autonomy_growth suite's per-test
      budget. The proof tool is exercised at full 10k scale by:
        - the operator's local run before PR (recorded in
          ``solver_scale_proof.json``);
        - the Docker run inside ``--network none``;
        - the post-merge run on origin/main.
      The test only proves the *code path* works end-to-end; the
      release artifact proves it works at 10000.
    * 240 descriptors still exercises 6 families × 8 cells (×5 each)
      which preserves the balanced-distribution invariant.

Note: the orchestrator and proof tool import the *real*
``RuntimeQueryRouter`` + ``LowRiskSolverDispatcher`` + ``ControlPlaneDB``;
no mocks, no synthetic shortcuts of the data path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "phase17a_solver_scale_proof_artifacts"


@pytest.fixture
def proof(out_dir: Path, tmp_path: Path) -> dict:
    """Run the scale proof at 240 descriptors and return the proof JSON."""
    db_path = tmp_path / "scale_proof_test.db"
    saved = sys.argv[:]
    try:
        sys.argv = [
            "run_solver_scale_proof.py",
            "--out-dir", str(out_dir),
            "--descriptors", "240",
            "--lookup-pass-count", "120",
            "--db", str(db_path),
        ]
        import run_solver_scale_proof as scale  # type: ignore
        rc = scale.main()
        assert rc == 0, "scale proof exited non-zero"
    finally:
        sys.argv = saved

    proof_path = out_dir / "solver_scale_proof.json"
    assert proof_path.is_file(), "solver_scale_proof.json missing"
    return json.loads(proof_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Honesty / claims-labelling
# ---------------------------------------------------------------------------

def test_proof_clearly_labelled_synthetic(proof: dict) -> None:
    """Master prompt rule 16: descriptors must be labelled
    synthetic-scale, NOT canonical proof corpus."""
    assert proof["is_synthetic_scale"] is True
    assert proof["not_canonical_corpus"] is True


def test_proof_emits_phase_marker(proof: dict) -> None:
    assert proof["phase"] == "phase17a_solver_scale"
    assert proof["schema_version"] == 1


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def test_proof_descriptor_count(proof: dict) -> None:
    assert proof["synthetic_solver_descriptors_total"] == 240


def test_proof_families_total(proof: dict) -> None:
    assert proof["families_total"] == 6
    expected_families = {
        "scalar_unit_conversion",
        "lookup_table",
        "threshold_rule",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "bounded_interpolation",
    }
    assert set(proof["allowed_families"]) == expected_families
    assert set(proof["descriptors_per_family"].keys()) == expected_families


def test_proof_descriptors_balanced_across_families(proof: dict) -> None:
    """240 / 6 = 40 descriptors per family exactly."""
    counts = proof["descriptors_per_family"]
    for family, count in counts.items():
        assert count == 40, \
            f"family {family} has {count} descriptors, expected 40"


def test_proof_hex_cells_total(proof: dict) -> None:
    assert proof["hex_cells_total"] == 8
    expected_cells = {
        "general", "thermal", "energy", "safety",
        "seasonal", "math", "system", "learning",
    }
    assert set(proof["hex_cells"]) == expected_cells


def test_proof_descriptors_balanced_across_cells(proof: dict) -> None:
    """240 / 8 = 30 descriptors per cell exactly."""
    counts = proof["descriptors_per_hex_cell"]
    for cell, count in counts.items():
        assert count == 30, \
            f"cell {cell} has {count} descriptors, expected 30"


# ---------------------------------------------------------------------------
# Capability lookup correctness
# ---------------------------------------------------------------------------

def test_proof_all_lookups_hit_capability_path(proof: dict) -> None:
    """Critical invariant: every sampled query must be served via the
    real auto_promoted_solver source. NO FIFO fallback. NO miss.

    A failure here means the test is exercising the FIFO fallback or
    the gap-emit path, not the capability lookup path. The whole
    point of the scale proof is to prove the capability path scales.
    """
    assert proof["lookup_capability_hits_total"] == proof["lookup_pass_count"]
    assert proof["lookup_fifo_fallback_total"] == 0
    assert proof["lookup_miss_total"] == 0
    assert proof["lookup_by_source"] == {
        "auto_promoted_solver": proof["lookup_pass_count"]
    }


def test_proof_uses_production_hot_path_cache(proof: dict) -> None:
    """R22.1a: the scale proof must measure the production-shaped
    RuntimeQueryRouter wiring with HotPathCache attached, not the old
    no-cache benchmark-only path."""
    assert proof["production_hot_path_cache_attached"] is True
    assert proof["lookup_benchmark_shape"] == "hot_path_cache_attached_warm_pass"
    hot = proof["hot_path_cache_stats"]
    assert hot["cold_hits_warmed"] == proof["lookup_pass_count"]
    assert hot["warm_hits"] == proof["lookup_pass_count"]
    assert hot["misses"] == 0
    assert hot["warm_index_size_after_lookup"] == proof["lookup_pass_count"]
    assert hot["artifact_cache_size_after_lookup"] == proof["lookup_pass_count"]
    cold = proof["lookup_cold_after_attach"]
    assert cold["lookup_capability_hits_total"] == proof["lookup_pass_count"]
    assert cold["lookup_fifo_fallback_total"] == 0
    assert cold["lookup_miss_total"] == 0


def test_proof_lookup_latency_p50_under_100ms(proof: dict) -> None:
    """p50 capability lookup latency should be well under 100 ms even
    with a SQLite-backed control plane and 240 descriptors. This is
    a sanity check, not a strict performance claim."""
    assert proof["lookup_p50_ms"] is not None
    assert proof["lookup_p50_ms"] < 100.0


def test_proof_lookup_p99_finite(proof: dict) -> None:
    assert proof["lookup_p99_ms"] is not None
    # No hard upper bound — only sanity-check that a number was emitted.
    assert proof["lookup_p99_ms"] >= proof["lookup_p50_ms"]


# ---------------------------------------------------------------------------
# Inner-loop invariants (carry-forward from Phase 11–16D RULE 7)
# ---------------------------------------------------------------------------

def test_proof_provider_jobs_delta_zero(proof: dict) -> None:
    assert proof["provider_jobs_delta"] == 0


def test_proof_builder_jobs_delta_zero(proof: dict) -> None:
    assert proof["builder_jobs_delta"] == 0


def test_proof_no_provider_credentials_required(proof: dict) -> None:
    assert proof["no_provider_credentials_required"] is True


def test_proof_no_runtime_network_required(proof: dict) -> None:
    assert proof["no_runtime_network_required"] is True


def test_proof_no_allowlist_widening(proof: dict) -> None:
    assert proof["no_allowlist_widening"] is True


# ---------------------------------------------------------------------------
# Build-time evidence
# ---------------------------------------------------------------------------

def test_proof_emits_build_index_time(proof: dict) -> None:
    assert "build_index_time_seconds" in proof
    assert isinstance(proof["build_index_time_seconds"], (int, float))
    assert proof["build_index_time_seconds"] > 0


def test_proof_emits_build_descriptors_per_second(proof: dict) -> None:
    assert "build_descriptors_per_second" in proof
    rate = proof["build_descriptors_per_second"]
    assert isinstance(rate, (int, float))
    assert rate > 0


# ---------------------------------------------------------------------------
# Real RuntimeQueryRouter is exercised (no mock bypass)
# ---------------------------------------------------------------------------

def test_real_capability_lookup_path_exercised() -> None:
    """Importing the proof script must pull in the real runtime router
    and dispatcher classes (not a mock or a synthetic stand-in)."""
    import run_solver_scale_proof as scale  # type: ignore
    from waggledance.core.autonomy_growth.runtime_query_router import (
        RuntimeQueryRouter as _RealRouter,
    )
    from waggledance.core.autonomy_growth.hot_path_cache import (
        HotPathCache as _RealHotPathCache,
    )
    from waggledance.core.autonomy_growth.solver_dispatcher import (
        LowRiskSolverDispatcher as _RealDispatcher,
    )
    from waggledance.core.storage.control_plane import (
        ControlPlaneDB as _RealCP,
    )
    # The proof module imports the real classes by name.
    assert scale.RuntimeQueryRouter is _RealRouter
    assert scale.HotPathCache is _RealHotPathCache
    assert scale.LowRiskSolverDispatcher is _RealDispatcher
    assert scale.ControlPlaneDB is _RealCP


# ---------------------------------------------------------------------------
# Synthesizer correctness
# ---------------------------------------------------------------------------

def test_synthesize_descriptors_unique_solver_names() -> None:
    import run_solver_scale_proof as scale  # type: ignore
    descriptors = scale.synthesize_descriptors(60)
    names = {d["solver_name"] for d in descriptors}
    assert len(names) == 60


def test_synthesize_features_only_within_allowlist() -> None:
    """Each synthesized feature dict belongs to one of the six
    low-risk families. No feature dict may bleed across families."""
    import run_solver_scale_proof as scale  # type: ignore
    for family in scale.ALLOWED_FAMILIES:
        feats = scale.synthesize_features(family, 0)
        # Every family includes the synth_id key for unique lookup.
        assert "synth_id" in feats
        # Family-specific keys are present.
        if family == "scalar_unit_conversion":
            assert "from_unit" in feats and "to_unit" in feats
        elif family == "lookup_table":
            assert "domain" in feats
        elif family == "threshold_rule":
            assert "subject" in feats and "operator" in feats
        elif family == "interval_bucket_classifier":
            assert "key" in feats and "dimension_count" in feats
        elif family == "linear_arithmetic":
            assert "x_var" in feats and "y_var" in feats
        elif family == "bounded_interpolation":
            assert "x_var" in feats and "y_var" in feats
            assert "sample_count" in feats


def test_synthesize_artifact_returns_executable_kind() -> None:
    """Each synthesized artifact carries the family ``kind`` field
    that ``execute_artifact`` requires."""
    import run_solver_scale_proof as scale  # type: ignore
    for family in scale.ALLOWED_FAMILIES:
        artifact, inputs = scale.synthesize_artifact_and_inputs(family, 0)
        assert artifact.get("kind") == family
        assert isinstance(inputs, dict) and len(inputs) >= 1

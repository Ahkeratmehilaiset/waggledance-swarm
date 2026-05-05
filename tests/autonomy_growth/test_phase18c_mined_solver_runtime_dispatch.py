# SPDX-License-Identifier: BUSL-1.1
"""Phase 18C — mined solver runtime dispatch integration tests.

Covers the canonical contract: Phase 18B mined ALLOWLISTED specs
register through the real `ControlPlaneDB` + `LowRiskSolverDispatcher`
path; non-allowlisted verdicts fail closed; six-family dispatch hits;
provider/builder delta = 0; allowlist unchanged; carry-forward gates
(Phase 18A bundle, Phase 18B proof) still pass.
"""

from __future__ import annotations

import importlib
import json
import math
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
    GapCandidate,
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_mining import (
    ALLOWED_FAMILIES,
    GapMiningConfig,
    mine_runtime_gaps,
)
from waggledance.core.autonomy_growth.mined_solver_runtime import (
    RuntimeArtifactCompilationError,
    compile_mined_spec_to_runtime_artifact,
    register_mined_solver_specs,
)
from waggledance.core.autonomy_growth.solver_dispatcher import (
    LowRiskSolverDispatcher,
)
from waggledance.core.storage.control_plane import ControlPlaneDB

import run_phase18b_gap_miner_feedback_proof as p18b


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def cp(tmp_path: Path) -> ControlPlaneDB:
    db = ControlPlaneDB(tmp_path / "p18c_test.db")
    yield db
    db.close()


@pytest.fixture
def mined_result():
    """Run Phase 18B mining once; cached for the module."""
    signals = p18b.build_synthetic_fixture()
    return mine_runtime_gaps(signals, config=GapMiningConfig())


def _allowlisted_candidates(result) -> list[GapCandidate]:
    return [
        c for c in result.candidates
        if c.verdict == GapVerdict.ALLOWLISTED_SOLVER_SPEC
    ]


def _stringify_features(features: dict) -> dict:
    out = {}
    for k, v in features.items():
        if isinstance(v, (str, int, float, bool)):
            out[str(k)] = str(v)
        elif v is None:
            out[str(k)] = ""
        else:
            out[str(k)] = json.dumps(v, sort_keys=True,
                                       separators=(",", ":"),
                                       default=str, ensure_ascii=True)
    return out


# ---------------------------------------------------------------------------
# 1-3. Registration happy path
# ---------------------------------------------------------------------------

def test_allowlisted_candidates_register(cp, mined_result):
    """Phase 18B mined ALLOWLISTED candidates register as executable specs."""
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    assert summary.registered_count == 6
    assert len(summary.registered_solver_ids) == 6
    assert len(summary.registered_candidate_ids) == 6


def test_exactly_six_families_register(cp, mined_result):
    """One ALLOWLISTED candidate per six-family allowlist family registers."""
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    families_seen = set()
    for cand_id in summary.registered_candidate_ids:
        cand = next(
            c for c in mined_result.candidates if c.candidate_id == cand_id
        )
        families_seen.add(cand.family_kind)
    assert families_seen == set(ALLOWED_FAMILIES)


def test_registration_idempotent_within_run(cp, mined_result):
    """Registering the same candidate list twice yields one solver per id."""
    summary1 = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    summary2 = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    assert summary1.registered_count == 6
    # Second call still upserts (existing rows; not duplicated solver names).
    assert summary2.registered_count == 6
    # SQLite solver name is unique; second pass should keep the same row.
    counts = []
    for fam in ALLOWED_FAMILIES:
        n = cp._conn.execute(  # type: ignore[attr-defined]
            "SELECT COUNT(*) FROM solvers WHERE name LIKE ?",
            (f"phase18c_{fam}_%",),
        ).fetchone()[0]
        counts.append(n)
    assert all(c == 1 for c in counts), counts


# ---------------------------------------------------------------------------
# 4-6. Determinism + provenance
# ---------------------------------------------------------------------------

def test_candidate_ids_are_deterministic_and_preserved(cp, mined_result):
    """candidate_ids are a stable function of (family, feature_dict)."""
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    cand_ids_a = sorted(summary.registered_candidate_ids)

    # Re-mine and re-register into a fresh CP; ids must match.
    cp2_result = mine_runtime_gaps(
        p18b.build_synthetic_fixture(), config=GapMiningConfig(),
    )
    cp2_ids = sorted(c.candidate_id for c in _allowlisted_candidates(cp2_result))
    assert cand_ids_a == cp2_ids


def test_spec_hash_is_deterministic_via_solver_record(cp, mined_result):
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    hashes = []
    for sid in summary.registered_solver_ids:
        row = cp._conn.execute(  # type: ignore[attr-defined]
            "SELECT spec_hash FROM solvers WHERE id = ?", (sid,),
        ).fetchone()
        hashes.append(row["spec_hash"])
    assert all(isinstance(h, str) and len(h) == 64 for h in hashes)
    assert len(set(hashes)) == 6  # one unique hash per family


def test_every_registered_solver_has_capability_features(cp, mined_result):
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    for sid in summary.registered_solver_ids:
        feats = cp.get_solver_capability_features(sid)
        assert feats, f"solver {sid} has no capability features"


# ---------------------------------------------------------------------------
# 7-13. Fail-closed contract
# ---------------------------------------------------------------------------

def test_unknown_family_compilation_fails_closed():
    with pytest.raises(RuntimeArtifactCompilationError):
        compile_mined_spec_to_runtime_artifact({
            "family_kind": "multi_step_reasoning",
            "feature_dict": {"task": "x"},
        })


def test_unrecognized_features_compilation_fails_closed():
    with pytest.raises(RuntimeArtifactCompilationError):
        compile_mined_spec_to_runtime_artifact({
            "family_kind": "scalar_unit_conversion",
            "feature_dict": {"input_unit": "lb", "output_unit": "kg"},
        })


def test_high_risk_does_not_register(cp, mined_result):
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    assert summary.rejected_by_verdict.get(
        GapVerdict.HIGH_RISK_REJECTED.value, 0
    ) == 1
    # And the registered set has no high-risk markers.
    for sid in summary.registered_solver_ids:
        feats = cp.get_solver_capability_features(sid)
        names = [f.feature_name for f in feats]
        assert "high_risk_marker" not in names


def test_out_of_family_does_not_register(cp, mined_result):
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    assert summary.rejected_by_verdict.get(
        GapVerdict.OUT_OF_FAMILY_REJECTED.value, 0
    ) == 2


def test_insufficient_evidence_does_not_register(cp, mined_result):
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    assert summary.rejected_by_verdict.get(
        GapVerdict.INSUFFICIENT_EVIDENCE.value, 0
    ) == 3


def test_duplicate_does_not_register_twice(cp, mined_result):
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    # Phase 18B itself emits the DUPLICATE verdict; Phase 18C rejects it.
    assert summary.rejected_by_verdict.get(
        GapVerdict.DUPLICATE_SUPPRESSED.value, 0
    ) == 1


def test_builder_handoff_is_quarantined(cp, mined_result):
    summary = register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    assert summary.builder_handoff_quarantined == 1
    # No solver row exists for the builder-handoff family.
    n = cp._conn.execute(  # type: ignore[attr-defined]
        "SELECT COUNT(*) FROM solvers WHERE name LIKE ?",
        ("phase18c_builder_handoff_%",),
    ).fetchone()[0]
    assert n == 0


# ---------------------------------------------------------------------------
# 14-15. Real runtime dispatch
# ---------------------------------------------------------------------------

def test_dispatch_through_real_router(cp, mined_result):
    """Capability lookup through the actual LowRiskSolverDispatcher
    serves a registered mined spec."""
    register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    dispatcher = LowRiskSolverDispatcher(cp)
    res = dispatcher.dispatch_by_features(
        family_kind="scalar_unit_conversion",
        features=_stringify_features({
            "input_unit": "km", "output_unit": "miles",
            "rule": "1 km = 0.621371 miles",
        }),
        inputs={"x": 10.0},
    )
    assert res.matched is True
    assert res.reason == "hit_by_features"
    assert math.isclose(float(res.output), 6.21371,
                          rel_tol=1e-9, abs_tol=1e-9)


def test_at_least_three_dispatch_cases_per_family_hit(cp, mined_result):
    """Verify all 18 dispatch cases from the proof harness hit."""
    register_mined_solver_specs(
        candidates=mined_result.candidates, control_plane=cp,
    )
    name = "run_phase18c_mined_solver_runtime_dispatch_proof"
    if name in sys.modules:
        del sys.modules[name]
    harness = importlib.import_module(name)

    dispatcher = LowRiskSolverDispatcher(cp)
    per_family_hits: dict[str, int] = {}
    for case in harness.DISPATCH_CASES:
        res = dispatcher.dispatch_by_features(
            family_kind=case["family_kind"],
            features=_stringify_features(dict(case["features"])),
            inputs=dict(case["inputs"]),
        )
        if res.matched and res.reason == "hit_by_features":
            per_family_hits[case["family_kind"]] = (
                per_family_hits.get(case["family_kind"], 0) + 1
            )
    assert set(per_family_hits.keys()) == set(ALLOWED_FAMILIES)
    for fam, n in per_family_hits.items():
        assert n >= 3, f"family {fam}: only {n} hits"


# ---------------------------------------------------------------------------
# 16-23. Honesty invariants surfaced by the proof harness
# ---------------------------------------------------------------------------

@pytest.fixture
def proof_doc(tmp_path: Path):
    name = "run_phase18c_mined_solver_runtime_dispatch_proof"
    if name in sys.modules:
        del sys.modules[name]
    harness = importlib.import_module(name)
    proof = harness.build_proof(
        out_dir=tmp_path,
        control_plane_path=tmp_path / "phase18c_proof.db",
    )
    return proof


def test_provider_jobs_delta_zero(proof_doc):
    assert proof_doc["provider_jobs_delta"] == 0


def test_builder_jobs_delta_zero(proof_doc):
    assert proof_doc["builder_jobs_delta"] == 0


def test_allowlist_unchanged_in_proof(proof_doc):
    assert proof_doc["allowlist_unchanged"] is True
    assert ALLOWED_FAMILIES == (
        "scalar_unit_conversion", "lookup_table", "threshold_rule",
        "interval_bucket_classifier", "linear_arithmetic",
        "bounded_interpolation",
    )


def test_no_stage2_no_human_approval(proof_doc):
    assert proof_doc["no_stage2_flip"] is True
    assert proof_doc["no_human_approval"] is True


def test_no_live_builder_execution(proof_doc):
    assert proof_doc["no_live_builder_execution"] is True


def test_no_cloud_api_calls(proof_doc):
    assert proof_doc["no_cloud_api_calls"] is True


def test_no_model_pull_or_download(proof_doc):
    assert proof_doc["no_model_pull_or_download"] is True


def test_no_high_risk_autonomy(proof_doc):
    assert proof_doc["no_high_risk_autonomy"] is True


# ---------------------------------------------------------------------------
# 24-25. Proof JSON shape + forbidden vocabulary
# ---------------------------------------------------------------------------

def test_proof_json_shape(proof_doc):
    expected_keys = {
        "phase", "benchmark_version", "started_utc", "finished_utc",
        "base_main_sha", "python_version", "platform",
        "source_prerelease", "candidate_prerelease",
        "is_synthetic_fixture", "fixture_size", "config_snapshot",
        "phase18b_counters", "signals_total", "candidates_total",
        "allowlisted_candidate_count", "insufficient_evidence_total",
        "out_of_family_rejected_total", "high_risk_rejected_total",
        "builder_handoff_quarantine_count", "duplicate_suppression_count",
        "registered_solver_count", "rejected_registration_count",
        "rejected_by_verdict", "registration_summary",
        "dispatch_case_count", "dispatch_success_count",
        "dispatch_failure_count", "families_covered",
        "per_family_dispatch_counts", "per_dispatch_case",
        "allowlist_unchanged", "provider_jobs_delta", "builder_jobs_delta",
        "no_model_pull_or_download", "no_cloud_api_calls",
        "no_live_builder_execution", "no_stage2_flip", "no_human_approval",
        "no_high_risk_autonomy", "no_cross_vendor_ranking_claim",
        "no_raw_intelligence_superiority_claim", "claim_labels",
        "forbidden_claims_absent", "forbidden_substring_hits",
        "release_gates", "release_gate_pass",
    }
    missing = expected_keys - set(proof_doc.keys())
    assert not missing, f"missing keys: {missing}"
    assert proof_doc["phase"] == "phase18c_mined_solver_runtime_dispatch"
    assert proof_doc["benchmark_version"] == "phase18c.v1"
    assert proof_doc["candidate_prerelease"] == "v3.10.2-mined-solver-dispatch-alpha"


def test_no_forbidden_claims_in_proof(proof_doc):
    assert proof_doc["forbidden_claims_absent"] is True
    assert proof_doc["forbidden_substring_hits"]["json_hits"] == []
    assert proof_doc["forbidden_substring_hits"]["md_hits"] == []


def test_release_gate_pass(proof_doc):
    assert proof_doc["release_gate_pass"] is True
    gates = proof_doc["release_gates"]
    assert all(gates.values()), f"failing gates: {gates}"


def test_claim_labels_required_set(proof_doc):
    cl = proof_doc["claim_labels"]
    assert cl["runtime_gap_feedback"] == "PROVEN-WITH-RUNTIME-DISPATCH"
    assert cl["mined_solver_specs"] == (
        "MEASURED-RUNTIME-DISPATCH-MINED-SOLVERS-SIX-FAMILY"
    )
    assert cl["builder_handoff"] == "QUARANTINED-NOT-AUTOPROMOTED"
    assert cl["high_risk_families"] == "NOT_CLAIMED"
    assert cl["raw_intelligence_vs_frontier_moe"] == "NOT_CLAIMED"
    assert cl["cross_vendor_ranking"] == "NOT_CLAIMED"
    assert cl["consciousness"] == "NOT_CLAIMED"


def test_per_family_dispatch_counts(proof_doc):
    pfc = proof_doc["per_family_dispatch_counts"]
    assert set(pfc.keys()) == set(ALLOWED_FAMILIES)
    for fam, n in pfc.items():
        assert n >= 3, f"family {fam}: only {n} cases"


# ---------------------------------------------------------------------------
# 26-27. Carry-forward gates
# ---------------------------------------------------------------------------

def test_phase18a_bundle_still_validates():
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


def test_phase18b_proof_still_passes(tmp_path):
    """Carry-forward: Phase 18B proof harness still produces release_gate_pass=true."""
    name = "run_phase18b_gap_miner_feedback_proof"
    if name in sys.modules:
        del sys.modules[name]
    harness = importlib.import_module(name)
    proof = harness.build_proof(out_dir=tmp_path)
    assert proof["release_gate_pass"] is True
    assert proof["provider_jobs_delta"] == 0
    assert proof["builder_jobs_delta"] == 0


# ---------------------------------------------------------------------------
# 28-30. Misc invariants
# ---------------------------------------------------------------------------

def test_proof_is_deterministic_enough(tmp_path: Path):
    """Two runs produce identical candidate IDs, registration counts,
    and pass/fail counters (timestamps may differ)."""
    name = "run_phase18c_mined_solver_runtime_dispatch_proof"
    if name in sys.modules:
        del sys.modules[name]
    harness = importlib.import_module(name)
    p1 = harness.build_proof(
        out_dir=tmp_path / "a",
        control_plane_path=tmp_path / "a.db",
    )
    p2 = harness.build_proof(
        out_dir=tmp_path / "b",
        control_plane_path=tmp_path / "b.db",
    )
    for k in (
        "signals_total", "candidates_total", "allowlisted_candidate_count",
        "registered_solver_count", "rejected_registration_count",
        "dispatch_case_count", "dispatch_success_count",
        "dispatch_failure_count", "families_covered",
        "release_gate_pass", "forbidden_claims_absent",
    ):
        assert p1[k] == p2[k], f"non-deterministic field: {k}"
    # Candidate ids should be byte-identical between runs.
    ids_a = sorted(p1["registration_summary"]["registered_candidate_ids"])
    ids_b = sorted(p2["registration_summary"]["registered_candidate_ids"])
    assert ids_a == ids_b


def test_no_db_or_transient_files_in_repo():
    """The proof harness's default --out-dir is committed; it must not
    contain a SQLite DB. The harness routes ControlPlaneDB to /tmp."""
    out_dir = (
        ROOT / "docs" / "runs"
        / "phase18c_mined_solver_runtime_dispatch_2026_05_05"
    )
    if not out_dir.is_dir():
        pytest.skip("no committed proof yet")
    db_files = list(out_dir.rglob("*.db"))
    db_files += list(out_dir.rglob("*.sqlite"))
    db_files += list(out_dir.rglob("*.wal"))
    db_files += list(out_dir.rglob("*.shm"))
    assert not db_files, f"DB/WAL files found: {db_files}"


def test_current_status_md_remains_truthful():
    """CURRENT_STATUS.md still references the 8bf1869 truthfulness commit
    (Phase 10 truth-regression invariant)."""
    cs = (ROOT / "CURRENT_STATUS.md").read_text(encoding="utf-8")
    assert "8bf1869" in cs

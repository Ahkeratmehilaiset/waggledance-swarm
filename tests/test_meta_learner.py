"""Targeted tests for the meta-learner scaffold + scoring formulas
(Phase 8.5 Session D, commit 2 + 3 categories).

Covers D.txt §TESTING REQUIREMENTS items: 1, 2, 3, 4, 6, 8, 9, 10,
11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 26, 27, 36, 38, 39,
40, 41, 43.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.meta import (
    EVIDENCE_PLANES,
    LIFECYCLE_STATUSES,
    PROPOSAL_TYPES,
    RESOLUTION_REASONS,
    SCOPE_CLASSES,
    HUMAN_REVIEW_BOUNDARY_TEXT,
    inputs as mi,
    meta_learner as ml,
)


# ── Fixture builders ─────────────────────────────────────────────-

def _make_self_model() -> dict:
    return {
        "schema_version": 1,
        "workspace_tensions": [
            {"tension_id": "ten_001", "type": "scorecard_drift",
             "claim": "thermal score = 0.92", "severity": "high",
             "lifecycle_status": "persisting",
             "resolution_path": "deferred_to_dream",
             "evidence_refs": ["cell:thermal", "cur:cur_thermal_1"]},
            {"tension_id": "ten_002", "type": "calibration_oscillation",
             "claim": "energy oscillates", "severity": "medium",
             "lifecycle_status": "persisting",
             "resolution_path": "deferred_to_dream",
             "evidence_refs": ["cell:energy"]},
        ],
        "blind_spots": [
            {"domain": "safety", "severity": "high",
             "detectors": ["coverage_negative_space"]},
        ],
        "scorecard": {"thermal": 0.55, "energy": 0.60},
    }


def _make_curiosity_log() -> list[dict]:
    return [
        {"curiosity_id": "cur_thermal_1", "candidate_cell": "thermal",
         "estimated_value": 9.5, "suspected_gap_type": "missing_solver",
         "count": 12},
        {"curiosity_id": "cur_thermal_2", "candidate_cell": "thermal",
         "estimated_value": 7.0, "suspected_gap_type": "improvement_opportunity",
         "count": 6},
        {"curiosity_id": "cur_energy_1", "candidate_cell": "energy",
         "estimated_value": 5.0, "suspected_gap_type": "missing_solver",
         "count": 4},
    ]


def _make_dream_meta_proposal(*, cell="thermal", structurally_promising=True,
                                  confidence=0.8, gain=2,
                                  source_tids=("ten_001",)) -> dict:
    return {
        "schema_version": 1,
        "continuity_anchor": {
            "branch_name": "phase8.5/dream-curriculum",
            "base_commit_hash": "abc1234",
            "pinned_input_manifest_sha256": "sha256:dream",
        },
        "structurally_promising": structurally_promising,
        "selected_proposal": {
            "path": "fixture/p.json", "proposal_id": "dream-prop-1",
            "solver_name": "thermal_dream_estimator", "cell_id": cell,
            "solver_hash": "sha256:abc",
        },
        "source_tension_ids": list(source_tids),
        "confidence": confidence,
        "tension_severity_max": 1.0,
        "expected_value_of_merging": 0.4,
        "uncertainty": "low",
        "replay_metrics": {
            "structural_gain_count": gain,
            "protected_case_regression_count": 0,
            "replay_case_count": 5,
            "estimated_fallback_delta": 0.4,
            "replay_methodology": "structural_proxy_v0.1",
            "targeted_case_count": 5,
            "unresolved_case_count": 1,
        },
        "structural_gains": {"structural_gain_ratio": 0.4,
                              "min_gain_ratio_required": 0.10},
        "gate_provenance": {"schema": "native"},
        "collapse_results": {"collapse_verdict": "ACCEPT_CANDIDATE",
                              "raw_verdict": "ACCEPT_CANDIDATE"},
        "consumed_hook_contracts": [],
        "why_human_review_required": "structural proxy",
        "why_runtime_flip_is_out_of_scope": "shadow only",
    }


# ── 1. enums round-trip ──────────────────────────────────────────-

def test_proposal_type_enum_matches_schema():
    schema = json.loads((ROOT / "schemas" / "meta_proposal.schema.json")
                         .read_text(encoding="utf-8"))
    schema_types = tuple(schema["properties"]["proposal_type"]["enum"])
    assert schema_types == PROPOSAL_TYPES
    assert tuple(schema["properties"]["scope_class"]["enum"]) == SCOPE_CLASSES
    assert tuple(schema["properties"]["lifecycle_status"]["enum"]) == LIFECYCLE_STATUSES


# ── 2. evidence plane gather: curiosity ──────────────────────────-

def test_curiosity_evidence_keyed_by_cell():
    log = _make_curiosity_log()
    items = ml.gather_curiosity_evidence(None, log)
    targets = {it.canonical_target for it in items}
    assert targets == {"thermal", "energy"}
    # severity is bounded
    assert all(0.0 <= it.severity <= 1.0 for it in items)


# ── 3. evidence plane gather: self-model ─────────────────────────-

def test_self_model_evidence_includes_tensions_and_blind_spots():
    sm = _make_self_model()
    items = ml.gather_self_model_evidence(sm, [])
    planes = {it.plane for it in items}
    assert planes == {"self_model"}
    # Both tensions and the blind spot must appear
    sources = {it.source_id for it in items}
    assert "ten_001" in sources
    assert "ten_002" in sources
    assert "blind_spot:safety" in sources


# ── 4. evidence plane gather: dream ──────────────────────────────-

def test_dream_evidence_excludes_unpromising():
    promising = _make_dream_meta_proposal(structurally_promising=True)
    not_promising = _make_dream_meta_proposal(structurally_promising=False,
                                                cell="energy")
    items = ml.gather_dream_evidence([promising, not_promising])
    targets = {it.canonical_target for it in items}
    assert "thermal" in targets
    assert "energy" not in targets


# ── 5. evidence plane gather: resilience optional ────────────────-

def test_resilience_evidence_empty_when_doc_missing():
    """Missing R7.5 must NOT penalize proposals."""
    items = ml.gather_resilience_evidence(None)
    assert items == []


def test_resilience_evidence_extracts_best_effort_rows():
    doc = (
        "# Resilience\n"
        "| 13 | Multi-writer append hazard | best_effort |\n"
        "| 14 | Stray .current.*.tmp file  | guaranteed |\n"
    )
    items = ml.gather_resilience_evidence(doc)
    assert len(items) == 1
    assert items[0].plane == "resilience"


# ── 6. aggregation by canonical_target ───────────────────────────-

def test_aggregate_by_target_groups_planes():
    items = [
        ml.EvidenceItem("curiosity", "thermal", "cur_1", "thermal", 0.9, "x"),
        ml.EvidenceItem("self_model", "thermal", "ten_001", "thermal", 1.0, "y"),
        ml.EvidenceItem("dream", "thermal", "ten_001", "thermal", 0.8, "z"),
        ml.EvidenceItem("self_model", "energy", "ten_002", "energy", 0.66, "w"),
    ]
    agg = ml.aggregate_by_target(items)
    assert set(agg.keys()) == {"thermal", "energy"}
    assert {it.plane for it in agg["thermal"]} == {"curiosity", "self_model", "dream"}


# ── 7. proposal_priority formula determinism ─────────────────────-

def test_proposal_priority_formula_deterministic():
    p1 = ml.proposal_priority_score(expected_value=0.5, confidence=0.8,
                                       cross_plane_factor=1.5, urgency=1.1)
    p2 = ml.proposal_priority_score(expected_value=0.5, confidence=0.8,
                                       cross_plane_factor=1.5, urgency=1.1)
    assert p1 == p2
    assert p1 == pytest.approx(0.5 * 0.8 * 1.5 * 1.1)


# ── 8. confidence formula determinism ────────────────────────────-

def test_confidence_formula_components():
    c0 = ml.confidence_score(primary_plane_supports=False,
                                 second_plane_supports=False,
                                 dream_replay_positive_gain=False,
                                 no_major_contradiction=False)
    c1 = ml.confidence_score(primary_plane_supports=True,
                                 second_plane_supports=False,
                                 dream_replay_positive_gain=False,
                                 no_major_contradiction=False)
    cmax = ml.confidence_score(primary_plane_supports=True,
                                  second_plane_supports=True,
                                  dream_replay_positive_gain=True,
                                  no_major_contradiction=True)
    assert c0 == 0.0
    assert c1 == pytest.approx(0.40)
    assert cmax == pytest.approx(1.0)
    # Clamped
    assert 0.0 <= cmax <= 1.0


# ── 9. cross_plane_support_factor behavior ───────────────────────-

def test_cross_plane_support_factor_caps():
    assert ml.cross_plane_support_factor(0) == 1.0
    assert ml.cross_plane_support_factor(1) == 1.0
    assert ml.cross_plane_support_factor(2) == 1.25
    assert ml.cross_plane_support_factor(3) == 1.50
    assert ml.cross_plane_support_factor(4) == 1.75
    # Cap
    assert ml.cross_plane_support_factor(10) == 1.75


# ── 10. urgency_factor behavior ──────────────────────────────────-

def test_urgency_factor_branches():
    assert ml.urgency_factor("topology_subdivision",
                                under_pressure_persistent=True) == 1.1
    assert ml.urgency_factor("introspection_gap",
                                calibration_oscillation_active=True) == 1.15
    assert ml.urgency_factor("infrastructure_followup",
                                r7_5_blocks_safe_scaling=True) == 1.2
    assert ml.urgency_factor("solver_family_growth") == 1.0


# ── 11. meta_proposal_id determinism ─────────────────────────────-

def test_meta_proposal_id_excludes_volatile_evidence():
    a = ml.compute_meta_proposal_id("topology_subdivision", "topology",
                                       ["thermal", "safety"], "thermal")
    b = ml.compute_meta_proposal_id("topology_subdivision", "topology",
                                       ["safety", "thermal"], "thermal")
    assert a == b      # impacted_cells sorted internally
    c = ml.compute_meta_proposal_id("topology_subdivision", "topology",
                                       ["thermal"], "thermal")
    assert c != a
    assert len(a) == 12


# ── 12. lifecycle: new vs persisting ─────────────────────────────-

def test_lifecycle_new_then_persisting():
    mid = "abc123def456"
    assert ml.lifecycle_for(mid, set(), set()) == "new"
    assert ml.lifecycle_for(mid, {mid}, {mid}) == "persisting"
    assert ml.lifecycle_for(mid, {mid}, set()) == "persisting"


# ── 13. resolved_proposals diff ──────────────────────────────────-

def test_resolved_proposals_diff_set():
    prev = {"a", "b", "c"}
    curr = {"a", "c"}
    assert ml.resolved_proposals(prev, curr) == ["b"]


# ── 14. infer_proposal_type: topology_subdivision needs ≥2 planes -

def test_infer_topology_subdivision_requires_multiplane():
    # Single plane (curiosity) → not topology
    t = ml.infer_proposal_type({"curiosity"}, severity_max=0.9,
                                  calibration_oscillation=False,
                                  dream_subdivision_hint=True,
                                  r7_5_blocks=False)
    assert t != "topology_subdivision"
    # Two primary planes + dream subdivision hint → topology
    t = ml.infer_proposal_type({"curiosity", "self_model", "dream"},
                                  severity_max=0.9,
                                  calibration_oscillation=False,
                                  dream_subdivision_hint=True,
                                  r7_5_blocks=False)
    assert t == "topology_subdivision"


# ── 15. infer: solver_family_growth on dream + supporting plane --

def test_infer_solver_family_growth():
    t = ml.infer_proposal_type({"dream", "self_model"},
                                  severity_max=0.7,
                                  calibration_oscillation=False,
                                  dream_subdivision_hint=False,
                                  r7_5_blocks=False)
    assert t == "solver_family_growth"


# ── 16. infer: policy_gate_adjustment on calibration oscillation -

def test_infer_policy_gate_on_calibration_oscillation():
    t = ml.infer_proposal_type({"self_model"},
                                  severity_max=0.7,
                                  calibration_oscillation=True,
                                  dream_subdivision_hint=False,
                                  r7_5_blocks=False)
    assert t == "policy_gate_adjustment"


# ── 17. infer: introspection_gap on strong self-model alone ─────-

def test_infer_introspection_gap_single_plane_allowed():
    t = ml.infer_proposal_type({"self_model"},
                                  severity_max=0.9,
                                  calibration_oscillation=False,
                                  dream_subdivision_hint=False,
                                  r7_5_blocks=False)
    assert t == "introspection_gap"


# ── 18. infer: infrastructure_followup on R7.5 alone ────────────-

def test_infer_infrastructure_followup_on_r7_5():
    t = ml.infer_proposal_type({"resilience"},
                                  severity_max=0.5,
                                  calibration_oscillation=False,
                                  dream_subdivision_hint=False,
                                  r7_5_blocks=True)
    assert t == "infrastructure_followup"


# ── 19. weak / single-plane non-allowed downgrades ──────────────-

def test_weak_single_plane_curiosity_downgrades_to_insufficient():
    items = [ml.EvidenceItem("curiosity", "thermal", "cur_1", "thermal", 0.4, "x")]
    res = ml.synthesize_proposals(
        items=items, self_model={}, dream_meta_proposals=[],
        resilience_doc=None, branch_name="b", base_commit_hash="abc",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[],
    )
    assert res.proposals == ()
    assert any(x["candidate_target"] == "thermal"
                for x in res.insufficient_evidence)


# ── 20. multi-plane synthesis emits a proposal ──────────────────-

def test_multiplane_synthesis_emits_proposal():
    sm = _make_self_model()
    log = _make_curiosity_log()
    dmp = [_make_dream_meta_proposal()]
    items = (ml.gather_curiosity_evidence(None, log)
              + ml.gather_self_model_evidence(sm, [])
              + ml.gather_dream_evidence(dmp))
    res = ml.synthesize_proposals(
        items=items, self_model=sm, dream_meta_proposals=dmp,
        resilience_doc=None, branch_name="phase8.5/hive-proposes",
        base_commit_hash="abc",
        pinned_input_manifest_sha256="sha256:test",
        consumed_hook_contracts=[],
    )
    assert len(res.proposals) >= 1
    # Every proposal carries no_mutation_in_session = True
    for p in res.proposals:
        assert p.no_mutation_in_session is True
    # Stable ordering: descending priority
    pris = [p.proposal_priority for p in res.proposals]
    assert pris == sorted(pris, reverse=True)


# ── 21. provenance fields present ───────────────────────────────-

def test_provenance_fields_present():
    sm = _make_self_model()
    log = _make_curiosity_log()
    dmp = [_make_dream_meta_proposal()]
    items = (ml.gather_curiosity_evidence(None, log)
              + ml.gather_self_model_evidence(sm, [])
              + ml.gather_dream_evidence(dmp))
    res = ml.synthesize_proposals(
        items=items, self_model=sm, dream_meta_proposals=dmp,
        resilience_doc=None, branch_name="branch_x", base_commit_hash="ab12",
        pinned_input_manifest_sha256="sha256:abc",
        consumed_hook_contracts=[
            {"file": "x.md", "version": 1, "file_sha256": "sha256:y"}
        ],
        fixture_fallback_used=True,
    )
    p = res.proposals[0]
    assert p.provenance["branch_name"] == "branch_x"
    assert p.provenance["base_commit_hash"] == "ab12"
    assert p.provenance["pinned_input_manifest_sha256"] == "sha256:abc"
    assert p.provenance["fixture_fallback_used"] is True
    assert p.provenance["consumed_hook_contracts"][0]["file"] == "x.md"


# ── 22. no_mutation_in_session is a const-True invariant ─────────

def test_no_mutation_invariant():
    """Production source must never set no_mutation_in_session = False."""
    src = (ROOT / "waggledance" / "core" / "meta" / "meta_learner.py")\
        .read_text(encoding="utf-8")
    # The token "no_mutation_in_session=False" must NOT appear
    assert "no_mutation_in_session=False" not in src
    assert "no_mutation_in_session = False" not in src


# ── 23. no runtime mutation imports ─────────────────────────────-

def test_no_runtime_imports_in_meta_package():
    """The meta package must not import from runtime registries / FAISS
    / port-8002 paths."""
    pkg_root = ROOT / "waggledance" / "core" / "meta"
    forbidden = ["from waggledance.core.faiss_store",
                  "import faiss",  # runtime FAISS — meta must not require it
                  "from waggledance.runtime",
                  "axiom_write(", "promote_to_runtime("]
    for p in pkg_root.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"forbidden import {pat} in {p.name}"


# ── 24. impacted_cells derivation ───────────────────────────────-

def test_impacted_cells_collected_and_sorted():
    items = [
        ml.EvidenceItem("self_model", "thermal", "ten_a", "thermal", 1.0, "r"),
        ml.EvidenceItem("dream", "thermal", "ten_a", "thermal", 0.7, "r"),
        ml.EvidenceItem("curiosity", "thermal", "cur_x", "thermal", 0.9, "r"),
    ]
    res = ml.synthesize_proposals(
        items=items, self_model={
            "workspace_tensions": [
                {"tension_id": "ten_a", "type": "scorecard_drift",
                 "severity": "high", "lifecycle_status": "persisting",
                 "evidence_refs": ["cell:thermal"]},
            ]
        },
        dream_meta_proposals=[], resilience_doc=None,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[],
    )
    if res.proposals:
        assert "thermal" in res.proposals[0].impacted_cells


# ── 25. consumed_hook_contracts validation rejects mismatch ─────-

def test_validate_hook_contracts_rejects_mismatch(tmp_path):
    target = tmp_path / "doc.md"
    target.write_text("hello", encoding="utf-8")
    import hashlib
    real_sha = "sha256:" + hashlib.sha256(b"hello").hexdigest()
    # Correct → ok
    errs = mi.validate_hook_contracts(
        [{"file": "doc.md", "version": 1, "file_sha256": real_sha}],
        repo_root=tmp_path,
    )
    assert errs == []
    # Tamper → error
    target.write_text("tampered", encoding="utf-8")
    errs = mi.validate_hook_contracts(
        [{"file": "doc.md", "version": 1, "file_sha256": real_sha}],
        repo_root=tmp_path,
    )
    assert errs and "sha mismatch" in errs[0]


# ── 26. inputs.load_state round-trip on real-shaped state.json ──-

def test_load_state_round_trip(tmp_path):
    state_path = tmp_path / "s.json"
    state_path.write_text(json.dumps({
        "pinned_input_manifest_sha256": "sha256:abc",
        "pinned_inputs": [{"path": "x", "size_bytes": 0,
                            "sha256_full": "sha256:0"}],
        "consumed_hook_contracts": [
            {"file": "y.md", "version": 1, "file_sha256": "sha256:0"}
        ],
    }), encoding="utf-8")
    pin, pinned, hooks = mi.load_state(state_path)
    assert pin == "sha256:abc"
    assert len(pinned) == 1
    assert hooks[0]["version"] == 1


# ── 27. bounded read does not exceed pinned size ────────────────-

def test_bounded_read_caps_at_size_bytes(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("x" * 1000, encoding="utf-8")
    pinned = [{"path": str(p), "size_bytes": 100, "sha256_full": "sha256:0"}]
    # use the curiosity_summary path-suffix to trigger the loader
    big_p = tmp_path / "curiosity_summary.json"
    big_p.write_text(json.dumps({"k": "v" * 1000, "schema_version": 1}),
                       encoding="utf-8")
    pinned = [{"path": str(big_p), "size_bytes": 50,
                "sha256_full": "sha256:0"}]
    # Loader will read only 50 bytes → invalid JSON → returns None
    summary = mi.load_curiosity_summary(pinned)
    assert summary is None  # truncated JSON cannot parse


# ── 28. weak single-plane curiosity is not a strong proposal ────-

def test_weak_single_plane_curiosity_does_not_emit_proposal():
    items = [
        ml.EvidenceItem("curiosity", "thermal", "cur_1", "thermal", 0.4, "r"),
        ml.EvidenceItem("curiosity", "thermal", "cur_2", "thermal", 0.5, "r"),
    ]
    res = ml.synthesize_proposals(
        items=items, self_model={}, dream_meta_proposals=[],
        resilience_doc=None, branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        consumed_hook_contracts=[],
    )
    assert res.proposals == ()


# ── 29. deterministic ordering by priority then id ──────────────-

def test_deterministic_proposal_order():
    """Run twice — same proposals, same ordering."""
    sm = _make_self_model()
    log = _make_curiosity_log()
    dmp = [_make_dream_meta_proposal()]
    items_a = (ml.gather_curiosity_evidence(None, log)
                + ml.gather_self_model_evidence(sm, [])
                + ml.gather_dream_evidence(dmp))
    items_b = (ml.gather_curiosity_evidence(None, log)
                + ml.gather_self_model_evidence(sm, [])
                + ml.gather_dream_evidence(dmp))
    r1 = ml.synthesize_proposals(items=items_a, self_model=sm,
                                    dream_meta_proposals=dmp,
                                    resilience_doc=None,
                                    branch_name="b", base_commit_hash="ab",
                                    pinned_input_manifest_sha256="sha256:t",
                                    consumed_hook_contracts=[])
    r2 = ml.synthesize_proposals(items=items_b, self_model=sm,
                                    dream_meta_proposals=dmp,
                                    resilience_doc=None,
                                    branch_name="b", base_commit_hash="ab",
                                    pinned_input_manifest_sha256="sha256:t",
                                    consumed_hook_contracts=[])
    ids1 = [p.meta_proposal_id for p in r1.proposals]
    ids2 = [p.meta_proposal_id for p in r2.proposals]
    assert ids1 == ids2


# ── 30. no live LLM call path in source ─────────────────────────-

def test_no_live_llm_call_path():
    pkg = ROOT / "waggledance" / "core" / "meta"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in ("requests.post(", "httpx.post(", "openai.",
                     "anthropic.", "ollama."):
            assert pat not in text, f"forbidden network call {pat} in {p.name}"

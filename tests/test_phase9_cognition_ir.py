# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §G Cognition IR + Capsule Registry."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.capsules import (
    CAPSULE_KINDS,
    capsule_registry as cr,
    capsule_resolver as crs,
)
from waggledance.core.ir import (
    IR_TYPES,
    LIFECYCLE_STATUSES,
    PROMOTION_STATES,
    cognition_ir as ir,
    ir_compatibility as irc,
    ir_translator as irt,
    ir_validator as irv,
)
from waggledance.core.ir.adapters import (
    from_curiosity as ad_cur,
    from_dream as ad_dream,
    from_hive as ad_hive,
    from_self_model as ad_sm,
)


def _prov() -> ir.Provenance:
    return ir.Provenance(
        branch_name="phase9/autonomy-fabric",
        base_commit_hash="ddb0821",
        pinned_input_manifest_sha256="sha256:test",
        produced_by="test",
        source_session="phase9_ir",
    )


# ═══════════════════ schema enums match constants ══════════════════

def test_ir_types_match_schema():
    schema = json.loads((ROOT / "schemas" / "cognition_ir.schema.json")
                          .read_text(encoding="utf-8"))
    schema_types = tuple(schema["properties"]["ir_type"]["enum"])
    assert schema_types == IR_TYPES


def test_lifecycle_statuses_match_schema():
    schema = json.loads((ROOT / "schemas" / "cognition_ir.schema.json")
                          .read_text(encoding="utf-8"))
    schema_st = tuple(schema["properties"]["lifecycle_status"]["enum"])
    assert schema_st == LIFECYCLE_STATUSES


def test_promotion_states_match_schema():
    schema = json.loads((ROOT / "schemas" / "cognition_ir.schema.json")
                          .read_text(encoding="utf-8"))
    schema_ps = tuple(schema["properties"]["promotion_state"]["enum"])
    assert schema_ps == PROMOTION_STATES


def test_capsule_kinds_match_schema():
    schema = json.loads((ROOT / "schemas" / "capsule_manifest.schema.json")
                          .read_text(encoding="utf-8"))
    schema_kinds = tuple(schema["properties"]["capsule_kind"]["enum"])
    assert schema_kinds == CAPSULE_KINDS


# ═══════════════════ make_ir + ir_id determinism ═══════════════════

def test_make_ir_curiosity_works():
    obj = ir.make_ir(
        ir_type="curiosity",
        payload={"candidate_cell": "thermal", "curiosity_id": "cur_1",
                  "suspected_gap_type": "missing_solver"},
        provenance=_prov(),
    )
    assert obj.ir_type == "curiosity"
    assert len(obj.ir_id) == 12


def test_make_ir_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown ir_type"):
        ir.make_ir(ir_type="bogus_type", payload={},
                      provenance=_prov())


def test_ir_id_excludes_volatile_payload_fields():
    """Two curiosity objects with same identity-keys but different
    rationale/scoring fields should share ir_id."""
    a = ir.make_ir(
        ir_type="curiosity",
        payload={"candidate_cell": "thermal", "curiosity_id": "cur_1",
                  "suspected_gap_type": "missing_solver",
                  "estimated_value": 9.5, "rationale": "long text A"},
        provenance=_prov(),
    )
    b = ir.make_ir(
        ir_type="curiosity",
        payload={"candidate_cell": "thermal", "curiosity_id": "cur_1",
                  "suspected_gap_type": "missing_solver",
                  "estimated_value": 1.2, "rationale": "different text B"},
        provenance=_prov(),
    )
    assert a.ir_id == b.ir_id


def test_ir_id_changes_on_capsule_context():
    a = ir.make_ir(ir_type="curiosity",
                      payload={"candidate_cell": "thermal",
                                "curiosity_id": "cur_1",
                                "suspected_gap_type": "missing_solver"},
                      provenance=_prov())
    b = ir.make_ir(ir_type="curiosity",
                      payload={"candidate_cell": "thermal",
                                "curiosity_id": "cur_1",
                                "suspected_gap_type": "missing_solver"},
                      provenance=_prov(),
                      capsule_context="factory_v1")
    assert a.ir_id != b.ir_id


# ═══════════════════ round-trip translation ═══════════════════════-

def test_round_trip_preserves_all_fields():
    orig = ir.make_ir(
        ir_type="meta_proposal",
        payload={"proposal_type": "solver_family_growth",
                  "scope_class": "solver_library",
                  "canonical_target": "thermal"},
        provenance=_prov(),
        risk="medium", promotion_state="review_ready",
        evidence_refs=("ten_001", "cur_thermal_1"),
        dependencies=(ir.Dependency(depends_on_ir_id="abc"*4,
                                         relation="supports"),),
    )
    rt = irt.round_trip(orig)
    assert rt.ir_id == orig.ir_id
    assert rt.ir_type == orig.ir_type
    assert rt.risk == orig.risk
    assert rt.promotion_state == orig.promotion_state
    assert tuple(rt.evidence_refs) == tuple(orig.evidence_refs)
    assert tuple(rt.dependencies) == tuple(orig.dependencies)


def test_canonical_json_byte_stable():
    obj = ir.make_ir(
        ir_type="curiosity",
        payload={"candidate_cell": "thermal", "curiosity_id": "cur_1",
                  "suspected_gap_type": "missing_solver"},
        provenance=_prov(),
    )
    j1 = irt.to_canonical_json(obj)
    j2 = irt.to_canonical_json(obj)
    assert j1 == j2


# ═══════════════════ validator ═════════════════════════════════════

def test_validator_accepts_clean_object():
    obj = ir.make_ir(ir_type="curiosity",
                        payload={"candidate_cell": "thermal",
                                  "curiosity_id": "cur_1"},
                        provenance=_prov())
    assert irv.validate(obj) == []


def test_validator_rejects_unknown_dependency_relation():
    """Cannot construct via Dependency directly; verify make_ir
    propagation by passing a hand-rolled bad relation through to the
    Dependency dataclass."""
    with pytest.raises(ValueError, match="unknown dependency relation"):
        ir.Dependency(depends_on_ir_id="abc", relation="invent_relation")


def test_validator_strict_raises():
    """Build an obj with invalid ir_id length and verify strict validation raises."""
    obj = ir.IRObject(
        schema_version=1,
        ir_id="too_short",   # ← invalid: not 12 hex chars
        ir_type="curiosity",
        ir_compat_version=1,
        lifecycle_status="new", risk="low",
        promotion_state="candidate",
        evidence_refs=(), dependencies=(),
        provenance=_prov(),
        capsule_context="neutral_v1",
        payload={},
    )
    with pytest.raises(irv.IRValidationError):
        irv.validate_strict(obj)


def test_dependency_cycle_detection():
    a = ir.make_ir(ir_type="meta_proposal",
                      payload={"proposal_type": "x", "scope_class": "x",
                                "canonical_target": "A"},
                      provenance=_prov())
    b = ir.make_ir(ir_type="meta_proposal",
                      payload={"proposal_type": "x", "scope_class": "x",
                                "canonical_target": "B"},
                      provenance=_prov(),
                      dependencies=(ir.Dependency(
                          depends_on_ir_id=a.ir_id, relation="supports"),))
    a2 = ir.IRObject(
        schema_version=a.schema_version, ir_id=a.ir_id,
        ir_type=a.ir_type, ir_compat_version=a.ir_compat_version,
        lifecycle_status=a.lifecycle_status, risk=a.risk,
        promotion_state=a.promotion_state,
        evidence_refs=a.evidence_refs,
        dependencies=(ir.Dependency(depends_on_ir_id=b.ir_id,
                                         relation="supports"),),
        provenance=a.provenance,
        capsule_context=a.capsule_context, payload=a.payload,
    )
    cycles = irv.detect_dependency_cycles([a2, b])
    assert cycles, "expected at least one cycle detected"


# ═══════════════════ compatibility ═════════════════════════════════

def test_is_compatible_default():
    obj = ir.make_ir(ir_type="curiosity",
                        payload={"candidate_cell": "x",
                                  "curiosity_id": "cur_x"},
                        provenance=_prov())
    assert irc.is_compatible(obj)


def test_is_compatible_unknown_major_refused():
    bad = ir.IRObject(
        schema_version=1, ir_id="x"*12, ir_type="curiosity",
        ir_compat_version=999,   # future major
        lifecycle_status="new", risk="low",
        promotion_state="candidate",
        evidence_refs=(), dependencies=(),
        provenance=_prov(), capsule_context="neutral_v1",
        payload={},
    )
    assert not irc.is_compatible(bad)
    assert irc.reason_incompatible(bad) is not None


# ═══════════════════ adapters ══════════════════════════════════════

def test_curiosity_adapter():
    log = [
        {"curiosity_id": "cur_1", "candidate_cell": "thermal",
         "estimated_value": 9.5, "suspected_gap_type": "missing_solver",
         "count": 12, "fallback_rate": 0.8},
    ]
    objs = ad_cur.adapt_curiosity_log(log, _prov())
    assert len(objs) == 1
    assert objs[0].ir_type == "curiosity"
    assert objs[0].risk == "high"   # estimated_value >= 8


def test_self_model_adapter_emits_tensions_and_blind_spots():
    snap = {
        "workspace_tensions": [
            {"tension_id": "ten_001", "type": "scorecard_drift",
             "claim": "thermal score = 0.92", "severity": "high",
             "lifecycle_status": "persisting",
             "evidence_refs": ["cell:thermal"]},
        ],
        "blind_spots": [
            {"domain": "safety", "severity": "high",
             "detectors": ["coverage_negative_space"]},
        ],
    }
    objs = ad_sm.adapt_self_model(snap, _prov())
    types = {o.ir_type for o in objs}
    assert types == {"tension", "blind_spot"}


def test_dream_adapters_handle_curriculum_and_meta():
    curr = {
        "nights": [
            {"night_index": 1, "mode": "base_solver_growth",
             "uncertainty": "medium",
             "target_items": [
                 {"source_id": "ten_001", "candidate_cell": "thermal"},
             ]},
        ],
    }
    objs = ad_dream.adapt_dream_curriculum(curr, _prov())
    assert any(o.ir_type == "dream_target" for o in objs)

    mp = {
        "structurally_promising": True,
        "selected_proposal": {
            "solver_name": "thermal_dream_estimator",
            "cell_id": "thermal",
            "solver_hash": "sha256:abc",
            "proposal_id": "p1",
        },
        "expected_value_of_merging": 0.4,
        "confidence": 0.8,
        "source_tension_ids": ["ten_001"],
    }
    objs = ad_dream.adapt_dream_meta_proposal(mp, _prov())
    types = {o.ir_type for o in objs}
    assert types == {"shadow_candidate", "meta_proposal"}


def test_hive_adapter_emits_meta_proposals_and_review_candidates():
    hp = {
        "proposals": [
            {"meta_proposal_id": "abc123def456",
             "proposal_type": "introspection_gap",
             "scope_class": "introspection",
             "impacted_cells": ["math"],
             "proposal_priority": 0.6,
             "confidence": 0.6,
             "canonical_target": "math",
             "source_tension_ids": [],
             "lifecycle_status": "new"},
        ],
    }
    objs = ad_hive.adapt_hive_proposals(hp, _prov())
    assert len(objs) == 1
    assert objs[0].ir_type == "meta_proposal"

    bundle = {
        "proposals": [
            {"meta_proposal_id": "abc123def456",
             "recommended_next_human_action": "review_for_future_PR",
             "proposal_priority": 0.6, "confidence": 0.6},
        ],
    }
    objs = ad_hive.adapt_review_bundle(bundle, _prov())
    assert objs[0].ir_type == "review_candidate"


# ═══════════════════ capsule registry + resolver ══════════════════

def test_make_manifest_rejects_unknown_kind():
    with pytest.raises(cr.CapsuleValidationError):
        cr.make_manifest(capsule_id="x", capsule_kind="bogus_kind")


def test_make_manifest_rejects_unknown_lane():
    with pytest.raises(cr.CapsuleValidationError):
        cr.make_manifest(capsule_id="x", capsule_kind="neutral_v1",
                            allowed_lanes=("bogus_lane",))


def test_make_manifest_invariants_are_const_true():
    m = cr.make_manifest(capsule_id="neutral_v1",
                            capsule_kind="neutral_v1")
    assert m.domain_neutral_core_required is True
    assert m.blast_radius_isolated is True


def test_registry_register_and_get():
    reg = cr.CapsuleRegistry()
    m = cr.make_manifest(capsule_id="neutral_v1",
                            capsule_kind="neutral_v1")
    reg.register(m)
    assert reg.get("neutral_v1") is m
    assert reg.list_ids() == ["neutral_v1"]


def test_registry_refuses_downgrade_or_replay():
    reg = cr.CapsuleRegistry()
    m1 = cr.make_manifest(capsule_id="x", capsule_kind="neutral_v1",
                              version=2)
    reg.register(m1)
    m_old = cr.make_manifest(capsule_id="x", capsule_kind="neutral_v1",
                                  version=1)
    with pytest.raises(cr.CapsuleValidationError):
        reg.register(m_old)


def test_resolver_falls_back_to_neutral_v1():
    reg = cr.CapsuleRegistry()
    reg.register(cr.make_manifest(capsule_id="neutral_v1",
                                       capsule_kind="neutral_v1"))
    m = crs.resolve_capsule(reg, "factory_v1")
    assert m is not None
    assert m.capsule_id == "neutral_v1"


def test_resolver_returns_direct_match():
    reg = cr.CapsuleRegistry()
    reg.register(cr.make_manifest(capsule_id="factory_v1",
                                       capsule_kind="factory_v1"))
    m = crs.resolve_capsule(reg, "factory_v1")
    assert m is not None
    assert m.capsule_id == "factory_v1"


def test_blast_radius_assert_blocks_cross_capsule():
    with pytest.raises(crs.BlastRadiusViolation):
        crs.assert_no_cross_capsule_leak(
            source_capsule_context="factory_v1",
            target_capsule_context="personal_v1",
            allow_neutral=True,
        )


def test_blast_radius_allows_neutral_substrate():
    crs.assert_no_cross_capsule_leak(
        source_capsule_context="factory_v1",
        target_capsule_context="neutral_v1",
        allow_neutral=True,
    )
    crs.assert_no_cross_capsule_leak(
        source_capsule_context="neutral_v1",
        target_capsule_context="factory_v1",
        allow_neutral=True,
    )


def test_lane_filtering():
    m = cr.make_manifest(capsule_id="x", capsule_kind="neutral_v1",
                            allowed_lanes=("provider_plane",
                                            "ingestion"))
    assert crs.is_lane_allowed(m, "provider_plane") is True
    assert crs.is_lane_allowed(m, "builder_lane") is False


def test_plane_subscription():
    m = cr.make_manifest(capsule_id="x", capsule_kind="neutral_v1",
                            evidence_planes_subscribed=("curiosity",
                                                         "self_model"))
    assert crs.is_plane_subscribed(m, "curiosity") is True
    assert crs.is_plane_subscribed(m, "dream") is False


# ═══════════════════ source safety (Phase G modules) ══════════════

def test_phase_g_core_source_safety():
    """Core modules must be strict — no domain metaphors. Adapters
    are exempt because they consume legacy-named upstream session
    outputs (Session D's 'hive_proposes', etc.) per Prompt_1_Master
    §DOMAIN-NEUTRALITY exception for legacy/product names."""
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.post(",
                  "axiom_write(", "promote_to_runtime("]
    forbidden_metaphors = ["bee ", "honeycomb ", "swarm ", "pdam",
                             "beverage"]
    core_files = [
        ROOT / "waggledance" / "core" / "ir" / "cognition_ir.py",
        ROOT / "waggledance" / "core" / "ir" / "ir_validator.py",
        ROOT / "waggledance" / "core" / "ir" / "ir_translator.py",
        ROOT / "waggledance" / "core" / "ir" / "ir_compatibility.py",
        ROOT / "waggledance" / "core" / "ir" / "__init__.py",
        ROOT / "waggledance" / "core" / "capsules" / "capsule_registry.py",
        ROOT / "waggledance" / "core" / "capsules" / "capsule_resolver.py",
        ROOT / "waggledance" / "core" / "capsules" / "__init__.py",
    ]
    for p in core_files:
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"
        text_l = text.lower()
        for pat in forbidden_metaphors:
            assert pat not in text_l, f"{p.name}: domain metaphor {pat}"


# ═══════════════════ adapter source safety ════════════════════════-

def test_adapters_do_not_import_runtime():
    pkg = ROOT / "waggledance" / "core" / "ir" / "adapters"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        # Must only import from our package
        for pat in ("import faiss", "from waggledance.runtime",
                     "from waggledance.adapters.llm",
                     "ollama.", "openai.", "anthropic."):
            assert pat not in text, f"{p.name}: {pat}"

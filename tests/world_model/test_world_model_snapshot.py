# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.world_model.world_model_snapshot.

world_model_snapshot is the frozen, deterministic snapshot of
external knowledge at a point in time (Phase 9 §I). It defines the
data model that the rest of world_model_*.py operates on:
ExternalFact, CausalRelation, Prediction, WorldModelSnapshot, plus
the snapshot constructor make_snapshot. The dataclass invariants
(kind/horizon/confidence/strength validation, cause != effect
guard) are the boundary that prevents corrupt facts from entering
the audit trail.

Closes the last zero-direct-coverage gap in
waggledance/core/world_model/. Pairs with #152/#153/#157/#158/#160.

Pinned invariants:

- ExternalFact.__post_init__:
  - rejects kind not in FACT_KINDS.
  - rejects confidence outside [0, 1].
- CausalRelation.__post_init__:
  - rejects strength outside [0, 1].
  - rejects cause_fact_id == effect_fact_id (no self-loops).
- Prediction.__post_init__: (additional pinning to PR #158 +
  prediction_calibrator coverage)
  - rejects horizon not in PREDICTION_HORIZONS.
  - rejects confidence outside [0, 1].
- to_dict round-trips for every dataclass.
- compute_snapshot_id is structural: deterministic on
  (sorted fact_ids, sorted "cause->effect" strings,
  sorted prediction_ids). Same structure -> same id even if
  ts_iso / confidence / strength differ.
- compute_snapshot_id format: 12-char hex prefix.
- make_snapshot:
  - sets schema_version = WORLD_MODEL_SCHEMA_VERSION.
  - computes snapshot_id from the structural inputs.
  - aggregates uncertainty_summary:
    - facts_with_low_confidence = count of facts with conf < 0.5.
    - predictions_pending_eval = count of preds with actual_value None.
    - drift_alerts_active = 0 (placeholder per current schema).
  - calibration_per_dimension defaults to {} when not provided.
  - produced_by defaults to "build_world_model_snapshot".
- to_canonical_json: indent=2, sort_keys=True (audit-stable).
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from waggledance.core.world_model import (
    PREDICTION_HORIZONS,
    WORLD_MODEL_SCHEMA_VERSION,
)
from waggledance.core.world_model.world_model_snapshot import (
    CausalRelation,
    ExternalFact,
    Prediction,
    WorldModelSnapshot,
    compute_snapshot_id,
    make_snapshot,
    to_canonical_json,
)


# --- helpers -------------------------------------------------------

def _fact(fid: str = "F-1", *, kind: str = "observation",
              conf: float = 0.5) -> ExternalFact:
    return ExternalFact(
        fact_id=fid, kind=kind, claim=f"claim {fid}",
        confidence=conf, source_refs=("ref-x",),
    )


def _cause(c: str = "F-1", e: str = "F-2",
                strength: float = 0.5) -> CausalRelation:
    return CausalRelation(
        cause_fact_id=c, effect_fact_id=e, strength=strength,
    )


def _prediction(pid: str = "P-1", *,
                    horizon: str = "short_term",
                    conf: float = 0.5,
                    actual=None) -> Prediction:
    return Prediction(
        prediction_id=pid, claim=f"claim {pid}",
        predicted_value=10.0, confidence=conf,
        horizon=horizon, actual_value=actual,
    )


# --- ExternalFact.__post_init__ validation -----------------------

def test_external_fact_rejects_unknown_kind():
    with pytest.raises(ValueError) as ei:
        ExternalFact(
            fact_id="F", kind="not_a_kind",
            claim="x", confidence=0.5, source_refs=(),
        )
    assert "unknown fact kind" in str(ei.value)


@pytest.mark.parametrize("bad_conf", [-0.01, -1.0, 1.01, 2.0])
def test_external_fact_rejects_confidence_outside_unit_interval(bad_conf):
    with pytest.raises(ValueError) as ei:
        ExternalFact(
            fact_id="F", kind="observation",
            claim="x", confidence=bad_conf, source_refs=(),
        )
    assert "confidence must be in [0,1]" in str(ei.value)


@pytest.mark.parametrize("ok_conf", [0.0, 0.5, 1.0])
def test_external_fact_accepts_unit_interval_boundaries(ok_conf):
    """Boundaries 0.0 and 1.0 are INCLUSIVE."""
    f = ExternalFact(
        fact_id="F", kind="observation",
        claim="x", confidence=ok_conf, source_refs=(),
    )
    assert f.confidence == ok_conf


def test_external_fact_to_dict_round_trips_fields():
    f = ExternalFact(
        fact_id="F-1", kind="report", claim="X",
        confidence=0.7, source_refs=("a", "b"),
        ts_iso="2026-05-09T00:00:00Z",
    )
    d = f.to_dict()
    assert d == {
        "fact_id": "F-1", "kind": "report", "claim": "X",
        "confidence": 0.7,
        "source_refs": ["a", "b"],  # tuple -> list
        "ts_iso": "2026-05-09T00:00:00Z",
    }


# --- CausalRelation.__post_init__ validation ---------------------

@pytest.mark.parametrize("bad_strength", [-0.01, -1.0, 1.01, 2.0])
def test_causal_relation_rejects_strength_outside_unit_interval(bad_strength):
    with pytest.raises(ValueError) as ei:
        CausalRelation(cause_fact_id="A", effect_fact_id="B",
                        strength=bad_strength)
    assert "strength must be in [0,1]" in str(ei.value)


def test_causal_relation_rejects_self_loop():
    """cause == effect is forbidden — a fact cannot cause itself.
    Drift here would corrupt the causal graph with cycles."""
    with pytest.raises(ValueError) as ei:
        CausalRelation(cause_fact_id="A", effect_fact_id="A",
                        strength=0.5)
    assert "must differ" in str(ei.value)


def test_causal_relation_accepts_distinct_endpoints():
    r = CausalRelation(cause_fact_id="A", effect_fact_id="B",
                        strength=0.7)
    assert r.cause_fact_id == "A"
    assert r.effect_fact_id == "B"
    assert r.strength == 0.7


def test_causal_relation_to_dict_carries_evidence_refs():
    r = CausalRelation(
        cause_fact_id="A", effect_fact_id="B",
        strength=0.5, evidence_refs=("e1", "e2"),
    )
    d = r.to_dict()
    assert d["evidence_refs"] == ["e1", "e2"]


# --- Prediction.__post_init__ validation -------------------------
# (Complements PR #158's tests — pinning the snapshot-level
# invariants explicitly so a future move/duplication of validation
# is caught.)

def test_prediction_rejects_unknown_horizon():
    with pytest.raises(ValueError) as ei:
        Prediction(
            prediction_id="P", claim="x", predicted_value=0.0,
            confidence=0.5, horizon="not_a_horizon",
        )
    assert "unknown horizon" in str(ei.value)


@pytest.mark.parametrize("bad_conf", [-0.01, 1.01])
def test_prediction_rejects_confidence_outside_unit_interval(bad_conf):
    with pytest.raises(ValueError) as ei:
        Prediction(
            prediction_id="P", claim="x", predicted_value=0.0,
            confidence=bad_conf, horizon="short_term",
        )
    assert "confidence must be in [0,1]" in str(ei.value)


def test_prediction_to_dict_carries_all_fields():
    p = Prediction(
        prediction_id="P", claim="X", predicted_value=20.5,
        confidence=0.6, horizon="medium_term", predicted_unit="C",
        based_on_facts=("f1",), evaluated_at_iso="2026-05-09T00:00:00Z",
        actual_value=21.0, calibration_error=0.5,
    )
    d = p.to_dict()
    assert d["prediction_id"] == "P"
    assert d["predicted_value"] == 20.5
    assert d["predicted_unit"] == "C"
    assert d["horizon"] == "medium_term"
    assert d["based_on_facts"] == ["f1"]
    assert d["actual_value"] == 21.0
    assert d["calibration_error"] == 0.5


# --- compute_snapshot_id structural determinism ------------------

def test_compute_snapshot_id_deterministic_on_same_structure():
    facts = [_fact("F-1"), _fact("F-2")]
    causes = [_cause("F-1", "F-2")]
    preds = [_prediction("P-1")]
    a = compute_snapshot_id(
        external_facts=facts, causal_relations=causes,
        predictions=preds,
    )
    b = compute_snapshot_id(
        external_facts=facts, causal_relations=causes,
        predictions=preds,
    )
    assert a == b


def test_compute_snapshot_id_invariant_to_input_order():
    """The id is computed from sorted ids, so caller order MUST
    NOT affect the id. A drifted impl that hashes raw order would
    miss the dedup."""
    facts1 = [_fact("F-2"), _fact("F-1"), _fact("F-3")]
    facts2 = [_fact("F-3"), _fact("F-2"), _fact("F-1")]
    a = compute_snapshot_id(
        external_facts=facts1, causal_relations=[],
        predictions=[],
    )
    b = compute_snapshot_id(
        external_facts=facts2, causal_relations=[],
        predictions=[],
    )
    assert a == b


def test_compute_snapshot_id_invariant_to_confidence_drift():
    """Two snapshots with the same structural ids but different
    fact confidences MUST produce the same snapshot_id — drift
    is tracked separately by world_model_delta. If confidence
    affected snapshot_id, every confidence shift would falsely
    change the snapshot identity."""
    facts_low = [_fact("F-1", conf=0.1), _fact("F-2", conf=0.1)]
    facts_high = [_fact("F-1", conf=0.9), _fact("F-2", conf=0.9)]
    a = compute_snapshot_id(
        external_facts=facts_low, causal_relations=[],
        predictions=[],
    )
    b = compute_snapshot_id(
        external_facts=facts_high, causal_relations=[],
        predictions=[],
    )
    assert a == b


def test_compute_snapshot_id_invariant_to_ts_iso():
    """ts_iso changes between snapshots (the docstring says so);
    structural id MUST NOT include ts."""
    f1_no_ts = _fact("F-1")
    f1_with_ts = ExternalFact(
        fact_id="F-1", kind="observation", claim="claim F-1",
        confidence=0.5, source_refs=("ref-x",),
        ts_iso="2026-05-09T00:00:00Z",
    )
    a = compute_snapshot_id(
        external_facts=[f1_no_ts], causal_relations=[],
        predictions=[],
    )
    b = compute_snapshot_id(
        external_facts=[f1_with_ts], causal_relations=[],
        predictions=[],
    )
    assert a == b


def test_compute_snapshot_id_changes_when_fact_added():
    a = compute_snapshot_id(
        external_facts=[_fact("F-1")], causal_relations=[],
        predictions=[],
    )
    b = compute_snapshot_id(
        external_facts=[_fact("F-1"), _fact("F-2")],
        causal_relations=[], predictions=[],
    )
    assert a != b


def test_compute_snapshot_id_changes_when_cause_added():
    facts = [_fact("F-1"), _fact("F-2")]
    a = compute_snapshot_id(
        external_facts=facts, causal_relations=[],
        predictions=[],
    )
    b = compute_snapshot_id(
        external_facts=facts,
        causal_relations=[_cause("F-1", "F-2")],
        predictions=[],
    )
    assert a != b


def test_compute_snapshot_id_format_is_12_char_hex():
    sid = compute_snapshot_id(
        external_facts=[], causal_relations=[], predictions=[],
    )
    assert len(sid) == 12
    # 12 lowercase hex chars
    int(sid, 16)  # raises if non-hex


# --- make_snapshot ----------------------------------------------

def _provenance() -> dict:
    return {
        "branch_name": "test",
        "base_commit_hash": "deadbeef",
        "pinned_input_manifest_sha256": "f" * 64,
    }


def test_make_snapshot_sets_schema_version():
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        **_provenance(),
    )
    assert s.schema_version == WORLD_MODEL_SCHEMA_VERSION


def test_make_snapshot_computes_snapshot_id_from_structural_inputs():
    """make_snapshot must call compute_snapshot_id internally —
    verify the computed id matches a direct call."""
    facts = [_fact("F-1")]
    expected = compute_snapshot_id(
        external_facts=facts, causal_relations=[],
        predictions=[],
    )
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=facts, causal_relations=[], predictions=[],
        **_provenance(),
    )
    assert s.snapshot_id == expected


def test_make_snapshot_uncertainty_summary_facts_low_confidence():
    """facts_with_low_confidence counts facts strictly < 0.5.
    Pinned: a fact at exactly 0.5 is NOT low (the cutoff is
    strict-less-than)."""
    facts = [
        _fact("F-1", conf=0.1),  # low
        _fact("F-2", conf=0.49),  # low
        _fact("F-3", conf=0.5),   # NOT low (strict <)
        _fact("F-4", conf=0.9),   # NOT low
    ]
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=facts, causal_relations=[], predictions=[],
        **_provenance(),
    )
    assert s.uncertainty_summary["facts_with_low_confidence"] == 2


def test_make_snapshot_uncertainty_summary_predictions_pending_eval():
    """predictions_pending_eval counts predictions with
    actual_value is None."""
    preds = [
        _prediction("P-1", actual=None),    # pending
        _prediction("P-2", actual=10.0),    # evaluated
        _prediction("P-3", actual=None),    # pending
    ]
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=preds,
        **_provenance(),
    )
    assert s.uncertainty_summary["predictions_pending_eval"] == 2


def test_make_snapshot_drift_alerts_active_is_zero_placeholder():
    """drift_alerts_active is currently a hardcoded 0 (the
    detector lives separately and its outputs are not yet wired
    into snapshot summary). Pinned so a future wire-up is loud."""
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        **_provenance(),
    )
    assert s.uncertainty_summary["drift_alerts_active"] == 0


def test_make_snapshot_calibration_default_empty_dict():
    """Missing calibration_per_dimension -> empty dict."""
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        **_provenance(),
    )
    assert s.calibration_per_dimension == {}


def test_make_snapshot_calibration_carried_through():
    cal = {"short_term": {"prior_score": 0.5,
                              "evidence_implied_score": 0.6,
                              "abs_error": 0.1, "n_observations": 5}}
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        calibration_per_dimension=cal,
        **_provenance(),
    )
    assert s.calibration_per_dimension == cal


def test_make_snapshot_default_produced_by():
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        **_provenance(),
    )
    assert s.produced_by == "build_world_model_snapshot"


def test_make_snapshot_carries_provenance_fields():
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        branch_name="custom_branch",
        base_commit_hash="abc123",
        pinned_input_manifest_sha256="x" * 64,
        produced_by="custom_producer",
        fixture_fallback_used=True,
    )
    assert s.branch_name == "custom_branch"
    assert s.base_commit_hash == "abc123"
    assert s.pinned_input_manifest_sha256 == "x" * 64
    assert s.produced_by == "custom_producer"
    assert s.fixture_fallback_used is True


# --- WorldModelSnapshot dataclass + to_dict ----------------------

def test_world_model_snapshot_is_frozen():
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        **_provenance(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.snapshot_id = "x"  # type: ignore[misc]


def test_world_model_snapshot_to_dict_calibration_dict_sorted():
    """to_dict's calibration_state.per_dimension is sorted by
    dimension name (audit byte-stability)."""
    cal = {
        "z_dim": {"a": 1},
        "a_dim": {"a": 1},
        "m_dim": {"a": 1},
    }
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        calibration_per_dimension=cal,
        **_provenance(),
    )
    d = s.to_dict()
    keys = list(d["calibration_state"]["per_dimension"].keys())
    assert keys == sorted(keys)
    assert keys == ["a_dim", "m_dim", "z_dim"]


def test_world_model_snapshot_to_dict_uncertainty_summary_sorted():
    """uncertainty_summary keys sorted in to_dict output."""
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        **_provenance(),
    )
    d = s.to_dict()
    keys = list(d["uncertainty_summary"].keys())
    assert keys == sorted(keys)


def test_world_model_snapshot_to_dict_provenance_block():
    """Provenance fields land under "provenance" sub-block, not
    at top level (clean schema separation)."""
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[], causal_relations=[], predictions=[],
        **_provenance(),
    )
    d = s.to_dict()
    assert "provenance" in d
    assert "branch_name" in d["provenance"]
    assert "base_commit_hash" in d["provenance"]
    assert "pinned_input_manifest_sha256" in d["provenance"]
    assert "produced_by" in d["provenance"]
    assert "fixture_fallback_used" in d["provenance"]


def test_world_model_snapshot_to_dict_facts_predictions_causes_lists():
    """external_facts/causal_relations/predictions are emitted as
    LISTS (not tuples) so the JSON serialization works without
    custom encoders."""
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[_fact("F-1")],
        causal_relations=[],
        predictions=[_prediction("P-1")],
        **_provenance(),
    )
    d = s.to_dict()
    assert isinstance(d["external_facts"], list)
    assert isinstance(d["predictions"], list)
    assert isinstance(d["causal_relations"], list)


# --- to_canonical_json ------------------------------------------

def test_to_canonical_json_indent_and_sort():
    """Output is indent=2, sort_keys=True. Round-trips back to
    a dict via json.loads — the canonical text is deterministic
    enough for a content-addressable artifact."""
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[_fact("F-1")], causal_relations=[],
        predictions=[],
        **_provenance(),
    )
    text = to_canonical_json(s)
    # Indented (multiple lines)
    assert "\n  " in text
    # Round-trips
    parsed = json.loads(text)
    assert parsed["schema_version"] == WORLD_MODEL_SCHEMA_VERSION


def test_to_canonical_json_sort_keys_byte_stable():
    """Same snapshot serialized twice produces identical bytes
    (audit invariant)."""
    s = make_snapshot(
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=[_fact("F-1"), _fact("F-2")],
        causal_relations=[_cause("F-1", "F-2")],
        predictions=[_prediction("P-1")],
        **_provenance(),
    )
    a = to_canonical_json(s)
    b = to_canonical_json(s)
    assert a == b

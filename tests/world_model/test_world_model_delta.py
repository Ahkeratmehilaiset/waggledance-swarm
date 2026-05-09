# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.world_model.world_model_delta.

world_model_delta computes the diff between two consecutive
WorldModelSnapshots: which external facts appeared / disappeared /
shifted confidence, which causal relations were added / removed,
which predictions got newly evaluated (Phase 9 §I).

Drift here breaks audit-trail — operators rely on deltas to see
what changed between snapshots. False positives (spurious adds)
or false negatives (missed shifts) corrupt the world-model
evolution log.

ZERO direct test coverage on main as of 2026-05-09.

Pinned invariants:

- compute_delta:
  - facts_added = facts in curr but not in prev (sorted by id).
  - facts_removed = facts in prev but not in curr (sorted by id).
  - facts_confidence_shifted: only facts in BOTH prev and curr
    AND |delta| >= confidence_shift_threshold (default 0.10).
    Each entry carries fact_id, from/to confidence, delta rounded
    to 6 decimals.
  - causes_added = (cause, effect) pairs in curr but not in prev
    (sorted as tuples).
  - causes_removed = (cause, effect) pairs in prev but not in
    curr (sorted as tuples).
  - predictions_evaluated = prediction_ids in curr that have
    actual_value set AND were NOT evaluated in prev (sorted).
    A prediction evaluated in BOTH snapshots is NOT in the delta
    (already known to operators).
- WorldModelDelta is frozen.
- to_dict round-trips: tuples become lists, nested dicts copied,
  schema_version/from/to ids preserved.
- Custom confidence_shift_threshold parameter respected.
"""
from __future__ import annotations

import dataclasses

import pytest

from waggledance.core.world_model.world_model_delta import (
    WorldModelDelta,
    compute_delta,
)
from waggledance.core.world_model.world_model_snapshot import (
    CausalRelation,
    ExternalFact,
    Prediction,
    WorldModelSnapshot,
)


# --- helpers -------------------------------------------------------

def _fact(fid: str, conf: float = 0.5, kind: str = "observation") -> ExternalFact:
    return ExternalFact(
        fact_id=fid, kind=kind, claim=f"claim {fid}",
        confidence=conf, source_refs=(),
    )


def _cause(c: str, e: str, strength: float = 0.5) -> CausalRelation:
    return CausalRelation(
        cause_fact_id=c, effect_fact_id=e, strength=strength,
    )


def _prediction(pid: str, *,
                evaluated: bool = False) -> Prediction:
    return Prediction(
        prediction_id=pid, claim=f"claim {pid}",
        predicted_value=10.0, confidence=0.5,
        horizon="short_term",
        actual_value=(11.0 if evaluated else None),
        calibration_error=(1.0 if evaluated else None),
    )


def _snapshot(
    snapshot_id: str = "snap-1",
    facts: tuple = (),
    causes: tuple = (),
    predictions: tuple = (),
) -> WorldModelSnapshot:
    return WorldModelSnapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        produced_at_iso="2026-05-09T00:00:00Z",
        external_facts=facts,
        causal_relations=causes,
        predictions=predictions,
        calibration_per_dimension={},
        uncertainty_summary={},
        branch_name="test",
        base_commit_hash="deadbeef",
        pinned_input_manifest_sha256="f" * 64,
        produced_by="test_world_model_delta",
    )


# --- empty / no-change baselines ---------------------------------

def test_compute_delta_two_empty_snapshots():
    """Empty -> empty: no adds, no removes, no shifts."""
    prev = _snapshot("a")
    curr = _snapshot("b")
    d = compute_delta(prev, curr)
    assert d.facts_added == ()
    assert d.facts_removed == ()
    assert d.facts_confidence_shifted == ()
    assert d.causes_added == ()
    assert d.causes_removed == ()
    assert d.predictions_evaluated == ()
    assert d.from_snapshot_id == "a"
    assert d.to_snapshot_id == "b"


def test_compute_delta_unchanged_snapshots_no_delta():
    """Same facts/causes/preds in both -> empty delta, even though
    snapshot_ids differ."""
    f = _fact("F-1")
    c = _cause("F-1", "F-2")
    p = _prediction("P-1")
    prev = _snapshot("a", facts=(f,), causes=(c,), predictions=(p,))
    curr = _snapshot("b", facts=(f,), causes=(c,), predictions=(p,))
    d = compute_delta(prev, curr)
    assert d.facts_added == ()
    assert d.facts_removed == ()
    assert d.facts_confidence_shifted == ()
    assert d.causes_added == ()
    assert d.causes_removed == ()
    assert d.predictions_evaluated == ()


# --- facts_added / facts_removed --------------------------------

def test_compute_delta_facts_added_sorted_by_id():
    prev = _snapshot("a", facts=())
    curr = _snapshot("b", facts=(_fact("F-z"), _fact("F-a"), _fact("F-m")))
    d = compute_delta(prev, curr)
    assert d.facts_added == ("F-a", "F-m", "F-z")
    assert d.facts_removed == ()


def test_compute_delta_facts_removed_sorted_by_id():
    prev = _snapshot("a", facts=(_fact("F-z"), _fact("F-a")))
    curr = _snapshot("b", facts=())
    d = compute_delta(prev, curr)
    assert d.facts_added == ()
    assert d.facts_removed == ("F-a", "F-z")


def test_compute_delta_facts_added_and_removed_disjoint():
    """A fact in prev only is removed; a fact in curr only is
    added; a fact in both is in neither list."""
    prev = _snapshot("a", facts=(_fact("F-1"), _fact("F-removed")))
    curr = _snapshot("b", facts=(_fact("F-1"), _fact("F-added")))
    d = compute_delta(prev, curr)
    assert d.facts_added == ("F-added",)
    assert d.facts_removed == ("F-removed",)


# --- facts_confidence_shifted -----------------------------------

def test_compute_delta_confidence_shift_above_default_threshold():
    """Default threshold 0.10. A fact whose confidence shifts from
    0.5 -> 0.7 (delta 0.2 >= 0.10) MUST appear in shifted."""
    prev = _snapshot("a", facts=(_fact("F-1", 0.5),))
    curr = _snapshot("b", facts=(_fact("F-1", 0.7),))
    d = compute_delta(prev, curr)
    assert len(d.facts_confidence_shifted) == 1
    entry = d.facts_confidence_shifted[0]
    assert entry["fact_id"] == "F-1"
    assert entry["from_confidence"] == 0.5
    assert entry["to_confidence"] == 0.7
    assert entry["delta"] == 0.2


def test_compute_delta_confidence_shift_below_threshold_excluded():
    """A 0.5 -> 0.55 shift (delta 0.05) is BELOW default 0.10 ->
    excluded from shifted (noise filter)."""
    prev = _snapshot("a", facts=(_fact("F-1", 0.5),))
    curr = _snapshot("b", facts=(_fact("F-1", 0.55),))
    d = compute_delta(prev, curr)
    assert d.facts_confidence_shifted == ()


def test_compute_delta_confidence_shift_at_exactly_threshold_included():
    """The check is `abs(d) >= threshold` -> equal is INCLUSIVE.
    A flip to `>` would silently suppress boundary shifts.

    Use halves (exactly representable in IEEE-754) to avoid the
    0.6 - 0.5 == 0.0999... float quirk that would otherwise flip
    the threshold check."""
    prev = _snapshot("a", facts=(_fact("F-1", 0.25),))
    curr = _snapshot("b", facts=(_fact("F-1", 0.75),))  # delta 0.5
    d = compute_delta(prev, curr, confidence_shift_threshold=0.5)
    assert len(d.facts_confidence_shifted) == 1
    assert d.facts_confidence_shifted[0]["delta"] == 0.5


def test_compute_delta_confidence_shift_negative_direction():
    """A confidence DROP (curr < prev) is also tracked. The delta
    field carries the sign (curr - prev), but the threshold check
    uses absolute value."""
    prev = _snapshot("a", facts=(_fact("F-1", 0.8),))
    curr = _snapshot("b", facts=(_fact("F-1", 0.3),))  # drop 0.5
    d = compute_delta(prev, curr)
    assert len(d.facts_confidence_shifted) == 1
    entry = d.facts_confidence_shifted[0]
    assert entry["delta"] < 0
    assert entry["delta"] == -0.5
    assert entry["from_confidence"] == 0.8
    assert entry["to_confidence"] == 0.3


def test_compute_delta_confidence_shift_only_for_facts_in_both():
    """Confidence shift is only computed for facts present in BOTH
    snapshots — a fact in curr only is in facts_added, NOT in
    confidence_shifted."""
    prev = _snapshot("a", facts=(_fact("F-1", 0.5),))
    curr = _snapshot("b", facts=(_fact("F-1", 0.5), _fact("F-2", 0.9)))
    d = compute_delta(prev, curr)
    assert d.facts_added == ("F-2",)
    assert d.facts_confidence_shifted == ()  # F-1 didn't shift


def test_compute_delta_custom_confidence_shift_threshold():
    """Custom threshold widens or narrows the shift band."""
    prev = _snapshot("a", facts=(_fact("F-1", 0.5),))
    curr = _snapshot("b", facts=(_fact("F-1", 0.55),))  # delta 0.05
    # default 0.10 -> excluded
    assert compute_delta(prev, curr).facts_confidence_shifted == ()
    # tighter 0.04 -> included
    tight = compute_delta(prev, curr, confidence_shift_threshold=0.04)
    assert len(tight.facts_confidence_shifted) == 1


def test_compute_delta_confidence_delta_rounded_to_six_decimals():
    """Long-tail floats truncated. Use 1/3 to force precision."""
    prev = _snapshot("a", facts=(_fact("F-1", 0.0),))
    curr = _snapshot("b", facts=(_fact("F-1", 1 / 3),))
    d = compute_delta(prev, curr)
    assert len(d.facts_confidence_shifted) == 1
    assert d.facts_confidence_shifted[0]["delta"] == round(1 / 3, 6)


# --- causes_added / causes_removed -------------------------------

def test_compute_delta_causes_added_as_sorted_tuples():
    prev = _snapshot("a", facts=(_fact("F-1"), _fact("F-2"), _fact("F-3")))
    curr = _snapshot("b", facts=(_fact("F-1"), _fact("F-2"), _fact("F-3")),
                          causes=(_cause("F-3", "F-1"), _cause("F-1", "F-2")))
    d = compute_delta(prev, curr)
    # Sorted: ('F-1','F-2') before ('F-3','F-1')
    assert d.causes_added == (("F-1", "F-2"), ("F-3", "F-1"))


def test_compute_delta_causes_removed():
    f1, f2 = _fact("F-1"), _fact("F-2")
    prev = _snapshot("a", facts=(f1, f2), causes=(_cause("F-1", "F-2"),))
    curr = _snapshot("b", facts=(f1, f2), causes=())
    d = compute_delta(prev, curr)
    assert d.causes_added == ()
    assert d.causes_removed == (("F-1", "F-2"),)


def test_compute_delta_unchanged_cause_in_neither_added_nor_removed():
    f1, f2 = _fact("F-1"), _fact("F-2")
    c = _cause("F-1", "F-2")
    prev = _snapshot("a", facts=(f1, f2), causes=(c,))
    curr = _snapshot("b", facts=(f1, f2), causes=(c,))
    d = compute_delta(prev, curr)
    assert d.causes_added == ()
    assert d.causes_removed == ()


def test_compute_delta_cause_strength_change_does_not_appear():
    """The cause delta only tracks (cause, effect) IDENTITY pairs.
    A strength change on the same pair is NOT picked up here —
    that's a separate concern (might want a future
    causes_strength_shifted field but currently not part of the
    contract). Pinning so the contract is explicit."""
    f1, f2 = _fact("F-1"), _fact("F-2")
    weak = _cause("F-1", "F-2", strength=0.2)
    strong = _cause("F-1", "F-2", strength=0.9)
    prev = _snapshot("a", facts=(f1, f2), causes=(weak,))
    curr = _snapshot("b", facts=(f1, f2), causes=(strong,))
    d = compute_delta(prev, curr)
    # Same (cause, effect) -> NOT added or removed
    assert d.causes_added == ()
    assert d.causes_removed == ()


# --- predictions_evaluated --------------------------------------

def test_compute_delta_newly_evaluated_predictions():
    """A prediction unevaluated in prev but evaluated in curr
    (actual_value not None) appears in predictions_evaluated."""
    p_unev = _prediction("P-1", evaluated=False)
    p_ev = _prediction("P-1", evaluated=True)
    prev = _snapshot("a", predictions=(p_unev,))
    curr = _snapshot("b", predictions=(p_ev,))
    d = compute_delta(prev, curr)
    assert d.predictions_evaluated == ("P-1",)


def test_compute_delta_predictions_already_evaluated_in_prev_excluded():
    """A prediction evaluated in BOTH snapshots is NOT in the
    delta — it's old news to operators."""
    p_ev = _prediction("P-1", evaluated=True)
    prev = _snapshot("a", predictions=(p_ev,))
    curr = _snapshot("b", predictions=(p_ev,))
    d = compute_delta(prev, curr)
    assert d.predictions_evaluated == ()


def test_compute_delta_unevaluated_predictions_excluded():
    """A prediction unevaluated in both snapshots: NOT in the
    delta (no evaluation event yet)."""
    p_unev = _prediction("P-1", evaluated=False)
    prev = _snapshot("a", predictions=(p_unev,))
    curr = _snapshot("b", predictions=(p_unev,))
    d = compute_delta(prev, curr)
    assert d.predictions_evaluated == ()


def test_compute_delta_predictions_evaluated_sorted():
    p1_unev = _prediction("P-z", evaluated=False)
    p2_unev = _prediction("P-a", evaluated=False)
    p3_unev = _prediction("P-m", evaluated=False)
    p1_ev = _prediction("P-z", evaluated=True)
    p2_ev = _prediction("P-a", evaluated=True)
    p3_ev = _prediction("P-m", evaluated=True)
    prev = _snapshot("a", predictions=(p1_unev, p2_unev, p3_unev))
    curr = _snapshot("b", predictions=(p1_ev, p2_ev, p3_ev))
    d = compute_delta(prev, curr)
    assert d.predictions_evaluated == ("P-a", "P-m", "P-z")


# --- snapshot id propagation ------------------------------------

def test_compute_delta_carries_snapshot_ids():
    prev = _snapshot("snap-prev")
    curr = _snapshot("snap-curr")
    d = compute_delta(prev, curr)
    assert d.from_snapshot_id == "snap-prev"
    assert d.to_snapshot_id == "snap-curr"


# --- WorldModelDelta dataclass + to_dict ------------------------

def test_world_model_delta_is_frozen():
    d = WorldModelDelta(
        schema_version=1, from_snapshot_id="a", to_snapshot_id="b",
        facts_added=(), facts_removed=(),
        facts_confidence_shifted=(),
        causes_added=(), causes_removed=(),
        predictions_evaluated=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.from_snapshot_id = "x"  # type: ignore[misc]


def test_to_dict_round_trips_all_fields():
    """to_dict converts tuples to lists, nested dicts copied; all
    schema fields preserved."""
    d = WorldModelDelta(
        schema_version=2, from_snapshot_id="a", to_snapshot_id="b",
        facts_added=("F-1",), facts_removed=("F-old",),
        facts_confidence_shifted=(
            {"fact_id": "F-2", "from_confidence": 0.5,
             "to_confidence": 0.7, "delta": 0.2},
        ),
        causes_added=(("F-1", "F-2"),),
        causes_removed=(("F-x", "F-y"),),
        predictions_evaluated=("P-1",),
    )
    out = d.to_dict()
    assert out["schema_version"] == 2
    assert out["from_snapshot_id"] == "a"
    assert out["to_snapshot_id"] == "b"
    assert out["facts_added"] == ["F-1"]
    assert out["facts_removed"] == ["F-old"]
    assert isinstance(out["facts_confidence_shifted"], list)
    assert out["facts_confidence_shifted"][0]["fact_id"] == "F-2"
    assert out["causes_added"] == [["F-1", "F-2"]]
    assert out["causes_removed"] == [["F-x", "F-y"]]
    assert out["predictions_evaluated"] == ["P-1"]


def test_to_dict_empty_delta():
    """Empty delta produces empty lists, NOT empty tuples (audit
    consumers expect JSON-serializable lists)."""
    d = WorldModelDelta(
        schema_version=1, from_snapshot_id="a", to_snapshot_id="b",
        facts_added=(), facts_removed=(),
        facts_confidence_shifted=(),
        causes_added=(), causes_removed=(),
        predictions_evaluated=(),
    )
    out = d.to_dict()
    assert out["facts_added"] == []
    assert out["facts_removed"] == []
    assert out["facts_confidence_shifted"] == []
    assert out["causes_added"] == []
    assert out["causes_removed"] == []
    assert out["predictions_evaluated"] == []

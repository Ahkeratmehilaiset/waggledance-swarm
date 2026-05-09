# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.dreaming.curriculum.

Codex scout round 3 flagged this as Candidate 1 (high risk if missing,
recommended first pick): the dream curriculum is the first runtime-
confirmed dream producer (`tools/run_phase17a_producer_fabric_proof.py`
feeds its output into the producer fabric and IR adapters), but the
proof only validates artifact shape — priority ordering, fallback
selection, mode mapping, uncertainty thresholds, and night exhaustion
are all uncovered. A drift here can silently route the dream cycle to
the wrong cell while leaving the artifact JSON-valid.

Pinned invariants (c.txt §C1):

- `build_dreamable_items` emits a deterministic priority-desc /
  source_id-asc list with one DreamableItem per tension, blind spot,
  and top-3 curiosity item.
- `build_curriculum` selects `primary_source = "deferred_to_dream"`
  iff any tension has `resolution_path == "deferred_to_dream"`;
  otherwise falls back to "secondary_fallback" and emits
  `secondary_fallback_reason`.
- The fallback selects only `blind_spot` and
  `calibration_oscillation` source_kinds.
- Night exhaustion: when fewer items than `top_nights` exist, the
  remaining nights have `mode = "wait"` and empty `target_items`.
- Uncertainty thresholds: priority ≥ 0.20 → low, [0.05, 0.20) →
  medium, < 0.05 → high.
- `suggest_mode` mapping: tension → introspection,
  calibration_oscillation → introspection, blind_spot →
  base_solver_growth, curiosity → solver_refinement.
"""
from __future__ import annotations

import pytest

from waggledance.core.dreaming.curriculum import (
    DreamCurriculum,
    DreamNight,
    DreamableItem,
    build_curriculum,
    build_dreamable_items,
    has_deferred_to_dream,
)


# --- helpers --------------------------------------------------------

def _provenance() -> dict:
    return {
        "branch_name": "test/dream-curriculum",
        "base_commit_hash": "deadbeef",
        "pinned_input_manifest_sha256": "f" * 64,
    }


def _self_model_with_deferred_tension() -> dict:
    return {
        "workspace_tensions": [
            {
                "tension_id": "T-deferred-1",
                "type": "scorecard_drift",
                "claim": "math_breadth score = 0.8",
                "observation": "evidence implies math_breadth = 0.4",
                "severity": "high",
                "resolution_path": "deferred_to_dream",
                "lifecycle_status": "persisting",
                "evidence_refs": ["cell:hex_math"],
            },
            {
                "tension_id": "T-osc-1",
                "type": "calibration_oscillation",
                "claim": "calibration oscillating",
                "observation": "drift seen in 3 cycles",
                "severity": "medium",
                "resolution_path": "calibration_correction",
                "lifecycle_status": "new",
                "evidence_refs": [],
            },
        ],
        "blind_spots": [
            {
                "domain": "vision_reasoning",
                "severity": "high",
                "detectors": ["coverage_negative_space"],
            },
        ],
    }


def _curiosity_log() -> list[dict]:
    return [
        {"curiosity_id": "C-1", "candidate_cell": "cell:hex_a",
         "estimated_value": 7.0, "suspected_gap_type": "missing_solver",
         "count": 4},
        {"curiosity_id": "C-2", "candidate_cell": "cell:hex_b",
         "estimated_value": 3.0, "suspected_gap_type": "low_recall",
         "count": 1},
    ]


def _calibration_corrections() -> list[dict]:
    return [
        {"dimension": "math_breadth", "evidence_implied_score": 0.4,
         "score": 0.8},
    ]


# --- build_dreamable_items ------------------------------------------

def test_dreamable_items_sorted_priority_desc_with_source_id_tiebreak():
    items = build_dreamable_items(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
    )
    # Sorted by (-dream_priority, source_id).
    priorities = [it.dream_priority for it in items]
    assert priorities == sorted(priorities, reverse=True)


def test_dreamable_items_emit_one_per_tension_blindspot_top3_curiosity():
    items = build_dreamable_items(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
    )
    kinds = [it.source_kind for it in items]
    assert kinds.count("tension") + kinds.count("calibration_oscillation") == 2
    assert kinds.count("blind_spot") == 1
    assert kinds.count("curiosity") == 2  # only 2 in log; top-3 cap doesn't trim


def test_dreamable_items_suggest_mode_per_kind():
    items = build_dreamable_items(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
    )
    by_kind = {it.source_kind: it.suggested_mode for it in items}
    # tension → introspection (no bridge / subdivision hints)
    assert by_kind.get("tension") == "introspection"
    # calibration_oscillation → introspection
    assert by_kind.get("calibration_oscillation") == "introspection"
    # blind_spot → base_solver_growth
    assert by_kind.get("blind_spot") == "base_solver_growth"
    # curiosity → solver_refinement (no bridge / subdivision)
    assert by_kind.get("curiosity") == "solver_refinement"


# --- build_curriculum: primary_source = deferred_to_dream -----------

def test_curriculum_primary_source_is_deferred_when_any_tension_deferred():
    cur = build_curriculum(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
        **_provenance(),
    )
    assert isinstance(cur, DreamCurriculum)
    assert cur.primary_source == "deferred_to_dream"
    assert cur.secondary_fallback_reason is None
    # Every night carries the same primary_source label.
    for n in cur.nights:
        assert n.primary_source == "deferred_to_dream"


def test_curriculum_default_top_nights_is_seven_with_wait_exhaustion():
    cur = build_curriculum(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
        **_provenance(),
    )
    assert len(cur.nights) == 7
    # Only tension + calibration_oscillation feed deferred_to_dream
    # primary_items, so 2 active nights then 5 wait-nights.
    active = [n for n in cur.nights if n.target_items]
    waiting = [n for n in cur.nights if not n.target_items]
    assert len(active) == 2
    assert len(waiting) == 5
    for n in waiting:
        assert n.mode == "wait"
        assert n.dream_objective == "No further dreamable items in this cycle."
        assert n.uncertainty == "high"


def test_curriculum_counts_by_mode_and_kind_match_active_nights():
    cur = build_curriculum(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
        **_provenance(),
    )
    # counts_by_mode includes "wait" for the exhausted nights.
    assert cur.counts_by_mode.get("wait") == 5
    # counts_by_kind sums over active nights only (target_items).
    total_kinds = sum(cur.counts_by_kind.values())
    active = sum(1 for n in cur.nights if n.target_items)
    assert total_kinds == active


# --- build_curriculum: secondary_fallback ---------------------------

def _self_model_no_deferred() -> dict:
    """Same shape as the deferred fixture but no resolution_path
    set to deferred_to_dream."""
    return {
        "workspace_tensions": [
            {
                "tension_id": "T-osc-only",
                "type": "calibration_oscillation",
                "claim": "calibration oscillating",
                "observation": "drift seen in 3 cycles",
                "severity": "medium",
                "resolution_path": "calibration_correction",
                "lifecycle_status": "new",
                "evidence_refs": [],
            },
        ],
        "blind_spots": [
            {
                "domain": "vision_reasoning",
                "severity": "high",
                "detectors": ["coverage_negative_space"],
            },
        ],
    }


def test_has_deferred_to_dream_predicate_matches_resolution_path():
    assert has_deferred_to_dream(_self_model_with_deferred_tension()) is True
    assert has_deferred_to_dream(_self_model_no_deferred()) is False
    assert has_deferred_to_dream({"workspace_tensions": []}) is False


def test_curriculum_falls_back_to_blind_spot_and_calibration_oscillation():
    """When NO tension has resolution_path == deferred_to_dream, the
    curriculum primary_source flips to secondary_fallback and
    primary_items contains only blind_spots and
    calibration_oscillations — never plain curiosity.
    """
    cur = build_curriculum(
        self_model=_self_model_no_deferred(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
        **_provenance(),
    )
    assert cur.primary_source == "secondary_fallback"
    assert cur.secondary_fallback_reason
    # The fallback reason mentions deferred_to_dream and blind spots /
    # calibration_oscillation explicitly so audit trails can find it.
    assert "deferred_to_dream" in cur.secondary_fallback_reason
    assert ("blind" in cur.secondary_fallback_reason.lower()
            or "calibration" in cur.secondary_fallback_reason.lower())
    # Active-night kinds must be only blind_spot or calibration_oscillation.
    active_kinds = {it.source_kind
                    for n in cur.nights for it in n.target_items}
    assert active_kinds.issubset({"blind_spot", "calibration_oscillation"})


def test_curriculum_uncertainty_thresholds_per_priority_band():
    """uncertainty = high if priority < 0.05;
    medium if 0.05 ≤ priority < 0.20; low if priority ≥ 0.20."""
    cur = build_curriculum(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
        **_provenance(),
    )
    for n in cur.nights:
        if not n.target_items:
            continue  # wait-nights asserted separately
        prio = n.target_items[0].dream_priority
        if prio < 0.05:
            assert n.uncertainty == "high"
        elif prio < 0.20:
            assert n.uncertainty == "medium"
        else:
            assert n.uncertainty == "low"


def test_curriculum_provenance_carried_through():
    cur = build_curriculum(
        self_model=_self_model_with_deferred_tension(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration_corrections(),
        **_provenance(),
    )
    assert cur.branch_name == "test/dream-curriculum"
    assert cur.base_commit_hash == "deadbeef"
    assert cur.pinned_input_manifest_sha256 == "f" * 64

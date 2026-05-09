# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.dreaming.request_pack.

Codex scout round 3 flagged this as Candidate 2 (medium risk if missing):
this module is the normal dream-teacher input-pack builder for the
dream pipeline, but the Phase 17A producer-fabric proof stops at
curriculum shape. The dream_request_pack_sha12 is the continuity anchor
used by downstream proposal sidecars; if it includes itself, changes
ordering nondeterministically, or slices the wrong evidence, generated
proposals can become unlinkable or overexposed while still looking
well-formed.

Pinned invariants (c.txt §C2):

- `compute_pack_sha12` raises if SHA_FIELD is in the input dict.
- `build_request_pack` mode → requested_action_type mapping per
  `_MODE_TO_ACTION` (introspection → self_inspection,
  base_solver_growth → new_solver, etc.).
- `attention_focus` bounded to first 3 entries (slice protection).
- `self_model_snippet` is RELEVANT-ONLY: only tensions whose
  tension_id matches the night's source_tension_ids; never the full
  workspace_tensions list.
- `replay_case_ids` are sorted, deduplicated, and only those whose
  source_tension_id or source_curiosity_id matches the night.
- `pack_to_dict` is stable: round-trip recompute of the sha matches
  the stored sha12.
"""
from __future__ import annotations

import pytest

from waggledance.core.dreaming.curriculum import DreamableItem, DreamNight
from waggledance.core.dreaming.request_pack import (
    SHA_FIELD,
    _pack_to_dict,
    build_request_pack,
    compute_pack_sha12,
    pack_to_dict,
)


# --- compute_pack_sha12: rejection of self-inclusion ---------------

def test_compute_pack_sha12_raises_when_sha_field_in_input():
    """The hash must be computed BEFORE the field is added to the dict.
    Including it would let the SHA influence its own computation."""
    payload = {"foo": "bar", SHA_FIELD: "abcdef012345"}
    with pytest.raises(ValueError):
        compute_pack_sha12(payload)


def test_compute_pack_sha12_deterministic_on_same_input():
    payload = {"a": 1, "b": [2, 3], "c": {"nested": "x"}}
    sha1 = compute_pack_sha12(payload)
    sha2 = compute_pack_sha12(payload)
    assert sha1 == sha2
    assert len(sha1) == 12  # truncated to 12 chars


def test_compute_pack_sha12_changes_when_input_changes():
    sha_a = compute_pack_sha12({"x": 1})
    sha_b = compute_pack_sha12({"x": 2})
    assert sha_a != sha_b


# --- helpers: minimal night + self_model + manifest ----------------

def _dreamable_item(source_id: str, source_kind: str = "tension",
                    candidate_cell: str | None = "cell:hex_a") -> DreamableItem:
    return DreamableItem(
        source_id=source_id,
        source_kind=source_kind,
        candidate_cell=candidate_cell,
        severity=0.7,
        calibration_gap=0.3,
        recurrence=1,
        expected_value=0.7,
        blind_spot_bonus=0.0,
        exploration_bonus=0.0,
        dream_priority=0.10,
        suggested_mode="introspection",
        evidence_refs=("ref:x",),
        rationale="test",
    )


def _dream_night(
    night_index: int = 1,
    target_items: tuple[DreamableItem, ...] = (),
    primary_cells: tuple[str, ...] = ("cell:hex_a",),
    mode: str = "introspection",
) -> DreamNight:
    if not target_items:
        target_items = (_dreamable_item("T-1"),)
    return DreamNight(
        night_index=night_index,
        target_items=target_items,
        primary_cells=primary_cells,
        secondary_cells=(),
        dream_objective="test objective",
        supporting_evidence=("ref:x",),
        why_now="test",
        uncertainty="medium",
        mode=mode,
        primary_source="deferred_to_dream",
    )


def _self_model_with_extra() -> dict:
    """Self-model with one targeted tension AND one untargeted one,
    plus blind spots and scorecard. Used to verify self_model_snippet
    only carries relevant content."""
    return {
        "schema_version": 1,
        "workspace_tensions": [
            {"tension_id": "T-1", "type": "scorecard_drift",
             "claim": "x", "observation": "y", "severity": "high",
             "evidence_refs": []},
            {"tension_id": "T-NOT-IN-NIGHT", "type": "other",
             "claim": "irrelevant", "observation": "irrelevant",
             "severity": "low", "evidence_refs": []},
        ],
        "scorecard": {"breadth": 0.5},
        "blind_spots": [{"domain": "x", "severity": "low"}],
    }


def _provenance_kwargs() -> dict:
    return {
        "branch_name": "test/dream-pack",
        "base_commit_hash": "deadbeef",
        "pinned_input_manifest_sha256": "f" * 64,
    }


# --- build_request_pack: action mapping ----------------------------

@pytest.mark.parametrize("mode,expected_action", [
    ("base_solver_growth", "new_solver"),
    ("solver_refinement", "solver_improvement"),
    ("bridge_composition", "bridge_composition"),
    ("subdivision_pressure", "subdivision_recommendation"),
    ("introspection", "self_inspection"),
    ("wait", "wait"),
])
def test_build_request_pack_mode_to_requested_action_type(mode, expected_action):
    night = _dream_night(mode=mode)
    pack = build_request_pack(
        night=night,
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a", "solver_count": 5},
        attention_focus=[],
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    assert pack.requested_action_type == expected_action
    assert pack.mode == mode


def test_build_request_pack_unknown_mode_falls_back_to_wait():
    night = _dream_night(mode="not_a_real_mode")
    pack = build_request_pack(
        night=night,
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    assert pack.requested_action_type == "wait"


# --- attention_focus bounded ---------------------------------------

def test_build_request_pack_attention_focus_bounded_to_three():
    """attention_focus is sliced to first 3 entries; longer lists
    must NOT leak past that cap."""
    focus = [{"curiosity_id": f"C-{i}"} for i in range(5)]
    pack = build_request_pack(
        night=_dream_night(),
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=focus,
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    assert len(pack.attention_focus) == 3
    assert [a["curiosity_id"] for a in pack.attention_focus] == [
        "C-0", "C-1", "C-2",
    ]


# --- self_model_snippet: relevant-only -----------------------------

def test_build_request_pack_self_model_snippet_filters_tensions():
    """Only tensions whose tension_id is in night's source_tension_ids
    appear in the snippet; the unrelated T-NOT-IN-NIGHT must be absent."""
    night = _dream_night(target_items=(_dreamable_item("T-1"),))
    pack = build_request_pack(
        night=night,
        self_model=_self_model_with_extra(),
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    snippet_ids = {t["tension_id"] for t in pack.self_model_snippet["workspace_tensions"]}
    assert snippet_ids == {"T-1"}
    # blind_spots and scorecard pass through verbatim.
    assert pack.self_model_snippet["blind_spots"] == [{"domain": "x", "severity": "low"}]
    assert pack.self_model_snippet["scorecard"] == {"breadth": 0.5}


# --- replay_case_ids: bounded + sorted + deduplicated --------------

def test_build_request_pack_replay_case_ids_sorted_dedup_and_filtered():
    night = _dream_night(target_items=(
        _dreamable_item("T-1"),
        _dreamable_item("C-1", source_kind="curiosity"),
    ))
    manifest = {"cases": [
        {"replay_case_id": "R-2", "source_tension_id": "T-1"},
        {"replay_case_id": "R-1", "source_curiosity_id": "C-1"},
        {"replay_case_id": "R-1", "source_tension_id": "T-1"},  # duplicate id
        {"replay_case_id": "R-9", "source_tension_id": "T-OTHER"},  # filtered out
    ]}
    pack = build_request_pack(
        night=night,
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=manifest,
        **_provenance_kwargs(),
    )
    # Sorted, deduplicated, filtered to relevant matches only.
    assert pack.replay_case_ids == ("R-1", "R-2")


def test_build_request_pack_replay_case_ids_empty_when_manifest_none():
    pack = build_request_pack(
        night=_dream_night(),
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    assert pack.replay_case_ids == ()


# --- calibration_oscillation linkage (PR #112 fix) -----------------
# Codex finding 2026-05-09: curriculum.build_curriculum can produce
# DreamableItem.source_kind == "calibration_oscillation" from
# workspace_tensions, but build_request_pack used to filter on
# source_kind == "tension" only. That dropped source_tension_ids,
# self_model_snippet.workspace_tensions, and replay_case_ids for
# calibration-oscillation nights. The fix treats both kinds as
# tension-backed; these tests pin that contract.

def test_calibration_oscillation_item_preserves_tension_id_linkage():
    """A calibration_oscillation source_kind must contribute its
    source_id to source_tension_ids — otherwise the dream night
    silently loses its evidence linkage."""
    night = _dream_night(target_items=(
        _dreamable_item("T-osc-1", source_kind="calibration_oscillation"),
    ))
    pack = build_request_pack(
        night=night,
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    assert pack.source_tension_ids == ("T-osc-1",)
    assert pack.source_curiosity_ids == ()


def test_calibration_oscillation_item_carries_through_to_self_model_snippet():
    """The self-model snippet must contain the matching workspace
    tension when the item is a calibration_oscillation, not just a
    plain tension."""
    night = _dream_night(target_items=(
        _dreamable_item("T-osc-1", source_kind="calibration_oscillation"),
    ))
    self_model = {
        "schema_version": 1,
        "workspace_tensions": [
            {"tension_id": "T-osc-1", "type": "calibration_drift",
             "claim": "x", "observation": "y", "severity": "high",
             "evidence_refs": []},
            {"tension_id": "T-NOT-IN-NIGHT", "type": "other",
             "claim": "irrelevant", "observation": "irrelevant",
             "severity": "low", "evidence_refs": []},
        ],
        "scorecard": {},
        "blind_spots": [],
    }
    pack = build_request_pack(
        night=night,
        self_model=self_model,
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    snippet_ids = {
        t["tension_id"] for t in pack.self_model_snippet["workspace_tensions"]
    }
    assert snippet_ids == {"T-osc-1"}


def test_calibration_oscillation_item_selects_replay_cases():
    """replay_case_ids selection must include cases whose
    source_tension_id matches a calibration_oscillation item, just
    like for plain tension items."""
    night = _dream_night(target_items=(
        _dreamable_item("T-osc-1", source_kind="calibration_oscillation"),
    ))
    manifest = {"cases": [
        {"replay_case_id": "R-osc", "source_tension_id": "T-osc-1"},
        {"replay_case_id": "R-other", "source_tension_id": "T-OTHER"},
    ]}
    pack = build_request_pack(
        night=night,
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=manifest,
        **_provenance_kwargs(),
    )
    assert pack.replay_case_ids == ("R-osc",)


# --- pack_to_dict + sha round-trip ---------------------------------

def test_pack_to_dict_round_trip_sha_matches_stored():
    """pack_to_dict carries the stored sha12. Recomputing the sha
    from `_pack_to_dict(pack, with_sha=False)` must match the stored
    value: this is the audit-trail invariant downstream sidecar
    consumers depend on."""
    pack = build_request_pack(
        night=_dream_night(),
        self_model=_self_model_with_extra(),
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=None,
        **_provenance_kwargs(),
    )
    stored = pack.dream_request_pack_sha12
    assert stored
    pack_no_sha = _pack_to_dict(pack, with_sha=False)
    assert SHA_FIELD not in pack_no_sha
    recomputed = compute_pack_sha12(pack_no_sha)
    assert recomputed == stored

    # pack_to_dict (with sha) MUST include the sha field.
    full = pack_to_dict(pack)
    assert full[SHA_FIELD] == stored


def test_pack_to_dict_carries_continuity_anchor_provenance():
    pack = build_request_pack(
        night=_dream_night(),
        self_model={"workspace_tensions": [], "blind_spots": []},
        cell_manifest={"cell_id": "cell:hex_a"},
        attention_focus=[],
        replay_case_manifest=None,
        replay_manifest_sha256="r" * 64,
        **_provenance_kwargs(),
    )
    d = pack_to_dict(pack)
    anchor = d["continuity_anchor"]
    assert anchor["branch_name"] == "test/dream-pack"
    assert anchor["base_commit_hash"] == "deadbeef"
    assert anchor["pinned_input_manifest_sha256"] == "f" * 64
    assert anchor["replay_manifest_sha256"] == "r" * 64

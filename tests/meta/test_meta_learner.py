# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.meta.meta_learner.

Codex scout round 2 flagged this file as Candidate 1 (high risk if
missing): ``synthesize_proposals`` is the decision point where raw
multi-plane evidence becomes a bounded MetaProposal that downstream
producer-fabric and review-bundle code present to a human reviewer.
The Phase 17A producer-fabric proof exercises the path structurally
but does not pin the per-rule decision boundaries.

These tests exercise ``gather_curiosity_evidence``,
``gather_self_model_evidence``, ``gather_dream_evidence`` and
``synthesize_proposals`` directly, asserting:

- a convergent multi-plane target produces a MetaProposal with the
  expected type, scope_class, impacted cell, evidence planes,
  priority>0, lifecycle, and no_mutation_in_session = True;
- a single-plane curiosity-only target falls into
  ``insufficient_evidence`` rather than becoming a proposal;
- a single-plane self_model target with high severity is allowed to
  become an ``introspection_gap`` proposal (per D.txt §D4);
- ``lifecycle_status`` flips from ``new`` to ``persisting`` when the
  meta_proposal_id appears in ``history_ids_in_immediate_prev``;
- ``no_mutation_in_session`` is unconditionally ``True`` on every
  emitted proposal.
"""
from __future__ import annotations

import pytest

from waggledance.core.meta import META_SCHEMA_VERSION, PRIMARY_PLANES
from waggledance.core.meta.meta_learner import (
    EvidenceItem,
    MetaProposal,
    SynthesisResult,
    compute_meta_proposal_id,
    gather_curiosity_evidence,
    gather_dream_evidence,
    gather_self_model_evidence,
    proposal_to_dict,
    synthesize_proposals,
)


# --- gather_* helpers ------------------------------------------------

def test_gather_curiosity_evidence_keys_by_candidate_cell():
    log = [
        {"curiosity_id": "C1", "candidate_cell": "cell:hex_a",
         "estimated_value": 7.0, "suspected_gap_type": "missing_solver"},
        {"curiosity_id": "C2", "candidate_cell": None,
         "estimated_value": 3.0, "suspected_gap_type": "low_recall"},
    ]
    items = gather_curiosity_evidence(curiosity_summary=None, curiosity_log=log)
    assert len(items) == 2
    by_target = {it.canonical_target: it for it in items}
    assert "cell:hex_a" in by_target
    assert "_unattributed" in by_target
    # severity = clamped (estimated_value / 10), capped at 1.0.
    assert by_target["cell:hex_a"].severity == pytest.approx(0.7)


def test_gather_self_model_evidence_emits_tensions_and_blind_spots():
    self_model = {
        "workspace_tensions": [
            {"tension_id": "T1", "type": "calibration_drift",
             "severity": "high", "evidence_refs": ["cell:hex_b"]},
        ],
        "blind_spots": [
            {"domain": "math_reasoning", "severity": 0.8},
        ],
    }
    items = gather_self_model_evidence(self_model, calibration_corrections=[])
    targets = {it.canonical_target for it in items}
    # `_cell_from_evidence_refs` strips the "cell:" prefix → bare cell name.
    assert "hex_b" in targets
    assert "math_reasoning" in targets  # blind_spot keyed by domain
    # 'high' string severity normalizes to 1.0
    tension = next(it for it in items if it.canonical_target == "hex_b")
    assert tension.severity == pytest.approx(1.0)


def test_gather_dream_evidence_skips_non_promising_proposals():
    proposals = [
        {"structurally_promising": False, "selected_proposal": {"cell_id": "cell:x"}},
        {"structurally_promising": True, "confidence": 0.8,
         "selected_proposal": {"cell_id": "cell:y", "proposal_id": "P-1"},
         "source_tension_ids": ["T1"]},
    ]
    items = gather_dream_evidence(proposals)
    # Only the promising one contributes.
    assert len(items) == 1
    assert items[0].canonical_target == "cell:y"
    assert items[0].plane == "dream"
    assert items[0].severity == pytest.approx(0.8)


# --- synthesize_proposals: convergent multi-plane target -------------

def _convergent_evidence(cell: str = "cell:hex_target") -> list[EvidenceItem]:
    """Build curiosity + self_model + dream evidence on the same target."""
    return [
        EvidenceItem(plane="curiosity", canonical_target=cell,
                     source_id="C1", cell_id=cell, severity=0.7,
                     rationale="curiosity gap"),
        EvidenceItem(plane="self_model", canonical_target=cell,
                     source_id="T1", cell_id=cell, severity=0.66,
                     rationale="tension"),
        EvidenceItem(plane="dream", canonical_target=cell,
                     source_id="T1", cell_id=cell, severity=0.8,
                     rationale="dream replay"),
    ]


def _provenance_kwargs() -> dict:
    return {
        "branch_name": "test/meta-learner",
        "base_commit_hash": "deadbeef",
        "pinned_input_manifest_sha256": "a" * 64,
        "consumed_hook_contracts": [{"name": "fixture_hook", "version": "1"}],
    }


def test_synthesize_emits_proposal_for_convergent_three_plane_target():
    items = _convergent_evidence("cell:hex_target")
    dream_mps = [{
        "structurally_promising": True,
        "confidence": 0.8,
        "selected_proposal": {"cell_id": "cell:hex_target", "proposal_id": "P1"},
        "source_tension_ids": ["T1"],
        "replay_metrics": {"structural_gain_count": 2},
    }]
    result = synthesize_proposals(
        items=items,
        self_model={"workspace_tensions": [], "blind_spots": []},
        dream_meta_proposals=dream_mps,
        resilience_doc=None,
        **_provenance_kwargs(),
    )
    assert isinstance(result, SynthesisResult)
    assert len(result.proposals) == 1
    p = result.proposals[0]
    # Three planes converged with primary planes ⇒ solver_family_growth
    # (because dream + curiosity/self_model present, no subdivision hint).
    assert p.proposal_type == "solver_family_growth"
    assert p.scope_class == "solver_library"
    assert p.impacted_cells == ("cell:hex_target",)
    assert set(p.evidence_planes) == {"curiosity", "self_model", "dream"}
    # cross_plane_support_factor for 3 planes = 1 + 0.25*2 = 1.5.
    assert p.cross_plane_support_factor == pytest.approx(1.5)
    assert p.proposal_priority > 0
    assert p.lifecycle_status == "new"
    assert p.no_mutation_in_session is True
    assert p.schema_version == META_SCHEMA_VERSION
    # The "review boundary" rationale must be present and non-empty.
    assert p.why_human_review_required
    assert p.why_now


def test_synthesize_lifecycle_flips_to_persisting_on_history_match():
    """Same proposal computed in a previous run must be tagged persisting."""
    items = _convergent_evidence("cell:hex_target")
    dream_mps = [{
        "structurally_promising": True,
        "confidence": 0.8,
        "selected_proposal": {"cell_id": "cell:hex_target", "proposal_id": "P1"},
        "source_tension_ids": ["T1"],
        "replay_metrics": {"structural_gain_count": 2},
    }]
    # Pre-compute the deterministic id the synthesizer will produce.
    expected_id = compute_meta_proposal_id(
        proposal_type="solver_family_growth",
        scope_class="solver_library",
        impacted_cells=("cell:hex_target",),
        canonical_target="cell:hex_target",
    )
    result = synthesize_proposals(
        items=items,
        self_model={"workspace_tensions": [], "blind_spots": []},
        dream_meta_proposals=dream_mps,
        resilience_doc=None,
        history_ids_in_immediate_prev={expected_id},
        **_provenance_kwargs(),
    )
    assert len(result.proposals) == 1
    assert result.proposals[0].meta_proposal_id == expected_id
    assert result.proposals[0].lifecycle_status == "persisting"


# --- single-plane boundaries -----------------------------------------

def test_synthesize_single_plane_curiosity_only_lands_in_insufficient_or_rejected():
    """Only curiosity plane present, no self_model / dream / resilience.

    `infer_proposal_type` returns None (no rule fires), and per D.txt §D4
    single-plane curiosity is not in the allow-list. The synthesizer must
    classify the target as either insufficient_evidence or rejected (not
    a proposal).
    """
    items = [
        EvidenceItem(plane="curiosity", canonical_target="cell:lonely",
                     source_id="C1", cell_id="cell:lonely", severity=0.7,
                     rationale="curiosity only"),
    ]
    result = synthesize_proposals(
        items=items,
        self_model={"workspace_tensions": [], "blind_spots": []},
        dream_meta_proposals=[],
        resilience_doc=None,
        **_provenance_kwargs(),
    )
    assert result.proposals == ()
    targets_seen = (
        [r["candidate_target"] for r in result.insufficient_evidence]
        + [r["candidate_target"] for r in result.rejected_candidates]
    )
    assert "cell:lonely" in targets_seen


def test_synthesize_single_plane_self_model_high_severity_promotes_to_introspection_gap():
    """Single-plane self_model is allowed if severity_max ≥ 0.66 (introspection_gap)."""
    items = [
        EvidenceItem(plane="self_model", canonical_target="cell:lonely_sm",
                     source_id="T-sm", cell_id="cell:lonely_sm", severity=0.9,
                     rationale="strong tension"),
    ]
    result = synthesize_proposals(
        items=items,
        self_model={"workspace_tensions": [], "blind_spots": []},
        dream_meta_proposals=[],
        resilience_doc=None,
        **_provenance_kwargs(),
    )
    assert len(result.proposals) == 1
    p = result.proposals[0]
    assert p.proposal_type == "introspection_gap"
    assert p.scope_class == "introspection"
    assert p.evidence_planes == ("self_model",)
    assert p.no_mutation_in_session is True


def test_synthesize_below_min_evidence_lands_in_rejected():
    """Evidence so weak no rule fires AND ev_strength < min_evidence ⇒ rejected."""
    items = [
        EvidenceItem(plane="curiosity", canonical_target="cell:weak",
                     source_id="C-weak", cell_id="cell:weak", severity=0.05,
                     rationale="very weak"),
    ]
    result = synthesize_proposals(
        items=items,
        self_model={"workspace_tensions": [], "blind_spots": []},
        dream_meta_proposals=[],
        resilience_doc=None,
        min_evidence=0.10,
        **_provenance_kwargs(),
    )
    assert result.proposals == ()
    assert any(r["candidate_target"] == "cell:weak"
               for r in result.rejected_candidates)


# --- serialization invariant -----------------------------------------

def test_proposal_to_dict_converts_tuples_to_lists():
    items = _convergent_evidence("cell:serial")
    dream_mps = [{
        "structurally_promising": True,
        "confidence": 0.8,
        "selected_proposal": {"cell_id": "cell:serial", "proposal_id": "P-ser"},
        "source_tension_ids": ["T1"],
        "replay_metrics": {"structural_gain_count": 1},
    }]
    result = synthesize_proposals(
        items=items,
        self_model={"workspace_tensions": [], "blind_spots": []},
        dream_meta_proposals=dream_mps,
        resilience_doc=None,
        **_provenance_kwargs(),
    )
    p = result.proposals[0]
    d = proposal_to_dict(p)
    # Must be JSON-serializable shape: tuples become lists.
    for key in ("impacted_cells", "evidence_planes",
                "source_curiosity_ids", "source_tension_ids",
                "source_dream_meta_proposal_ids", "source_resilience_refs"):
        assert isinstance(d[key], list), f"{key} must be a list, got {type(d[key])}"
    # no_mutation_in_session boundary survives serialization.
    assert d["no_mutation_in_session"] is True

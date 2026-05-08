# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.meta.review_bundle.

Codex scout round 2 flagged this as Candidate 2 (medium risk if missing).
Existing producer-fabric and IR tests reference review_bundle artifacts
but do not exercise the threshold boundaries of `recommend_action_for`
or the human-review-boundary contract of `build_review_bundle` directly.

Pinned invariants (D.txt §D7-D8):

- `recommend_action_for` is **deterministic** from priority + confidence
  + risk + scope_class. The four boundaries:
  - `post_campaign_runtime_review_candidate` requires confidence ≥ 0.6,
    priority ≥ 0.05, AND scope ∈ {topology, solver_library, policy};
  - `review_for_future_PR` requires confidence ≥ 0.4 AND priority ≥ 0.02;
  - `archive_as_low_value` requires confidence < 0.30 AND priority < 0.01;
  - `wait_for_more_evidence` is the fall-through.
- `build_review_bundle` MUST embed the `human_review_boundary` text and
  the `why_no_runtime_mutation_occurred` explanation. These are the
  load-bearing strings that prevent shadow-only artifacts from being
  read as actionable runtime changes.
- `counts_by_recommended_next_human_action` MUST be internally
  consistent (sum equals number of proposals).
- Proposal blocks MUST preserve lifecycle, evidence_planes, why_now,
  why_human_review_required.
"""
from __future__ import annotations

import pytest

from waggledance.core.meta import (
    HUMAN_REVIEW_BOUNDARY_TEXT,
    META_SCHEMA_VERSION,
    RECOMMENDED_NEXT_HUMAN_ACTIONS,
)
from waggledance.core.meta.meta_learner import MetaProposal
from waggledance.core.meta.review_bundle import (
    build_review_bundle,
    recommend_action_for,
)


# --- helpers --------------------------------------------------------

def _proposal(
    *,
    meta_proposal_id: str = "abc123",
    proposal_type: str = "solver_family_growth",
    scope_class: str = "solver_library",
    impacted_cells: tuple[str, ...] = ("cell:hex_a",),
    evidence_planes: tuple[str, ...] = ("curiosity", "self_model"),
    confidence: float = 0.5,
    proposal_priority: float = 0.03,
    risk: str = "low",
    lifecycle_status: str = "new",
    why_now: str = "two planes converged",
    why_human_review_required: str = "human review required boundary text",
) -> MetaProposal:
    return MetaProposal(
        schema_version=META_SCHEMA_VERSION,
        meta_proposal_id=meta_proposal_id,
        proposal_type=proposal_type,
        scope_class=scope_class,
        impacted_cells=impacted_cells,
        evidence_planes=evidence_planes,
        evidence_strength=0.7,
        expected_value=0.7,
        confidence=confidence,
        proposal_priority=proposal_priority,
        cross_plane_support_factor=1.25,
        urgency_factor=1.0,
        uncertainty="medium",
        risk=risk,
        why_now=why_now,
        why_human_review_required=why_human_review_required,
        no_mutation_in_session=True,
        source_curiosity_ids=("C1",),
        source_tension_ids=("T1",),
        source_dream_meta_proposal_ids=(),
        source_resilience_refs=(),
        canonical_target="cell:hex_a",
        provenance={"branch_name": "test"},
        lifecycle_status=lifecycle_status,
        resolution_reason="n/a",
    )


# --- recommend_action_for: four-action boundary contract -----------

def test_recommend_action_post_campaign_when_high_confidence_actionable_scope():
    p = _proposal(confidence=0.7, proposal_priority=0.10,
                  scope_class="topology")
    assert recommend_action_for(p) == "post_campaign_runtime_review_candidate"


def test_recommend_action_post_campaign_requires_actionable_scope():
    """Even with high confidence + priority, a non-actionable scope
    (e.g. introspection) must NOT promote to post_campaign."""
    p = _proposal(confidence=0.9, proposal_priority=0.10,
                  scope_class="introspection")
    # scope ∉ {topology, solver_library, policy} → falls through.
    assert recommend_action_for(p) != "post_campaign_runtime_review_candidate"


def test_recommend_action_review_for_future_PR_at_mid_band():
    p = _proposal(confidence=0.5, proposal_priority=0.03,
                  scope_class="introspection")
    assert recommend_action_for(p) == "review_for_future_PR"


def test_recommend_action_archive_as_low_value_when_both_below_floor():
    p = _proposal(confidence=0.2, proposal_priority=0.005,
                  scope_class="topology")
    assert recommend_action_for(p) == "archive_as_low_value"


def test_recommend_action_wait_for_more_evidence_fallthrough():
    """Confidence below review_for_future_PR floor but priority
    above archive floor → fall-through to wait_for_more_evidence."""
    p = _proposal(confidence=0.35, proposal_priority=0.015,
                  scope_class="topology")
    assert recommend_action_for(p) == "wait_for_more_evidence"


def test_recommend_action_returns_only_allowlisted_values():
    """The four returned actions must always be in the allowlist."""
    actions = {
        recommend_action_for(_proposal(confidence=c, proposal_priority=pr,
                                       scope_class=s))
        for c in (0.1, 0.4, 0.7, 0.9)
        for pr in (0.001, 0.025, 0.10)
        for s in ("topology", "solver_library", "introspection", "archival")
    }
    assert actions.issubset(set(RECOMMENDED_NEXT_HUMAN_ACTIONS))


# --- build_review_bundle: human-review boundary contract -----------

def _bundle_kwargs() -> dict:
    return {
        "branch_name": "test/review-bundle",
        "base_commit_hash": "deadbeef",
        "pinned_input_manifest_sha256": "f" * 64,
        "consumed_hook_contracts": [{"name": "hook1", "version": "1"}],
        "fixture_fallback_used": {"any": False},
    }


def test_build_review_bundle_embeds_human_review_boundary_text():
    bundle = build_review_bundle(
        proposals=[],
        insufficient_evidence=[],
        rejected_candidates=[],
        resolved_proposal_ids=[],
        **_bundle_kwargs(),
    )
    # The exact boundary string must appear; this is the load-bearing
    # contract that prevents shadow artifacts being read as actionable.
    assert bundle["human_review_boundary"] == HUMAN_REVIEW_BOUNDARY_TEXT
    assert bundle["why_no_runtime_mutation_occurred"]
    assert "Session D" in bundle["why_no_runtime_mutation_occurred"]


def test_build_review_bundle_counts_are_internally_consistent():
    """sum(counts.values()) MUST equal len(proposals)."""
    proposals = [
        _proposal(meta_proposal_id="p1", confidence=0.7,
                  proposal_priority=0.10, scope_class="topology"),
        _proposal(meta_proposal_id="p2", confidence=0.5,
                  proposal_priority=0.03, scope_class="introspection"),
        _proposal(meta_proposal_id="p3", confidence=0.2,
                  proposal_priority=0.005, scope_class="topology"),
    ]
    bundle = build_review_bundle(
        proposals=proposals,
        insufficient_evidence=[],
        rejected_candidates=[],
        resolved_proposal_ids=[],
        **_bundle_kwargs(),
    )
    counts = bundle["counts_by_recommended_next_human_action"]
    assert sum(counts.values()) == 3
    # Every key in counts must be in the allowlist.
    assert set(counts.keys()).issubset(set(RECOMMENDED_NEXT_HUMAN_ACTIONS))


def test_build_review_bundle_proposal_blocks_preserve_metadata():
    p = _proposal(
        meta_proposal_id="m1",
        evidence_planes=("curiosity", "self_model", "dream"),
        lifecycle_status="persisting",
        why_now="three-plane convergence on hex_a",
        why_human_review_required="explicit boundary text required",
    )
    bundle = build_review_bundle(
        proposals=[p],
        insufficient_evidence=[],
        rejected_candidates=[],
        resolved_proposal_ids=[],
        **_bundle_kwargs(),
    )
    assert len(bundle["proposals"]) == 1
    block = bundle["proposals"][0]
    assert block["meta_proposal_id"] == "m1"
    assert block["lifecycle_status"] == "persisting"
    assert block["evidence_planes"] == ["curiosity", "self_model", "dream"]
    assert block["why_now"] == "three-plane convergence on hex_a"
    assert block["why_human_review_required"] == "explicit boundary text required"
    assert block["recommended_next_human_action"] in RECOMMENDED_NEXT_HUMAN_ACTIONS


def test_build_review_bundle_summary_text_reports_section_sizes():
    bundle = build_review_bundle(
        proposals=[
            _proposal(meta_proposal_id="p1"),
            _proposal(meta_proposal_id="p2"),
        ],
        insufficient_evidence=[
            {"candidate_target": "cell:i1", "missing_planes": ["dream"],
             "evidence_strength_seen": 0.4, "why_below_threshold": "single"},
        ],
        rejected_candidates=[
            {"candidate_target": "cell:r1", "rejection_reason": "weak"},
            {"candidate_target": "cell:r2", "rejection_reason": "weak"},
        ],
        resolved_proposal_ids=["resolved1"],
        **_bundle_kwargs(),
    )
    s = bundle["summary_text"]
    assert "2 bounded self-proposals" in s
    assert "1 insufficient-evidence" in s
    assert "2 rejected" in s
    assert "1 resolved since last run" in s


def test_build_review_bundle_carries_provenance_and_consumed_hooks():
    bundle = build_review_bundle(
        proposals=[],
        insufficient_evidence=[],
        rejected_candidates=[],
        resolved_proposal_ids=[],
        **_bundle_kwargs(),
    )
    prov = bundle["provenance"]
    assert prov["branch_name"] == "test/review-bundle"
    assert prov["base_commit_hash"] == "deadbeef"
    assert prov["pinned_input_manifest_sha256"] == "f" * 64
    # consumed_hook_contracts shape preserved as a list.
    assert bundle["consumed_hook_contracts"] == [{"name": "hook1", "version": "1"}]


def test_build_review_bundle_resolved_proposals_carry_meta_proposal_id():
    bundle = build_review_bundle(
        proposals=[],
        insufficient_evidence=[],
        rejected_candidates=[],
        resolved_proposal_ids=["abc", "def"],
        **_bundle_kwargs(),
    )
    resolved = bundle["resolved_proposals"]
    assert {r["meta_proposal_id"] for r in resolved} == {"abc", "def"}
    # All resolved entries carry a resolution_reason field even if "unknown".
    assert all("resolution_reason" in r for r in resolved)

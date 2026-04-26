"""Rollout planner — Phase 9 §O.

Pure: produces a deterministic rollout_plan dict from a meta-proposal.
NEVER applies anything to runtime.
"""
from __future__ import annotations


def plan_rollout(proposal: dict) -> dict:
    ptype = str(proposal.get("proposal_type") or "")
    is_runtime_touching = ptype in (
        "solver_family_growth",
        "solver_family_consolidation",
        "policy_gate_adjustment",
        "topology_subdivision",
    )
    return {
        "stages": [
            "human_review",
            "post_campaign_runtime_candidate",
            "canary_cell" if is_runtime_touching else "review_only",
            "limited_runtime" if is_runtime_touching else "review_only",
            "full_runtime" if is_runtime_touching else "review_only",
        ],
        "human_approval_gates": [
            "human_review",
            "post_campaign_runtime_candidate",
            "canary_cell",
            "limited_runtime",
            "full_runtime",
        ],
        "shadow_first": True,
        "min_canary_observation_window_seconds": 3600,
        "min_limited_runtime_observation_window_seconds": 86400,
    }


def plan_rollback(proposal: dict) -> dict:
    return {
        "rollback_target_stage": "post_campaign_runtime_candidate",
        "trigger_conditions": [
            "critical_regression_detected",
            "calibration_drift_overconfident",
            "human_revoke_approval",
        ],
        "rollback_artifacts_required": [
            "previous_axiom_yaml_backup",
            "vector_provenance_snapshot",
        ],
        "shadow_first": True,
    }

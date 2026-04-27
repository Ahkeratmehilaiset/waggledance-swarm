# SPDX-License-Identifier: BUSL-1.1
"""Promotion ladder — Phase 9 §M.

Formal path from curiosity → tension → dream target → stochastic
external proposal → deterministic collapse → shadow graph → replay
→ meta-proposal → human review → post-campaign runtime candidate
→ canary cell → limited runtime → full runtime / rollback.

CRITICAL CONTRACT (Prompt_1_Master §M):
- every stage has explicit criteria
- no automatic runtime promotion in this phase
- no bypasses
- every stage represented in IR promotion_state
"""

PROMOTION_SCHEMA_VERSION = 1

# 14 stages in the ladder (Prompt_1_Master §M)
STAGES = (
    "curiosity",
    "tension",
    "dream_target",
    "stochastic_external_proposal",
    "deterministic_collapse",
    "shadow_graph",
    "replay",
    "meta_proposal",
    "human_review",
    "post_campaign_runtime_candidate",
    "canary_cell",
    "limited_runtime",
    "full_runtime",
    "archived",
)

# Every transition must satisfy ALL criteria for the target stage.
STAGE_CRITERIA: dict[str, tuple[str, ...]] = {
    "tension": ("from_stage_is_curiosity",),
    "dream_target": ("from_stage_is_tension",
                      "tension_resolution_path_deferred_to_dream"),
    "stochastic_external_proposal": ("from_stage_is_dream_target",),
    "deterministic_collapse": ("from_stage_is_stochastic",
                                "passes_proposal_gate"),
    "shadow_graph": ("from_stage_is_deterministic_collapse",
                      "shadow_only_admit"),
    "replay": ("from_stage_is_shadow_graph",
                "replay_methodology_acknowledged"),
    "meta_proposal": ("from_stage_is_replay",
                       "structurally_promising"),
    "human_review": ("from_stage_is_meta_proposal",),
    "post_campaign_runtime_candidate": (
        "from_stage_is_human_review",
        "human_approval_id_present",
    ),
    "canary_cell": (
        "from_stage_is_post_campaign_runtime_candidate",
        "human_approval_id_present",
        "campaign_finished_or_frozen",
    ),
    "limited_runtime": (
        "from_stage_is_canary_cell",
        "human_approval_id_present",
        "canary_observation_window_passed",
    ),
    "full_runtime": (
        "from_stage_is_limited_runtime",
        "human_approval_id_present",
        "limited_runtime_observation_window_passed",
        "no_critical_regressions",
    ),
    "archived": (),   # archive can happen from any stage; rationale required
}

# Runtime stages — these are gated on the SEPARATE Phase Z prompt
RUNTIME_STAGES = (
    "post_campaign_runtime_candidate",
    "canary_cell",
    "limited_runtime",
    "full_runtime",
)

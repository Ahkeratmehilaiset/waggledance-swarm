"""Cognition IR — Phase 9 §G.

Common language for ALL cognitive objects across phases A-D and the
phase-9 autonomy fabric. Adapters convert per-session outputs into
IR; consumers (action_gate, scheduler, promotion_ladder, etc.)
operate on IR exclusively.
"""

IR_SCHEMA_VERSION = 1
IR_COMPAT_VERSION = 1

IR_TYPES = (
    "curiosity",
    "tension",
    "blind_spot",
    "dream_target",
    "shadow_candidate",
    "meta_proposal",
    "capability",
    "review_candidate",
    "approved_change",
    "repair_request",
    "solver_candidate",
    "builder_request",
    "builder_result",
    "learning_suggestion",
)

LIFECYCLE_STATUSES = (
    "new", "active", "persisting", "resolved", "archived", "advisory",
)

PROMOTION_STATES = (
    "candidate",
    "supportive",
    "shadow",
    "review_ready",
    "post_campaign_runtime_review_candidate",
    "approved",
    "rejected",
    "archived",
)

DEPENDENCY_RELATIONS = (
    "supports",
    "extends",
    "specializes",
    "alternates_with",
    "contradicts",
    "refines",
    "composes_with",
    "blocks_until_resolved",
)

SOURCE_SESSIONS = (
    "A_curiosity",
    "B_self_model",
    "C_dream",
    "D_hive_proposes",
    "R7_5_vector_chaos",
    "phase9_kernel",
    "phase9_ir",
    "phase9_capsules",
    "external",
)

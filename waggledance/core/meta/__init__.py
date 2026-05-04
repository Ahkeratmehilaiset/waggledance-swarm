# SPDX-License-Identifier: BUSL-1.1
"""WD meta-learner package — Phase 8.5 Session D.

Crown-jewel area per D.txt §BUSL: any non-trivial logic edit here
requires the LICENSE-BUSL.txt Change Date update to 2030-03-19 in
the same commit.

Strict scope (D.txt §SESSION MODE):
- bounded self-proposal generation
- deterministic evidence-plane aggregation
- machine-readable review handoff
- explicit human-review boundary

Out of scope:
- automatic merge/apply
- self-rewrite
- runtime mutation
- code generation
"""

META_SCHEMA_VERSION = 1

# Allowed enums for meta-proposal records (must mirror
# schemas/meta_proposal.schema.json).
PROPOSAL_TYPES = (
    "topology_subdivision",
    "solver_family_growth",
    "solver_family_consolidation",
    "policy_gate_adjustment",
    "introspection_gap",
    "archival_cleanup",
    "infrastructure_followup",
)

SCOPE_CLASSES = (
    "topology",
    "solver_library",
    "policy",
    "introspection",
    "archival",
    "infrastructure",
    "review_only",
)

LIFECYCLE_STATUSES = ("new", "persisting", "resolved")

RESOLUTION_REASONS = (
    "evidence_weakened",
    "human_archived",
    "underlying_issue_resolved",
    "unknown",
    "n/a",
)

EVIDENCE_PLANES = ("curiosity", "self_model", "dream", "resilience")

PRIMARY_PLANES = ("curiosity", "self_model", "dream")

RECOMMENDED_NEXT_HUMAN_ACTIONS = (
    "review_for_future_PR",
    "archive_as_low_value",
    "wait_for_more_evidence",
    "post_campaign_runtime_review_candidate",
)

# Mandatory boundary text on every review bundle artifact (D.txt §D8)
HUMAN_REVIEW_BOUNDARY_TEXT = (
    "This artifact is shadow-only. Every entry requires human review "
    "before any runtime promotion. No automatic merging is performed by "
    "Session D code. Runtime flip is out of scope until a later gated "
    "session with explicit permission."
)

# Cap on cross_plane_support_factor (D.txt §D5)
CROSS_PLANE_SUPPORT_FACTOR_CAP = 1.75

# proposal_priority is an UNCLAMPED ranking score; documented in
# META_PROPOSAL_FORMULAS.md.

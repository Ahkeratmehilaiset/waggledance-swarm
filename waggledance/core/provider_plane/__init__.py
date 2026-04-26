# SPDX-License-Identifier: BUSL-1.1
"""Multi-provider communication plane — Phase 9 §J.

Allows WD to autonomously communicate with Claude Code (priority 1),
Anthropic APIs, GPT APIs, and local model services from day 1 — but
strictly through request/response packs, NEVER directly mutating
self/world models.

CRITICAL CONTRACT (Prompt_1_Master §J):
- Claude Code is preferred for code/repair/repository-aware tasks
- but is NOT WD's irreducible inner runtime cognition primitive
- WD must remain operational if all providers go away
- offline replay over consultation cache is always possible
"""

PROVIDER_PLANE_SCHEMA_VERSION = 1

PROVIDERS = (
    "claude_code_builder_lane",
    "anthropic_api",
    "gpt_api",
    "local_model_service",
)

TASK_CLASSES = (
    "code_or_repair",
    "spec_or_critique",
    "bulk_classification",
)

# Default priority order per task class (Prompt_1_Master §J)
TASK_CLASS_PRIORITY = {
    "code_or_repair": (
        "claude_code_builder_lane",
        "anthropic_api",
        "gpt_api",
        "local_model_service",
    ),
    "spec_or_critique": (
        "anthropic_api",
        "claude_code_builder_lane",
        "gpt_api",
        "local_model_service",
    ),
    "bulk_classification": (
        "local_model_service",
        "anthropic_api",
        "gpt_api",
        "claude_code_builder_lane",
    ),
}

# 6-layer trust gate states (Prompt_1_Master §J)
TRUST_LAYERS = (
    "raw_quarantine",
    "internal_consistency_passed",
    "cross_check_passed",
    "corroborated",
    "calibration_threshold_passed",
    "human_gated",
)

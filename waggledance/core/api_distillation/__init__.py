"""API distillation core — Phase 9 §J.

Provider responses NEVER directly mutate self_model or world_model.
All external answers go through normalize → extract → distill →
6-layer trust gate → local store updates.

Crown-jewel area waggledance/core/api_distillation/*
(BUSL Change Date 2030-03-19).
"""

API_DISTILLATION_SCHEMA_VERSION = 1

# 6-layer trust gate per Prompt_1_Master §J
TRUST_GATE_LAYERS = (
    "raw_quarantine",
    "internal_consistency",
    "existing_knowledge_cross_check",
    "multi_source_corroboration",
    "calibration_threshold",
    "human_gated",
)

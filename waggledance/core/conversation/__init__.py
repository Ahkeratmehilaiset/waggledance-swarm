"""Conversation layer — Phase 9 §V.

Surfaces memory, uncertainty, and continuity in WD's outputs. Reads
identity/personality.yaml + voice_profile.yaml + forbidden_patterns.yaml
+ presence_log entries to produce calibrated responses.

Crown-jewel area waggledance/core/conversation/*
(BUSL Change Date 2030-03-19).
"""

CONVERSATION_SCHEMA_VERSION = 1

PRESENCE_KINDS = (
    "turn",
    "observation",
    "reflection",
    "uncertainty_surfaced",
    "blind_spot_surfaced",
    "delta_since_last",
    "learning_intent",
)

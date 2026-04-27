# SPDX-License-Identifier: BUSL-1.1
"""Conversation layer — Phase 9 §V.

Surfaces memory, uncertainty, and continuity in WD's outputs. Reads
identity/personality.yaml + voice_profile.yaml + forbidden_patterns.yaml
+ presence_log entries to produce calibrated responses.
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

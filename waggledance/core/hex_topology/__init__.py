# SPDX-License-Identifier: BUSL-1.1
"""Hex runtime topology — Phase 9 §K.

Turns hex cells from taxonomy into real runtime topology. Each cell
carries its own curiosity / self-model / vector / event-log /
proposal-queue / dream-queue / local-budget / builder-lane shards.

CRITICAL CONTRACT (Prompt_1_Master §K):
- subdivision is a controlled primitive
- shadow-first before live usage
- no runtime mutation in this session
- cell topology remains domain-neutral
"""

HEX_RUNTIME_SCHEMA_VERSION = 1

LIVE_STATES = ("shadow_only", "canary", "live", "deprecated")
SUBDIVISION_STATES = (
    "leaf", "subdivided", "subdivision_planned", "subdivision_in_shadow",
)

REQUIRED_SHARDS = (
    "curiosity", "self_model", "vector", "event_log",
    "proposal_queue", "dream_queue", "local_budget",
)
OPTIONAL_SHARDS = ("builder_lane_queue",)

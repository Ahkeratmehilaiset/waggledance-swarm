"""Memory tiering — Phase 9 §L.

Continuously make memory faster while preserving foundational
knowledge. Four tiers: hot → warm → cold → glacier. Foundational
identity anchors are PINNED and never demote to cold/glacier.

CRITICAL OUT-OF-SCOPE: generative memory compression is deferred to
Phase 12+. This phase is mechanical tiering only.
"""

MEMORY_TIERING_SCHEMA_VERSION = 1

TIERS = ("hot", "warm", "cold", "glacier")

# Default thresholds (capsule manifests may narrow)
HOT_MIN_ACCESS_PER_DAY = 5
WARM_MIN_ACCESS_PER_WEEK = 1
COLD_MAX_INACTIVE_DAYS = 30
GLACIER_AFTER_DAYS_INACTIVE = 90

PROMOTABLE_ANCHOR_STATUSES_PINNED = ("foundational", "supportive")

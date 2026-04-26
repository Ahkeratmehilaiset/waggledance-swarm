"""Capsule registry — Phase 9 §G.

A capsule represents one deployment context (factory_v1,
personal_v1, research_v1, home_v1, cottage_v1, gadget_v1, or
neutral_v1). The core kernel treats capsules as DATA, not as
hardcoded business logic — domain-specific semantics belong in
capsule manifests, never in core modules.
"""

CAPSULE_KINDS = (
    "factory_v1",
    "personal_v1",
    "research_v1",
    "home_v1",
    "cottage_v1",
    "gadget_v1",
    "neutral_v1",
)

CAPSULE_SCHEMA_VERSION = 1

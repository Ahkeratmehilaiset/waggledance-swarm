"""Capsule resolver — Phase 9 §G.

Resolves an IRObject (or any capsule_context-bearing artifact) into
a CapsuleManifest, validates blast-radius isolation, and provides
the lane / plane filters consumers need.

CRITICAL BLAST RADIUS RULE (Prompt_1_Master §G):
- corruption or blockage in one capsule must NOT poison all capsules
- cross-capsule learning may only use explicit abstracted patterns,
  not raw leakage
- this resolver enforces the membership boundary; cross-capsule
  observers (Phase Q) handle pattern abstraction.
"""
from __future__ import annotations

from typing import Iterable

from .capsule_registry import (
    CapsuleManifest,
    CapsuleRegistry,
    CapsuleValidationError,
)


def resolve_capsule(registry: CapsuleRegistry,
                          capsule_context: str
                          ) -> CapsuleManifest | None:
    """Find the manifest for the given context.

    Resolution strategy:
    1. exact capsule_id match
    2. fall back to neutral_v1 if registered (always domain-neutral)
    3. None if no match
    """
    direct = registry.get(capsule_context)
    if direct is not None:
        return direct
    return registry.get("neutral_v1")


def is_lane_allowed(manifest: CapsuleManifest | None,
                          lane: str) -> bool:
    """If the manifest specifies allowed_lanes, only those are
    allowed. If the manifest is None or specifies an empty list,
    all lanes are allowed (default-permit for missing manifests is
    intentional for the bootstrap capsule)."""
    if manifest is None:
        return True
    if not manifest.allowed_lanes:
        return True
    return lane in manifest.allowed_lanes


def is_plane_subscribed(manifest: CapsuleManifest | None,
                              plane: str) -> bool:
    if manifest is None:
        return True
    if not manifest.evidence_planes_subscribed:
        return True
    return plane in manifest.evidence_planes_subscribed


# ── Blast-radius isolation ───────────────────────────────────────-

class BlastRadiusViolation(ValueError):
    """Raised when an operation tries to leak data across capsules
    without going through the explicit cross-capsule observer."""


def assert_no_cross_capsule_leak(*,
                                       source_capsule_context: str,
                                       target_capsule_context: str,
                                       allow_neutral: bool = True,
                                       ) -> None:
    """Raise BlastRadiusViolation if source != target unless one side
    is the neutral_v1 capsule (which is allowed to flow both ways).
    Cross-capsule learning must go through cross_capsule_observer."""
    if source_capsule_context == target_capsule_context:
        return
    if allow_neutral and (
        source_capsule_context == "neutral_v1"
        or target_capsule_context == "neutral_v1"
    ):
        return
    raise BlastRadiusViolation(
        f"cross-capsule leakage refused: {source_capsule_context!r} → "
        f"{target_capsule_context!r}; route via cross_capsule_observer"
    )


def filter_to_capsule(items: Iterable, *,
                            capsule_context: str,
                            attr_name: str = "capsule_context"
                            ) -> list:
    """Return only items whose capsule_context matches. neutral_v1
    items are always included (they are the shared substrate)."""
    out = []
    for item in items:
        ctx = getattr(item, attr_name, None) or item.get(attr_name) \
            if isinstance(item, dict) else getattr(item, attr_name, None)
        if ctx == capsule_context or ctx == "neutral_v1":
            out.append(item)
    return out

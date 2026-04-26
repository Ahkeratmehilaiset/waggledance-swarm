"""Phase 9 §Q — Cross-Capsule Observer + High-Risk Shadow Modes.

This package extracts ABSTRACT structural patterns across capsules
without leaking raw capsule data. It is intentionally narrow:
- it observes capsule signal summaries, never raw text
- it produces deterministic, redacted pattern records
- it never mutates a capsule and never feeds an LLM
"""
from __future__ import annotations

from .cross_capsule_observer import (
    CrossCapsuleObserver,
    CapsuleSignalSummary,
    CrossCapsuleObservation,
    CrossCapsuleObserverError,
    OBSERVABLE_PATTERN_KINDS,
)
from .abstract_pattern_registry import (
    AbstractPatternRecord,
    AbstractPatternRegistry,
    AbstractPatternRegistryError,
)


__all__ = [
    "CrossCapsuleObserver",
    "CapsuleSignalSummary",
    "CrossCapsuleObservation",
    "CrossCapsuleObserverError",
    "OBSERVABLE_PATTERN_KINDS",
    "AbstractPatternRecord",
    "AbstractPatternRegistry",
    "AbstractPatternRegistryError",
]

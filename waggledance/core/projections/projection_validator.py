# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Projection Validator — validates projection outputs against ground truth (v3.2).

Validates projection references against live world state:
  - Unknown entity references
  - Stale goal references
  - Invalid motive IDs
  - Timestamp staleness

Key rule: no hidden dependencies.
If goal validation is needed, goal_engine must be passed explicitly.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("waggledance.projections.validator")


# ── Config ───────────────────────────────────────────────────

DEFAULT_STALE_SECONDS = 300  # 5 minutes


# ── Data types ───────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Result of projection validation."""
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "checked_at": self.checked_at,
        }


# ── Core function ────────────────────────────────────────────

def validate_projection(
    projection: Dict[str, Any],
    known_entity_ids: Optional[Set[str]] = None,
    known_goal_ids: Optional[Set[str]] = None,
    known_motive_ids: Optional[Set[str]] = None,
    max_age_seconds: float = DEFAULT_STALE_SECONDS,
) -> ValidationResult:
    """
    Validate a projection dict against known state.

    Args:
        projection: The projection output dict to validate.
        known_entity_ids: Set of valid entity IDs (from world model).
        known_goal_ids: Set of valid goal IDs (from goal engine — explicit!).
        known_motive_ids: Set of valid motive IDs (from motive registry).
        max_age_seconds: Maximum allowed age of the projection timestamp.

    Returns:
        ValidationResult with valid flag, errors, and warnings.
    """
    result = ValidationResult()
    now = time.time()

    # ── Timestamp check ──
    ts = projection.get("timestamp")
    if ts is not None:
        age = now - ts
        if age > max_age_seconds:
            result.warnings.append(
                f"Projection is stale: {age:.0f}s old (max {max_age_seconds:.0f}s)"
            )
    else:
        result.warnings.append("Projection has no timestamp")

    # ── Entity references ──
    entity_refs = projection.get("entity_ids", [])
    if known_entity_ids is not None and entity_refs:
        unknown = [eid for eid in entity_refs if eid not in known_entity_ids]
        if unknown:
            result.errors.append(f"Unknown entity references: {unknown}")
            result.valid = False

    # ── Goal references ──
    goal_refs = projection.get("goal_ids", [])
    if known_goal_ids is not None and goal_refs:
        unknown = [gid for gid in goal_refs if gid not in known_goal_ids]
        if unknown:
            result.errors.append(f"Unknown goal references: {unknown}")
            result.valid = False

    # ── Motive references ──
    motive_refs = projection.get("motive_ids", [])
    if known_motive_ids is not None and motive_refs:
        unknown = [mid for mid in motive_refs if mid not in known_motive_ids]
        if unknown:
            result.errors.append(f"Unknown motive references: {unknown}")
            result.valid = False

    # ── Required fields ──
    if "narrative" not in projection and "data" not in projection:
        result.warnings.append("Projection has no 'narrative' or 'data' field")

    if result.errors:
        log.warning("Projection validation failed: %s", result.errors)

    return result

# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Attention Budget — resource-aware attention allocation (v3.2).

Dynamic allocation across four categories:
  - critical:    solver / user-facing (40% base)
  - normal:      standard pipeline work (35% base)
  - background:  night learning, canary, maintenance (15% base)
  - reflection:  self-entity update, epistemic uncertainty, curiosity (10% base)

Reallocation rules:
  - >80% load:  reflection→5%, background→5%, normal→50%
  - >95% load:  reflection→2%, background→0%, normal→20%
  - uncertainty >0.6: shift 5% from background→reflection (if possible)
  - Reflection NEVER falls to 0% (hard min = reflection_min_pct)
  - Critical path NEVER starves (hard min = critical_pct)

Feature-flagged: attention section in configs/settings.yaml
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger("waggledance.autonomy.attention_budget")


# ── Defaults matching configs/settings.yaml ─────────────────

DEFAULT_CRITICAL_PCT = 40
DEFAULT_NORMAL_PCT = 35
DEFAULT_BACKGROUND_PCT = 15
DEFAULT_REFLECTION_PCT = 10
DEFAULT_HIGH_LOAD = 0.80
DEFAULT_EMERGENCY_LOAD = 0.95
DEFAULT_REFLECTION_MIN_PCT = 2


# ── Data types ───────────────────────────────────────────────

@dataclass
class AttentionAllocation:
    """Current attention budget allocation (percentages that sum to 100)."""
    critical: int = DEFAULT_CRITICAL_PCT
    normal: int = DEFAULT_NORMAL_PCT
    background: int = DEFAULT_BACKGROUND_PCT
    reflection: int = DEFAULT_REFLECTION_PCT
    load_factor: float = 0.0
    uncertainty_factor: float = 0.0
    reason: str = "base allocation"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "critical": self.critical,
            "normal": self.normal,
            "background": self.background,
            "reflection": self.reflection,
            "load_factor": round(self.load_factor, 3),
            "uncertainty_factor": round(self.uncertainty_factor, 3),
            "reason": self.reason,
        }

    @property
    def total(self) -> int:
        return self.critical + self.normal + self.background + self.reflection


# ── Core class ───────────────────────────────────────────────

class AttentionBudget:
    """
    Resource-aware attention budget allocator.

    Reads load and uncertainty signals to dynamically reallocate
    attention across critical/normal/background/reflection buckets.
    """

    def __init__(
        self,
        critical_pct: int = DEFAULT_CRITICAL_PCT,
        normal_pct: int = DEFAULT_NORMAL_PCT,
        background_pct: int = DEFAULT_BACKGROUND_PCT,
        reflection_pct: int = DEFAULT_REFLECTION_PCT,
        high_load_threshold: float = DEFAULT_HIGH_LOAD,
        emergency_load_threshold: float = DEFAULT_EMERGENCY_LOAD,
        reflection_min_pct: int = DEFAULT_REFLECTION_MIN_PCT,
    ):
        self._base_critical = critical_pct
        self._base_normal = normal_pct
        self._base_background = background_pct
        self._base_reflection = reflection_pct
        self._high_load = high_load_threshold
        self._emergency_load = emergency_load_threshold
        self._reflection_min = reflection_min_pct

        self._current = AttentionAllocation(
            critical=critical_pct,
            normal=normal_pct,
            background=background_pct,
            reflection=reflection_pct,
        )

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "AttentionBudget":
        """Create from settings.yaml attention section."""
        att = config.get("attention", {})
        return cls(
            critical_pct=att.get("critical_pct", DEFAULT_CRITICAL_PCT),
            normal_pct=att.get("normal_pct", DEFAULT_NORMAL_PCT),
            background_pct=att.get("background_pct", DEFAULT_BACKGROUND_PCT),
            reflection_pct=att.get("reflection_pct", DEFAULT_REFLECTION_PCT),
            high_load_threshold=att.get("high_load_threshold", DEFAULT_HIGH_LOAD),
            emergency_load_threshold=att.get("emergency_load_threshold", DEFAULT_EMERGENCY_LOAD),
            reflection_min_pct=att.get("reflection_min_pct", DEFAULT_REFLECTION_MIN_PCT),
        )

    # ── Query ─────────────────────────────────────────────

    @property
    def current(self) -> AttentionAllocation:
        """Return current attention allocation."""
        return self._current

    def query(self) -> Dict[str, Any]:
        """Return current allocation as dict (for API/dashboard)."""
        return self._current.to_dict()

    # ── Reallocation ──────────────────────────────────────

    def reallocate(
        self,
        load_factor: float = 0.0,
        uncertainty_factor: float = 0.0,
    ) -> AttentionAllocation:
        """
        Reallocate attention based on load and uncertainty signals.

        Args:
            load_factor: system load 0.0–1.0 (from ResourceKernel)
            uncertainty_factor: epistemic uncertainty 0.0–1.0 (from WorldModel)

        Returns:
            Updated AttentionAllocation.
        """
        critical = self._base_critical
        normal = self._base_normal
        background = self._base_background
        reflection = self._base_reflection
        reasons = []

        # ── Load-based reallocation ──
        if load_factor >= self._emergency_load:
            # Emergency: shed background, minimize reflection
            reflection = self._reflection_min
            background = 0
            # Redistribute freed capacity to critical + normal
            freed = self._base_reflection - reflection + self._base_background
            critical = self._base_critical
            normal = self._base_normal + freed
            # Cap: total must be 100
            normal = 100 - critical - background - reflection
            reasons.append(f"emergency load ({load_factor:.0%})")

        elif load_factor >= self._high_load:
            # High load: reduce reflection and background
            reflection = max(self._reflection_min, 5)
            background = 5
            critical = self._base_critical
            normal = 100 - critical - background - reflection
            reasons.append(f"high load ({load_factor:.0%})")

        # ── Uncertainty-based shift ──
        if uncertainty_factor > 0.6 and background >= 5:
            shift = min(5, background)
            background -= shift
            reflection += shift
            reasons.append(f"high uncertainty ({uncertainty_factor:.2f})")

        # ── Enforce hard minimums ──
        if reflection < self._reflection_min:
            deficit = self._reflection_min - reflection
            reflection = self._reflection_min
            # Take from normal (largest pool)
            normal = max(0, normal - deficit)
            reasons.append("reflection floor enforced")

        if critical < self._base_critical:
            deficit = self._base_critical - critical
            critical = self._base_critical
            normal = max(0, normal - deficit)
            reasons.append("critical floor enforced")

        # ── Normalize to 100 ──
        total = critical + normal + background + reflection
        if total != 100:
            # Adjust normal to balance
            normal += (100 - total)
            normal = max(0, normal)

        reason = "; ".join(reasons) if reasons else "base allocation"

        self._current = AttentionAllocation(
            critical=critical,
            normal=normal,
            background=background,
            reflection=reflection,
            load_factor=load_factor,
            uncertainty_factor=uncertainty_factor,
            reason=reason,
        )

        if reasons:
            log.info("Attention reallocated: %s → %s", reason, self._current.to_dict())

        return self._current

    # ── Continuity floor check ────────────────────────────

    def continuity_floor_held(self) -> bool:
        """Check that hard minimums are maintained."""
        return (
            self._current.reflection >= self._reflection_min
            and self._current.critical >= self._base_critical
        )

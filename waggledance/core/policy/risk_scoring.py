"""
Risk Scoring — builds RiskScore for actions based on context.

Computes the 5-dimension risk profile for any action:
  - severity: how bad if it goes wrong
  - reversibility: can we undo it
  - observability: can we detect outcome
  - uncertainty: how uncertain are inputs
  - blast_radius: how many things affected
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
    RiskScore,
)

log = logging.getLogger("waggledance.policy.risk_scoring")

# Default risk profiles per capability category
_CATEGORY_RISK_DEFAULTS: Dict[str, Dict[str, float]] = {
    "sense": {"severity": 0.0, "reversibility": 1.0, "observability": 1.0, "uncertainty": 0.1, "blast_radius": 0.0},
    "normalize": {"severity": 0.0, "reversibility": 1.0, "observability": 1.0, "uncertainty": 0.05, "blast_radius": 0.0},
    "retrieve": {"severity": 0.0, "reversibility": 1.0, "observability": 1.0, "uncertainty": 0.1, "blast_radius": 0.0},
    "estimate": {"severity": 0.1, "reversibility": 1.0, "observability": 0.8, "uncertainty": 0.3, "blast_radius": 0.1},
    "detect": {"severity": 0.1, "reversibility": 1.0, "observability": 0.9, "uncertainty": 0.2, "blast_radius": 0.1},
    "predict": {"severity": 0.2, "reversibility": 1.0, "observability": 0.7, "uncertainty": 0.4, "blast_radius": 0.1},
    "solve": {"severity": 0.1, "reversibility": 1.0, "observability": 0.9, "uncertainty": 0.1, "blast_radius": 0.1},
    "verify": {"severity": 0.0, "reversibility": 1.0, "observability": 1.0, "uncertainty": 0.1, "blast_radius": 0.0},
    "explain": {"severity": 0.1, "reversibility": 1.0, "observability": 0.8, "uncertainty": 0.3, "blast_radius": 0.1},
    "plan": {"severity": 0.2, "reversibility": 0.8, "observability": 0.8, "uncertainty": 0.3, "blast_radius": 0.2},
    "optimize": {"severity": 0.5, "reversibility": 0.5, "observability": 0.7, "uncertainty": 0.4, "blast_radius": 0.4},
    "act": {"severity": 0.6, "reversibility": 0.4, "observability": 0.6, "uncertainty": 0.3, "blast_radius": 0.3},
    "learn": {"severity": 0.2, "reversibility": 0.7, "observability": 0.8, "uncertainty": 0.2, "blast_radius": 0.1},
}


class RiskScorer:
    """
    Computes RiskScore for actions based on capability, quality path,
    and context factors.
    """

    def __init__(self):
        self._overrides: Dict[str, Dict[str, float]] = {}

    def set_override(self, capability_id: str, overrides: Dict[str, float]):
        """Set risk overrides for a specific capability."""
        self._overrides[capability_id] = overrides

    def score(
        self,
        capability: Optional[CapabilityContract] = None,
        quality_path: str = "bronze",
        context: Optional[Dict[str, Any]] = None,
    ) -> RiskScore:
        """
        Compute RiskScore for an action.

        Args:
            capability: the capability being invoked
            quality_path: gold/silver/bronze (affects uncertainty)
            context: additional context (e.g., emergency, target entity count)
        """
        context = context or {}
        category = capability.category.value if capability else "act"

        # Start from category defaults
        defaults = _CATEGORY_RISK_DEFAULTS.get(category, _CATEGORY_RISK_DEFAULTS["act"])
        severity = defaults["severity"]
        reversibility = defaults["reversibility"]
        observability = defaults["observability"]
        uncertainty = defaults["uncertainty"]
        blast_radius = defaults["blast_radius"]

        # Apply capability-specific overrides
        if capability and capability.capability_id in self._overrides:
            ovr = self._overrides[capability.capability_id]
            severity = ovr.get("severity", severity)
            reversibility = ovr.get("reversibility", reversibility)
            observability = ovr.get("observability", observability)
            uncertainty = ovr.get("uncertainty", uncertainty)
            blast_radius = ovr.get("blast_radius", blast_radius)

        # Adjust for quality path
        if quality_path == "gold":
            uncertainty = max(0.0, uncertainty - 0.2)
        elif quality_path == "bronze":
            uncertainty = min(1.0, uncertainty + 0.2)

        # Adjust for rollback possibility
        if capability and not capability.rollback_possible:
            reversibility = max(0.0, reversibility - 0.3)

        # Context adjustments
        if context.get("emergency"):
            # Emergency situations lower approval threshold
            severity = max(0.0, severity - 0.1)
        if context.get("entity_count", 1) > 5:
            blast_radius = min(1.0, blast_radius + 0.2)

        approval = context.get("approval_required", False)

        return RiskScore(
            severity=round(severity, 3),
            reversibility=round(reversibility, 3),
            observability=round(observability, 3),
            uncertainty=round(uncertainty, 3),
            blast_radius=round(blast_radius, 3),
            approval_required=approval,
        )

    def score_for_action(
        self,
        action: Action,
        capability: Optional[CapabilityContract] = None,
        quality_path: str = "bronze",
        context: Optional[Dict[str, Any]] = None,
    ) -> RiskScore:
        """Score and attach risk to an existing Action."""
        risk = self.score(capability, quality_path, context)
        action.risk_score = risk
        return risk

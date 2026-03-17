"""
Verifier — validates action outcomes against world state.

The verifier compares world snapshots before and after an action:
  - Did residuals improve?
  - Were success criteria met?
  - Any unexpected side effects?
  - Confidence in the outcome?

Returns VerifierResult used for CaseTrajectory quality grading.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityContract,
    WorldSnapshot,
)

log = logging.getLogger("waggledance.reasoning.verifier")


@dataclass
class VerifierResult:
    """Outcome of verification."""
    passed: bool
    confidence: float = 0.5  # 0-1
    residual_improvement: float = 0.0  # positive = improved
    success_criteria_met: List[str] = field(default_factory=list)
    success_criteria_failed: List[str] = field(default_factory=list)
    side_effects: List[str] = field(default_factory=list)
    conflict: bool = False
    hallucination: bool = False
    details: Dict[str, Any] = field(default_factory=dict)
    verified_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "confidence": self.confidence,
            "residual_improvement": self.residual_improvement,
            "success_criteria_met": self.success_criteria_met,
            "success_criteria_failed": self.success_criteria_failed,
            "side_effects": self.side_effects,
            "conflict": self.conflict,
            "hallucination": self.hallucination,
        }


class Verifier:
    """
    Validates action outcomes by comparing world state before and after.

    Verification layers:
      1. Residual comparison (did the metric improve?)
      2. Success criteria check (did the capability meet its contract?)
      3. Side effect detection (did anything unexpected change?)
      4. Conflict detection (do results contradict each other?)
    """

    def __init__(self, residual_threshold: float = 0.05):
        self._residual_threshold = residual_threshold
        self._verification_log: List[VerifierResult] = []

    def verify(
        self,
        action: Action,
        capability: Optional[CapabilityContract] = None,
        snapshot_before: Optional[WorldSnapshot] = None,
        snapshot_after: Optional[WorldSnapshot] = None,
        action_result: Optional[Dict[str, Any]] = None,
    ) -> VerifierResult:
        """
        Verify an action's outcome.

        Args:
            action: the executed action
            capability: the capability contract (for success criteria)
            snapshot_before: world state before action
            snapshot_after: world state after action
            action_result: result dict from executor
        """
        action_result = action_result or {}

        # 1. Check residual improvement
        residual_improvement = 0.0
        if snapshot_before and snapshot_after:
            residual_improvement = self._compare_residuals(
                snapshot_before, snapshot_after
            )

        # 2. Check success criteria
        criteria_met = []
        criteria_failed = []
        if capability and capability.success_criteria:
            criteria_met, criteria_failed = self._check_criteria(
                capability.success_criteria, action_result
            )

        # 3. Detect side effects
        side_effects = []
        if snapshot_before and snapshot_after:
            side_effects = self._detect_side_effects(
                snapshot_before, snapshot_after, action.capability_id
            )

        # 4. Check for conflicts
        conflict = action_result.get("conflict", False)
        hallucination = action_result.get("hallucination", False)

        # 5. Determine pass/fail
        passed = self._determine_pass(
            criteria_met, criteria_failed,
            residual_improvement, conflict, hallucination,
        )

        # 6. Compute confidence
        confidence = self._compute_confidence(
            passed, criteria_met, criteria_failed,
            residual_improvement, conflict, hallucination,
        )

        result = VerifierResult(
            passed=passed,
            confidence=confidence,
            residual_improvement=residual_improvement,
            success_criteria_met=criteria_met,
            success_criteria_failed=criteria_failed,
            side_effects=side_effects,
            conflict=conflict,
            hallucination=hallucination,
            details=action_result,
        )

        self._verification_log.append(result)
        return result

    def verify_simple(
        self,
        action_result: Dict[str, Any],
        expected_fields: Optional[List[str]] = None,
    ) -> VerifierResult:
        """Simplified verification without world snapshots."""
        expected_fields = expected_fields or []
        met = [f for f in expected_fields if f in action_result]
        failed = [f for f in expected_fields if f not in action_result]

        conflict = action_result.get("conflict", False)
        hallucination = action_result.get("hallucination", False)
        passed = len(failed) == 0 and not conflict and not hallucination

        result = VerifierResult(
            passed=passed,
            confidence=0.8 if passed else 0.3,
            success_criteria_met=met,
            success_criteria_failed=failed,
            conflict=conflict,
            hallucination=hallucination,
            details=action_result,
        )
        self._verification_log.append(result)
        return result

    def recent_results(self, limit: int = 50) -> List[VerifierResult]:
        return self._verification_log[-limit:]

    def stats(self) -> dict:
        total = len(self._verification_log)
        passed = sum(1 for r in self._verification_log if r.passed)
        conflicts = sum(1 for r in self._verification_log if r.conflict)
        hallucinations = sum(1 for r in self._verification_log if r.hallucination)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total if total > 0 else 0.0,
            "conflicts": conflicts,
            "hallucinations": hallucinations,
        }

    # ── Internal ──────────────────────────────────────────

    def _compare_residuals(
        self, before: WorldSnapshot, after: WorldSnapshot
    ) -> float:
        """Compare residuals between snapshots. Positive = improved."""
        if not before.residuals or not after.residuals:
            return 0.0

        improvements = []
        for key in before.residuals:
            if key in after.residuals:
                before_abs = abs(before.residuals[key])
                after_abs = abs(after.residuals[key])
                if before_abs > self._residual_threshold:
                    improvement = (before_abs - after_abs) / before_abs
                    improvements.append(improvement)

        return sum(improvements) / len(improvements) if improvements else 0.0

    def _check_criteria(
        self, criteria: List[str], result: Dict[str, Any]
    ) -> tuple:
        """Check which success criteria are met."""
        met = []
        failed = []
        for criterion in criteria:
            # Simple heuristic: check if criterion appears as key or value
            if criterion in result:
                if result[criterion]:
                    met.append(criterion)
                else:
                    failed.append(criterion)
            elif any(criterion.lower() in str(v).lower() for v in result.values()):
                met.append(criterion)
            else:
                failed.append(criterion)
        return met, failed

    def _detect_side_effects(
        self, before: WorldSnapshot, after: WorldSnapshot,
        capability_id: str,
    ) -> List[str]:
        """Detect unexpected changes in world state."""
        side_effects = []

        # Check for new entities
        before_entities = set(before.entities.keys())
        after_entities = set(after.entities.keys())
        new_entities = after_entities - before_entities
        if new_entities:
            side_effects.append(f"New entities: {', '.join(list(new_entities)[:5])}")

        # Check for unexpected residual changes
        for key in after.residuals:
            if key not in before.residuals:
                side_effects.append(f"New residual: {key}")

        return side_effects

    def _determine_pass(
        self,
        criteria_met: List[str],
        criteria_failed: List[str],
        residual_improvement: float,
        conflict: bool,
        hallucination: bool,
    ) -> bool:
        """Determine if verification passed."""
        if conflict or hallucination:
            return False
        if criteria_failed:
            return False
        if criteria_met:
            return True
        # No criteria defined — pass if no negative signals
        return not conflict and not hallucination

    def _compute_confidence(
        self,
        passed: bool,
        criteria_met: List[str],
        criteria_failed: List[str],
        residual_improvement: float,
        conflict: bool,
        hallucination: bool,
    ) -> float:
        """Compute confidence in verification result."""
        if conflict or hallucination:
            return 0.1

        base = 0.5
        if passed:
            base = 0.7
        if criteria_met:
            base += 0.1 * min(len(criteria_met), 3)
        if criteria_failed:
            base -= 0.15 * min(len(criteria_failed), 3)
        if residual_improvement > 0.5:
            base += 0.1

        return max(0.0, min(1.0, base))

"""
Case Trajectory Builder — creates CaseTrajectory from query lifecycle.

Each query/action cycle produces a CaseTrajectory recording:
  - Goal (inferred from query)
  - World snapshot before
  - Selected capabilities
  - Actions taken
  - World snapshot after
  - Verifier result
  - Quality grade (gold/silver/bronze/quarantine)

CaseTrajectories are the primary learning units for Night Learning v2.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.reasoning.verifier import VerifierResult

log = logging.getLogger("waggledance.learning.case_builder")

# Intent → GoalType mapping
_INTENT_TO_GOAL_TYPE = {
    "math": GoalType.OBSERVE,
    "symbolic": GoalType.DIAGNOSE,
    "constraint": GoalType.PROTECT,
    "seasonal": GoalType.PROTECT,
    "anomaly": GoalType.DIAGNOSE,
    "retrieval": GoalType.OBSERVE,
    "chat": GoalType.OBSERVE,
    "optimize": GoalType.OPTIMIZE,
    "plan": GoalType.PLAN,
    "act": GoalType.ACT,
    "verify": GoalType.VERIFY,
}


class CaseTrajectoryBuilder:
    """
    Builds CaseTrajectory objects from query/action lifecycle data.

    Usage:
        builder = CaseTrajectoryBuilder()
        case = builder.build(
            query="What is the hive temperature?",
            intent="retrieval",
            capabilities=[...],
            actions=[...],
            verifier_result=...,
            snapshot_before=...,
            snapshot_after=...,
        )
    """

    def __init__(self, profile: str = "DEFAULT"):
        self._profile = profile
        self._cases: List[CaseTrajectory] = []

    def build(
        self,
        query: str,
        intent: str,
        capabilities: Optional[List[CapabilityContract]] = None,
        actions: Optional[List[Action]] = None,
        verifier_result: Optional[VerifierResult] = None,
        snapshot_before: Optional[WorldSnapshot] = None,
        snapshot_after: Optional[WorldSnapshot] = None,
        canonical_id: str = "",
        goal: Optional[Goal] = None,
    ) -> CaseTrajectory:
        """
        Build a CaseTrajectory from lifecycle data.

        Args:
            query: the original query/command
            intent: classified intent
            capabilities: capabilities that were selected
            actions: actions that were executed
            verifier_result: verification outcome
            snapshot_before: world state before
            snapshot_after: world state after
            canonical_id: domain.entity.metric canonical ID
            goal: explicit goal (if not provided, inferred from intent)
        """
        capabilities = capabilities or []
        actions = actions or []

        # 1. Build or use goal
        if goal is None:
            goal_type = _INTENT_TO_GOAL_TYPE.get(intent, GoalType.OBSERVE)
            goal = Goal(type=goal_type, description=query[:200])

        # 2. Build verifier result dict
        vr_dict: Dict[str, Any] = {}
        if verifier_result:
            vr_dict = verifier_result.to_dict()
        else:
            vr_dict = {"passed": False}

        # 3. Create case trajectory
        case = CaseTrajectory(
            goal=goal,
            world_snapshot_before=snapshot_before or WorldSnapshot(),
            selected_capabilities=capabilities,
            actions=actions,
            world_snapshot_after=snapshot_after or WorldSnapshot(),
            verifier_result=vr_dict,
            canonical_id=canonical_id,
            profile=self._profile,
        )

        # 4. Auto-grade
        case.grade()

        # 5. Store
        self._cases.append(case)
        if len(self._cases) > 5000:
            self._cases = self._cases[-2500:]

        log.debug("Case trajectory built: %s grade=%s (intent=%s)",
                  case.trajectory_id, case.quality_grade.value, intent)
        return case

    def build_from_legacy(
        self,
        question: str,
        answer: str,
        confidence: float,
        source: str,
        route_type: str = "",
        corrections: Optional[List[str]] = None,
    ) -> CaseTrajectory:
        """
        Build a CaseTrajectory from legacy Q&A data.

        Used for backfilling training data from existing systems.
        """
        corrections = corrections or []

        # Infer intent from route_type
        route_to_intent = {
            "hotcache": "retrieval",
            "memory": "retrieval",
            "micromodel": "symbolic",
            "llm": "chat",
            "swarm": "chat",
        }
        intent = route_to_intent.get(route_type, "chat")

        # Determine quality signals
        has_solver = route_type in ("micromodel",)
        has_corrections = len(corrections) > 0
        has_high_confidence = confidence > 0.8

        # Build verifier result from legacy signals
        vr_passed = has_high_confidence and not has_corrections
        vr_dict = {
            "passed": vr_passed,
            "confidence": confidence,
            "source": source,
            "corrections": corrections,
            "conflict": has_corrections,
        }

        # Build capabilities list
        capabilities = []
        if route_type == "micromodel":
            capabilities.append(
                CapabilityContract("solve.pattern_match", category=_cat("solve"))
            )
        elif route_type in ("hotcache", "memory"):
            capabilities.append(
                CapabilityContract(f"retrieve.{route_type}", category=_cat("retrieve"))
            )
        else:
            capabilities.append(
                CapabilityContract("explain.llm_reasoning", category=_cat("explain"))
            )

        goal_type = _INTENT_TO_GOAL_TYPE.get(intent, GoalType.OBSERVE)
        goal = Goal(type=goal_type, description=question[:200])

        case = CaseTrajectory(
            goal=goal,
            selected_capabilities=capabilities,
            verifier_result=vr_dict,
            profile=self._profile,
        )
        case.grade()

        self._cases.append(case)
        return case

    # ── Drain ──────────────────────────────────────────────

    def drain_cases(self) -> List[CaseTrajectory]:
        """Return all accumulated cases and clear the internal buffer.

        Used by NightLearningPipeline to consume the day's cases.
        """
        cases = self._cases[:]
        self._cases.clear()
        return cases

    def pending_count(self) -> int:
        """Number of cases waiting to be drained."""
        return len(self._cases)

    # ── Query ─────────────────────────────────────────────

    def recent_cases(self, limit: int = 50) -> List[CaseTrajectory]:
        return self._cases[-limit:]

    def cases_by_grade(self, grade: QualityGrade) -> List[CaseTrajectory]:
        return [c for c in self._cases if c.quality_grade == grade]

    def stats(self) -> dict:
        total = len(self._cases)
        if total == 0:
            return {"total": 0, "grades": {}}

        grade_counts: Dict[str, int] = {}
        for c in self._cases:
            g = c.quality_grade.value
            grade_counts[g] = grade_counts.get(g, 0) + 1

        return {
            "total": total,
            "grades": grade_counts,
            "gold_rate": grade_counts.get("gold", 0) / total,
            "quarantine_rate": grade_counts.get("quarantine", 0) / total,
        }


def _cat(name: str):
    """Helper to create CapabilityCategory from string."""
    from waggledance.core.domain.autonomy import CapabilityCategory
    return CapabilityCategory(name)

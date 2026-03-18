# BUSL-1.1 - see LICENSE-CORE.md
"""
Quality Gate — controls what learning data gets promoted.

Rules:
  - Gold: solver + verifier confirmed → direct to production
  - Silver: solver OR verifier → candidate, 48h monitoring
  - Bronze: LLM-only or unverified → no automatic promotion
  - Quarantine: conflicting signals → manual review or reject

The quality gate enforces that only verified learning data
feeds specialist model training and procedural memory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import CaseTrajectory, QualityGrade

log = logging.getLogger("waggledance.learning.quality_gate")

# Grade → allowed promotion paths
_PROMOTION_RULES: Dict[str, Dict[str, Any]] = {
    "gold": {
        "auto_promote": True,
        "monitoring_hours": 0,
        "max_grade": "gold",
        "feeds_specialist": True,
        "feeds_procedural": True,
    },
    "silver": {
        "auto_promote": False,
        "monitoring_hours": 48,
        "max_grade": "silver",
        "feeds_specialist": True,
        "feeds_procedural": False,
    },
    "bronze": {
        "auto_promote": False,
        "monitoring_hours": 0,
        "max_grade": "bronze",
        "feeds_specialist": False,
        "feeds_procedural": False,
    },
    "quarantine": {
        "auto_promote": False,
        "monitoring_hours": 0,
        "max_grade": "quarantine",
        "feeds_specialist": False,
        "feeds_procedural": False,
    },
}


@dataclass
class PromotionDecision:
    """Result of quality gate evaluation."""
    case_id: str
    grade: QualityGrade
    auto_promote: bool = False
    monitoring_hours: int = 0
    feeds_specialist: bool = False
    feeds_procedural: bool = False
    reason: str = ""
    timestamp: float = field(default_factory=time.time)


class QualityGate:
    """
    Evaluates case trajectories against promotion rules.

    Controls what data feeds specialist training and procedural memory.
    LLM-only answers (bronze) never auto-promote.
    """

    def __init__(
        self,
        min_specialist_accuracy: float = 0.85,
        canary_hours: int = 48,
    ):
        self._min_accuracy = min_specialist_accuracy
        self._canary_hours = canary_hours
        self._decisions: List[PromotionDecision] = []

    def evaluate(self, case: CaseTrajectory) -> PromotionDecision:
        """Evaluate a case trajectory against quality rules."""
        grade = case.quality_grade
        rules = _PROMOTION_RULES.get(grade.value, _PROMOTION_RULES["bronze"])

        decision = PromotionDecision(
            case_id=case.trajectory_id,
            grade=grade,
            auto_promote=rules["auto_promote"],
            monitoring_hours=rules["monitoring_hours"],
            feeds_specialist=rules["feeds_specialist"],
            feeds_procedural=rules["feeds_procedural"],
            reason=self._build_reason(grade, rules),
        )

        self._decisions.append(decision)
        if len(self._decisions) > 5000:
            self._decisions = self._decisions[-2500:]

        return decision

    def evaluate_batch(self, cases: List[CaseTrajectory]) -> List[PromotionDecision]:
        """Evaluate a batch of cases."""
        return [self.evaluate(c) for c in cases]

    def filter_for_specialist(
        self, cases: List[CaseTrajectory],
    ) -> List[CaseTrajectory]:
        """Filter cases that are eligible to feed specialist model training."""
        eligible = []
        for case in cases:
            decision = self.evaluate(case)
            if decision.feeds_specialist:
                eligible.append(case)
        return eligible

    def filter_for_procedural(
        self, cases: List[CaseTrajectory],
    ) -> List[CaseTrajectory]:
        """Filter cases eligible for procedural memory (gold only)."""
        return [c for c in cases if c.quality_grade == QualityGrade.GOLD]

    def check_specialist_accuracy(self, accuracy: float) -> bool:
        """Check if a specialist model meets minimum accuracy threshold."""
        return accuracy >= self._min_accuracy

    # ── Stats ──────────────────────────────────────────────

    def stats(self) -> dict:
        total = len(self._decisions)
        if total == 0:
            return {"total": 0, "by_grade": {}, "auto_promote_rate": 0.0}

        by_grade: Dict[str, int] = {}
        auto_count = 0
        for d in self._decisions:
            g = d.grade.value
            by_grade[g] = by_grade.get(g, 0) + 1
            if d.auto_promote:
                auto_count += 1

        return {
            "total": total,
            "by_grade": by_grade,
            "auto_promote_rate": auto_count / total,
            "min_specialist_accuracy": self._min_accuracy,
            "canary_hours": self._canary_hours,
        }

    # ── Internal ───────────────────────────────────────────

    @staticmethod
    def _build_reason(grade: QualityGrade, rules: dict) -> str:
        if grade == QualityGrade.GOLD:
            return "Solver + verifier confirmed — auto-promote"
        if grade == QualityGrade.SILVER:
            return f"Partial verification — monitor {rules['monitoring_hours']}h"
        if grade == QualityGrade.BRONZE:
            return "LLM-only or unverified — no auto-promotion"
        return "Conflicting signals — quarantined"

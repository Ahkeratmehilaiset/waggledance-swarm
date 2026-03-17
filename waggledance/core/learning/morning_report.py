"""
Morning Report — generates a summary of overnight learning results.

Produced at the end of the night learning pipeline, covering:
  - New gold/silver cases
  - Specialist model canary results
  - World model changes
  - Coverage gaps (low-coverage areas)
  - Proposed proactive goals
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import CaseTrajectory, QualityGrade

log = logging.getLogger("waggledance.learning.morning_report")


@dataclass
class MorningReport:
    """Summary of overnight learning results."""
    report_id: str = field(default_factory=lambda: f"report_{int(time.time())}")
    timestamp: float = field(default_factory=time.time)
    profile: str = ""

    # Case trajectory summary
    total_cases: int = 0
    gold_cases: int = 0
    silver_cases: int = 0
    bronze_cases: int = 0
    quarantine_cases: int = 0
    gold_rate: float = 0.0

    # Specialist model results
    models_trained: int = 0
    canaries_promoted: int = 0
    canaries_rolled_back: int = 0
    model_details: List[Dict[str, Any]] = field(default_factory=list)

    # World model changes
    new_entities: int = 0
    baseline_updates: int = 0
    expired_entities: int = 0

    # Procedural memory
    new_procedures: int = 0
    new_anti_patterns: int = 0

    # Gaps and suggestions
    coverage_gaps: List[str] = field(default_factory=list)
    suggested_goals: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "profile": self.profile,
            "cases": {
                "total": self.total_cases,
                "gold": self.gold_cases,
                "silver": self.silver_cases,
                "bronze": self.bronze_cases,
                "quarantine": self.quarantine_cases,
                "gold_rate": self.gold_rate,
            },
            "models": {
                "trained": self.models_trained,
                "promoted": self.canaries_promoted,
                "rolled_back": self.canaries_rolled_back,
                "details": self.model_details,
            },
            "world_model": {
                "new_entities": self.new_entities,
                "baseline_updates": self.baseline_updates,
                "expired_entities": self.expired_entities,
            },
            "procedural": {
                "new_procedures": self.new_procedures,
                "new_anti_patterns": self.new_anti_patterns,
            },
            "gaps": self.coverage_gaps,
            "suggested_goals": self.suggested_goals,
        }

    def summary_text(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Morning Report ({self.profile})",
            f"  Cases: {self.total_cases} total "
            f"(gold: {self.gold_cases}, silver: {self.silver_cases}, "
            f"bronze: {self.bronze_cases}, quarantine: {self.quarantine_cases})",
            f"  Gold rate: {self.gold_rate:.1%}",
            f"  Models trained: {self.models_trained} "
            f"(promoted: {self.canaries_promoted}, "
            f"rolled back: {self.canaries_rolled_back})",
            f"  New procedures: {self.new_procedures}, "
            f"anti-patterns: {self.new_anti_patterns}",
        ]
        if self.coverage_gaps:
            lines.append(f"  Coverage gaps: {', '.join(self.coverage_gaps[:5])}")
        if self.suggested_goals:
            lines.append(f"  Suggested goals: {', '.join(self.suggested_goals[:5])}")
        return "\n".join(lines)


class MorningReportBuilder:
    """Builds morning reports from night learning results."""

    def __init__(self, profile: str = "DEFAULT"):
        self._profile = profile
        self._reports: List[MorningReport] = []

    def build(
        self,
        cases: Optional[List[CaseTrajectory]] = None,
        training_results: Optional[List[Dict[str, Any]]] = None,
        canary_results: Optional[Dict[str, str]] = None,
        world_changes: Optional[Dict[str, int]] = None,
        procedural_stats: Optional[Dict[str, int]] = None,
    ) -> MorningReport:
        """Build a morning report from night learning outputs."""
        cases = cases or []
        training_results = training_results or []
        canary_results = canary_results or {}
        world_changes = world_changes or {}
        procedural_stats = procedural_stats or {}

        report = MorningReport(profile=self._profile)

        # Case stats
        report.total_cases = len(cases)
        report.gold_cases = sum(
            1 for c in cases if c.quality_grade == QualityGrade.GOLD
        )
        report.silver_cases = sum(
            1 for c in cases if c.quality_grade == QualityGrade.SILVER
        )
        report.bronze_cases = sum(
            1 for c in cases if c.quality_grade == QualityGrade.BRONZE
        )
        report.quarantine_cases = sum(
            1 for c in cases if c.quality_grade == QualityGrade.QUARANTINE
        )
        report.gold_rate = (
            report.gold_cases / report.total_cases
            if report.total_cases > 0 else 0.0
        )

        # Model stats
        report.models_trained = len(training_results)
        report.model_details = training_results
        report.canaries_promoted = sum(
            1 for v in canary_results.values() if v == "promoted"
        )
        report.canaries_rolled_back = sum(
            1 for v in canary_results.values() if v == "rolled_back"
        )

        # World model stats
        report.new_entities = world_changes.get("new_entities", 0)
        report.baseline_updates = world_changes.get("baseline_updates", 0)
        report.expired_entities = world_changes.get("expired_entities", 0)

        # Procedural memory stats
        report.new_procedures = procedural_stats.get("new_procedures", 0)
        report.new_anti_patterns = procedural_stats.get("new_anti_patterns", 0)

        # Coverage gap detection
        report.coverage_gaps = self._detect_gaps(cases)
        report.suggested_goals = self._suggest_goals(cases, report)

        self._reports.append(report)
        if len(self._reports) > 365:
            self._reports = self._reports[-180:]

        log.info("Morning report: %d cases, gold_rate=%.1f%%, %d models trained",
                 report.total_cases, report.gold_rate * 100, report.models_trained)
        return report

    def recent_reports(self, limit: int = 7) -> List[MorningReport]:
        return self._reports[-limit:]

    def stats(self) -> dict:
        return {"total_reports": len(self._reports)}

    # ── Internal ───────────────────────────────────────────

    @staticmethod
    def _detect_gaps(cases: List[CaseTrajectory]) -> List[str]:
        """Detect areas with low coverage (few gold/silver cases)."""
        gaps = []
        goal_types: Dict[str, int] = {}
        for c in cases:
            gt = c.goal.type.value if c.goal else "unknown"
            goal_types[gt] = goal_types.get(gt, 0) + 1

        if not goal_types:
            return ["No cases processed"]

        avg_count = sum(goal_types.values()) / len(goal_types)
        for gt, count in goal_types.items():
            if count < avg_count * 0.3:
                gaps.append(f"Low coverage: {gt} ({count} cases)")

        return gaps

    @staticmethod
    def _suggest_goals(
        cases: List[CaseTrajectory],
        report: MorningReport,
    ) -> List[str]:
        """Suggest proactive goals based on night learning results."""
        suggestions = []

        if report.gold_rate < 0.4 and report.total_cases > 0:
            suggestions.append("Improve verification coverage (gold rate < 40%)")

        if report.quarantine_cases > report.total_cases * 0.1:
            suggestions.append("Investigate quarantined cases (>10% quarantine rate)")

        if report.canaries_rolled_back > 0:
            suggestions.append(
                f"Review {report.canaries_rolled_back} rolled-back specialist models"
            )

        return suggestions

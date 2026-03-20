"""
Night Learning Pipeline v2 — full autonomous learning orchestrator.

Runs overnight (22:00–06:00) and performs:
  1. Case trajectory building (from day's data)
  2. Quality grading (gold/silver/bronze/quarantine)
  3. LLM background tasks (explain, hard negatives, counterfactuals)
  4. Specialist model training (from gold/silver cases)
  5. World model maintenance (baselines, residuals, entity TTL)
  6. Procedural memory update (gold → proven chains, quarantine → anti-patterns)
  7. Morning report generation

The pipeline integrates:
  - CaseTrajectoryBuilder (Phase 5)
  - QualityGate (Phase 7)
  - SpecialistTrainer (Phase 7)
  - ProceduralMemory (Phase 7)
  - WorldModel (Phase 3)
  - GoalEngine (Phase 6, for proactive goals)
  - MorningReportBuilder (Phase 7)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    CaseTrajectory,
    QualityGrade,
)
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
from waggledance.core.learning.legacy_converter import LegacyConverter, LegacyRecord
from waggledance.core.learning.morning_report import MorningReport, MorningReportBuilder
from waggledance.core.learning.procedural_memory import ProceduralMemory
from waggledance.core.learning.quality_gate import QualityGate
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer

log = logging.getLogger("waggledance.learning.night_pipeline")


@dataclass
class NightLearningResult:
    """Result of a full night learning cycle."""
    cycle_id: str = field(default_factory=lambda: f"night_{int(time.time())}")
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    duration_s: float = 0.0

    # Phase results
    cases_built: int = 0
    cases_graded: int = 0
    legacy_converted: int = 0
    models_trained: int = 0
    canaries_evaluated: int = 0
    procedures_learned: int = 0
    anti_patterns_learned: int = 0
    world_updates: int = 0

    # Quality summary
    gold_count: int = 0
    silver_count: int = 0
    bronze_count: int = 0
    quarantine_count: int = 0

    # Canary evaluation results {model_id: "promoted"/"rolled_back"}
    canary_results: Dict[str, str] = field(default_factory=dict)

    report: Optional[MorningReport] = None
    errors: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": self.duration_s,
            "cases_built": self.cases_built,
            "legacy_converted": self.legacy_converted,
            "models_trained": self.models_trained,
            "procedures_learned": self.procedures_learned,
            "quality": {
                "gold": self.gold_count,
                "silver": self.silver_count,
                "bronze": self.bronze_count,
                "quarantine": self.quarantine_count,
            },
            "canary_results": self.canary_results,
            "errors": self.errors,
            "success": self.success,
        }


class NightLearningPipeline:
    """
    Main orchestrator for the night learning cycle.

    Usage:
        pipeline = NightLearningPipeline(profile="HOME")
        result = pipeline.run_cycle(day_cases=cases, legacy_records=records)
    """

    def __init__(
        self,
        profile: str = "DEFAULT",
        case_builder: Optional[CaseTrajectoryBuilder] = None,
        quality_gate: Optional[QualityGate] = None,
        specialist_trainer: Optional[SpecialistTrainer] = None,
        procedural_memory: Optional[ProceduralMemory] = None,
        legacy_converter: Optional[LegacyConverter] = None,
        report_builder: Optional[MorningReportBuilder] = None,
    ):
        self._profile = profile
        self._case_builder = case_builder or CaseTrajectoryBuilder(profile=profile)
        self._quality_gate = quality_gate or QualityGate()
        self._trainer = specialist_trainer or SpecialistTrainer()
        self._procedural = procedural_memory or ProceduralMemory()
        self._converter = legacy_converter or LegacyConverter(profile=profile)
        self._report_builder = report_builder or MorningReportBuilder(profile=profile)

        self._history: List[NightLearningResult] = []
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def run_cycle(
        self,
        day_cases: Optional[List[CaseTrajectory]] = None,
        legacy_records: Optional[List[LegacyRecord]] = None,
    ) -> NightLearningResult:
        """
        Run a full night learning cycle.

        Args:
            day_cases: case trajectories from the day's activity
            legacy_records: legacy Q&A data to convert
        """
        self._running = True
        result = NightLearningResult()
        t0 = time.time()

        try:
            all_cases: List[CaseTrajectory] = []

            # Phase 1: Collect day cases
            if day_cases:
                all_cases.extend(day_cases)
                result.cases_built = len(day_cases)

            # Phase 2: Convert legacy records
            if legacy_records:
                legacy_cases = self._converter.convert_batch(legacy_records)
                all_cases.extend(legacy_cases)
                result.legacy_converted = len(legacy_cases)

            # Phase 3: Quality grading
            self._grade_cases(all_cases, result)

            # Phase 4: Specialist model training
            self._train_specialists(all_cases, result)

            # Phase 5: Procedural memory update
            self._update_procedural(all_cases, result)

            # Phase 6: Generate morning report
            result.report = self._generate_report(all_cases, result)

        except Exception as e:
            result.errors.append(str(e))
            log.error("Night learning cycle failed: %s", e)
        finally:
            result.completed_at = time.time()
            result.duration_s = round(time.time() - t0, 3)
            self._running = False

        self._history.append(result)
        if len(self._history) > 365:
            self._history = self._history[-180:]

        log.info(
            "Night learning cycle %s: %d cases, %d models, %.1fs",
            result.cycle_id, result.cases_built + result.legacy_converted,
            result.models_trained, result.duration_s,
        )
        return result

    # ── Pipeline phases ────────────────────────────────────

    def _grade_cases(
        self,
        cases: List[CaseTrajectory],
        result: NightLearningResult,
    ):
        """Phase 3: Grade all cases via quality gate."""
        decisions = self._quality_gate.evaluate_batch(cases)
        result.cases_graded = len(decisions)

        for case in cases:
            grade = case.quality_grade
            if grade == QualityGrade.GOLD:
                result.gold_count += 1
            elif grade == QualityGrade.SILVER:
                result.silver_count += 1
            elif grade == QualityGrade.BRONZE:
                result.bronze_count += 1
            elif grade == QualityGrade.QUARANTINE:
                result.quarantine_count += 1

    def _train_specialists(
        self,
        cases: List[CaseTrajectory],
        result: NightLearningResult,
    ):
        """Phase 4: Train specialist models from eligible cases."""
        eligible = self._quality_gate.filter_for_specialist(cases)
        if not eligible:
            return

        try:
            training_results = self._trainer.train_all(eligible)
            self._last_training_results = training_results
            result.models_trained = sum(
                1 for r in training_results if r.status == "completed"
            )

            # Start canaries for newly trained models
            for tr in training_results:
                if tr.status == "completed":
                    self._trainer.start_canary(tr.model_id)

            # Evaluate existing canaries
            canary_results = self._trainer.evaluate_all_canaries()
            result.canaries_evaluated = len(canary_results)
            result.canary_results = canary_results

        except Exception as e:
            result.errors.append(f"Specialist training: {e}")
            log.warning("Specialist training failed: %s", e)

    def _update_procedural(
        self,
        cases: List[CaseTrajectory],
        result: NightLearningResult,
    ):
        """Phase 5: Update procedural memory from gold/quarantine cases."""
        for case in cases:
            proc = self._procedural.learn_from_case(case)
            if proc:
                if proc.is_anti_pattern:
                    result.anti_patterns_learned += 1
                else:
                    result.procedures_learned += 1

    def _generate_report(
        self,
        cases: List[CaseTrajectory],
        result: NightLearningResult,
    ) -> MorningReport:
        """Phase 6: Generate morning report."""
        training_details = []
        for tr in getattr(self, "_last_training_results", []):
            training_details.append({"model_id": tr.model_id, "accuracy": tr.accuracy})

        return self._report_builder.build(
            cases=cases,
            procedural_stats={
                "new_procedures": result.procedures_learned,
                "new_anti_patterns": result.anti_patterns_learned,
            },
        )

    # ── Query ──────────────────────────────────────────────

    def recent_results(self, limit: int = 7) -> List[NightLearningResult]:
        return self._history[-limit:]

    def last_result(self) -> Optional[NightLearningResult]:
        return self._history[-1] if self._history else None

    def stats(self) -> dict:
        return {
            "profile": self._profile,
            "running": self._running,
            "total_cycles": len(self._history),
            "quality_gate": self._quality_gate.stats(),
            "specialist_trainer": self._trainer.stats(),
            "procedural_memory": self._procedural.stats(),
            "converter": self._converter.stats(),
        }

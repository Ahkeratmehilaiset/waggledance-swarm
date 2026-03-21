"""
Regression gate: Night Learning v2 tests.

Validates the case trajectory pipeline, quality gate, procedural memory,
morning report, and legacy converter.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    CaseTrajectory, Goal, GoalType, QualityGrade,
    CapabilityCategory, CapabilityContract,
)
from waggledance.core.learning.quality_gate import QualityGate
from waggledance.core.learning.procedural_memory import ProceduralMemory
from waggledance.core.learning.night_learning_pipeline import (
    NightLearningPipeline, NightLearningResult,
)
from waggledance.core.learning.morning_report import MorningReportBuilder
from waggledance.core.learning.legacy_converter import LegacyConverter


def _gold_case(goal_type: str = "observe") -> CaseTrajectory:
    """Helper: build a gold-grade case with capabilities and goal."""
    return CaseTrajectory(
        goal=Goal(type=GoalType(goal_type)),
        selected_capabilities=[
            CapabilityContract(
                capability_id="solver.test",
                category=CapabilityCategory.SOLVE,
            ),
        ],
        verifier_result={"passed": True},
        quality_grade=QualityGrade.GOLD,
    )


def _quarantine_case() -> CaseTrajectory:
    """Helper: build a quarantine-grade case."""
    return CaseTrajectory(
        goal=Goal(type=GoalType.ACT),
        selected_capabilities=[
            CapabilityContract(
                capability_id="bad.action",
                category=CapabilityCategory.ACT,
            ),
        ],
        verifier_result={"conflict": True},
        quality_grade=QualityGrade.QUARANTINE,
    )


class TestQualityGate:
    @pytest.fixture
    def gate(self):
        return QualityGate()

    def test_gold_auto_promotes(self, gate):
        case = _gold_case()
        decision = gate.evaluate(case)
        assert decision.auto_promote is True

    def test_bronze_never_promotes(self, gate):
        case = CaseTrajectory(quality_grade=QualityGrade.BRONZE)
        decision = gate.evaluate(case)
        assert decision.auto_promote is False

    def test_quarantine_no_promote(self, gate):
        case = _quarantine_case()
        decision = gate.evaluate(case)
        assert decision.auto_promote is False
        assert decision.feeds_procedural is False


class TestProceduralMemory:
    @pytest.fixture
    def memory(self, tmp_path):
        return ProceduralMemory(persist_path=str(tmp_path / "procs.json"))

    def test_learn_gold_case(self, memory):
        case = _gold_case("diagnose")
        proc = memory.learn_from_case(case)
        assert proc is not None
        assert proc.success_count >= 1

    def test_best_procedure(self, memory):
        case = _gold_case("diagnose")
        memory.learn_from_case(case)
        best = memory.best_procedure("diagnose")
        assert best is not None

    def test_learn_quarantine_anti_pattern(self, memory):
        case = _quarantine_case()
        proc = memory.learn_from_case(case)
        assert proc is not None
        assert proc.is_anti_pattern is True

    def test_get_anti_patterns(self, memory):
        case = _quarantine_case()
        memory.learn_from_case(case)
        patterns = memory.get_anti_patterns("act")
        assert len(patterns) >= 1


class TestNightLearningPipeline:
    def test_run_cycle(self):
        pipeline = NightLearningPipeline(profile="TEST")
        result = pipeline.run_cycle()
        assert result.duration_s >= 0
        assert result.cases_graded >= 0

    def test_stats(self):
        pipeline = NightLearningPipeline(profile="TEST")
        s = pipeline.stats()
        assert s["profile"] == "TEST"


class TestMorningReportBuilder:
    def test_build_empty(self):
        builder = MorningReportBuilder()
        morning = builder.build()
        assert morning.total_cases == 0

    def test_build_with_cases(self):
        builder = MorningReportBuilder()
        cases = [_gold_case() for _ in range(3)]
        morning = builder.build(cases=cases)
        assert isinstance(morning.to_dict(), dict)


class TestLegacyConverter:
    def test_convert_from_dicts(self):
        converter = LegacyConverter()
        dicts = [
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ]
        cases = converter.convert_from_dicts(dicts)
        assert len(cases) == 2
        assert all(c.quality_grade == QualityGrade.BRONZE for c in cases)

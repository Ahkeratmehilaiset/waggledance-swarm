"""
Phase 7 Tests: Night Learning v2 Pipeline, Quality Gate,
Specialist Trainer, Procedural Memory, Legacy Converter, Morning Report.

Tests cover:
- QualityGate evaluation and filtering
- ProceduralMemory learning from gold/quarantine cases
- LegacyConverter batch conversion
- SpecialistTrainer training and canary lifecycle
- NightLearningPipeline full cycle
- MorningReportBuilder summary generation
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
    CaseTrajectory,
    Goal,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder
from waggledance.core.learning.legacy_converter import LegacyConverter, LegacyRecord
from waggledance.core.learning.morning_report import MorningReportBuilder
from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline
from waggledance.core.learning.procedural_memory import ProceduralMemory
from waggledance.core.learning.quality_gate import QualityGate
from waggledance.core.specialist_models.model_store import ModelStore, ModelStatus
from waggledance.core.specialist_models.specialist_trainer import SpecialistTrainer


# ── Helpers ──────────────────────────────────────────────

def _gold_case(desc: str = "gold test") -> CaseTrajectory:
    """Create a gold-grade case trajectory."""
    goal = Goal(type=GoalType.DIAGNOSE, description=desc)
    case = CaseTrajectory(
        goal=goal,
        selected_capabilities=[
            CapabilityContract("solve.math", category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": True, "confidence": 0.95},
        profile="TEST",
    )
    case.grade()
    assert case.quality_grade == QualityGrade.GOLD
    return case


def _silver_case(desc: str = "silver test") -> CaseTrajectory:
    goal = Goal(type=GoalType.OBSERVE, description=desc)
    case = CaseTrajectory(
        goal=goal,
        selected_capabilities=[
            CapabilityContract("solve.pattern", category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": False},
        profile="TEST",
    )
    case.grade()
    assert case.quality_grade == QualityGrade.SILVER
    return case


def _bronze_case(desc: str = "bronze test") -> CaseTrajectory:
    goal = Goal(type=GoalType.OBSERVE, description=desc)
    case = CaseTrajectory(
        goal=goal,
        selected_capabilities=[
            CapabilityContract("explain.llm", category=CapabilityCategory.EXPLAIN),
        ],
        verifier_result={"passed": False},
        profile="TEST",
    )
    case.grade()
    assert case.quality_grade == QualityGrade.BRONZE
    return case


def _quarantine_case(desc: str = "quarantine test") -> CaseTrajectory:
    goal = Goal(type=GoalType.OBSERVE, description=desc)
    case = CaseTrajectory(
        goal=goal,
        selected_capabilities=[
            CapabilityContract("solve.math", category=CapabilityCategory.SOLVE),
        ],
        verifier_result={"passed": True, "conflict": True},
        profile="TEST",
    )
    case.grade()
    assert case.quality_grade == QualityGrade.QUARANTINE
    return case


# ── QualityGate ──────────────────────────────────────────

class TestQualityGate:
    def test_gold_auto_promote(self):
        gate = QualityGate()
        decision = gate.evaluate(_gold_case())
        assert decision.auto_promote is True
        assert decision.feeds_specialist is True
        assert decision.feeds_procedural is True
        assert decision.monitoring_hours == 0

    def test_silver_no_auto_promote(self):
        gate = QualityGate()
        decision = gate.evaluate(_silver_case())
        assert decision.auto_promote is False
        assert decision.feeds_specialist is True
        assert decision.feeds_procedural is False
        assert decision.monitoring_hours == 48

    def test_bronze_no_promote(self):
        gate = QualityGate()
        decision = gate.evaluate(_bronze_case())
        assert decision.auto_promote is False
        assert decision.feeds_specialist is False
        assert decision.feeds_procedural is False

    def test_quarantine_no_promote(self):
        gate = QualityGate()
        decision = gate.evaluate(_quarantine_case())
        assert decision.auto_promote is False
        assert decision.feeds_specialist is False

    def test_evaluate_batch(self):
        gate = QualityGate()
        cases = [_gold_case(), _silver_case(), _bronze_case()]
        decisions = gate.evaluate_batch(cases)
        assert len(decisions) == 3
        assert decisions[0].auto_promote is True
        assert decisions[2].auto_promote is False

    def test_filter_for_specialist(self):
        gate = QualityGate()
        cases = [_gold_case(), _silver_case(), _bronze_case(), _quarantine_case()]
        eligible = gate.filter_for_specialist(cases)
        assert len(eligible) == 2  # gold + silver

    def test_filter_for_procedural(self):
        gate = QualityGate()
        cases = [_gold_case(), _silver_case(), _bronze_case()]
        eligible = gate.filter_for_procedural(cases)
        assert len(eligible) == 1  # gold only

    def test_check_specialist_accuracy(self):
        gate = QualityGate(min_specialist_accuracy=0.85)
        assert gate.check_specialist_accuracy(0.90) is True
        assert gate.check_specialist_accuracy(0.80) is False

    def test_stats(self):
        gate = QualityGate()
        gate.evaluate(_gold_case())
        gate.evaluate(_bronze_case())
        s = gate.stats()
        assert s["total"] == 2
        assert "gold" in s["by_grade"]


# ── ProceduralMemory ─────────────────────────────────────

class TestProceduralMemory:
    def test_learn_gold(self):
        pm = ProceduralMemory()
        proc = pm.learn_from_case(_gold_case())
        assert proc is not None
        assert proc.success_count == 1
        assert proc.is_anti_pattern is False

    def test_learn_quarantine_anti_pattern(self):
        pm = ProceduralMemory()
        proc = pm.learn_from_case(_quarantine_case())
        assert proc is not None
        assert proc.fail_count == 1
        assert proc.is_anti_pattern is True

    def test_ignore_bronze(self):
        pm = ProceduralMemory()
        proc = pm.learn_from_case(_bronze_case())
        assert proc is None

    def test_ignore_silver(self):
        pm = ProceduralMemory()
        proc = pm.learn_from_case(_silver_case())
        assert proc is None

    def test_increment_success(self):
        pm = ProceduralMemory()
        pm.learn_from_case(_gold_case("first"))
        proc = pm.learn_from_case(_gold_case("second"))
        assert proc.success_count == 2

    def test_get_procedures(self):
        pm = ProceduralMemory()
        pm.learn_from_case(_gold_case())
        procs = pm.get_procedures()
        assert len(procs) == 1

    def test_get_procedures_by_goal_type(self):
        pm = ProceduralMemory()
        pm.learn_from_case(_gold_case())  # diagnose type
        procs = pm.get_procedures(goal_type="diagnose")
        assert len(procs) == 1
        procs = pm.get_procedures(goal_type="observe")
        assert len(procs) == 0

    def test_get_anti_patterns(self):
        pm = ProceduralMemory()
        pm.learn_from_case(_quarantine_case())
        antis = pm.get_anti_patterns()
        assert len(antis) == 1

    def test_best_procedure(self):
        pm = ProceduralMemory()
        pm.learn_from_case(_gold_case())
        best = pm.best_procedure("diagnose")
        assert best is not None
        assert best.goal_type == "diagnose"

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "proc.json")
        pm1 = ProceduralMemory(persist_path=path)
        pm1.learn_from_case(_gold_case())
        assert pm1.count() == 1

        pm2 = ProceduralMemory(persist_path=path)
        assert pm2.count() == 1

    def test_clear(self):
        pm = ProceduralMemory()
        pm.learn_from_case(_gold_case())
        pm.clear()
        assert pm.count() == 0

    def test_stats(self):
        pm = ProceduralMemory()
        pm.learn_from_case(_gold_case())
        pm.learn_from_case(_quarantine_case())
        s = pm.stats()
        assert s["proven"] == 1
        assert s["anti_patterns"] == 1


# ── LegacyConverter ──────────────────────────────────────

class TestLegacyConverter:
    def test_convert_single(self):
        conv = LegacyConverter(profile="TEST")
        record = LegacyRecord(
            question="What is hive temperature?",
            answer="35°C",
            confidence=0.9,
            source="memory",
            route_type="memory",
        )
        case = conv.convert(record)
        assert isinstance(case, CaseTrajectory)
        assert case.quality_grade in list(QualityGrade)

    def test_convert_batch(self):
        conv = LegacyConverter(profile="TEST")
        records = [
            LegacyRecord(question=f"Q{i}", answer=f"A{i}", confidence=0.8,
                         source="memory", route_type="memory")
            for i in range(5)
        ]
        cases = conv.convert_batch(records)
        assert len(cases) == 5

    def test_convert_from_dicts(self):
        conv = LegacyConverter(profile="TEST")
        dicts = [
            {"question": "What?", "answer": "That.", "confidence": 0.7,
             "source": "llm", "route_type": "llm"},
        ]
        cases = conv.convert_from_dicts(dicts)
        assert len(cases) == 1

    def test_convert_training_pairs(self):
        conv = LegacyConverter(profile="TEST")
        pairs = [
            ("Q1", "A1", 0.9, "memory"),
            ("Q2", "A2", 0.5, "llm"),
        ]
        cases = conv.convert_training_pairs(pairs)
        assert len(cases) == 2

    def test_stats(self):
        conv = LegacyConverter(profile="TEST")
        conv.convert(LegacyRecord(question="Q", answer="A", confidence=0.8,
                                  source="s", route_type="memory"))
        s = conv.stats()
        assert s["converted"] == 1
        assert s["errors"] == 0


# ── SpecialistTrainer ────────────────────────────────────

class TestSpecialistTrainer:
    @pytest.fixture
    def trainer(self, tmp_path):
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        return SpecialistTrainer(
            model_store=store,
            min_samples=2,
            min_accuracy=0.85,
            canary_hours=0,  # No waiting in tests
        )

    def test_train_from_cases(self, trainer):
        cases = [_gold_case(f"g{i}") for i in range(5)]
        result = trainer.train_from_cases("route_classifier", cases)
        assert result.status == "completed"
        assert result.training_samples >= 2
        assert result.accuracy > 0

    def test_train_insufficient_samples(self, trainer):
        cases = [_gold_case()]
        result = trainer.train_from_cases("route_classifier", cases)
        assert result.status == "skipped"

    def test_train_all(self, trainer):
        cases = [_gold_case(f"g{i}") for i in range(5)]
        results = trainer.train_all(cases)
        assert len(results) == 8  # All specialist model types
        completed = [r for r in results if r.status == "completed"]
        assert len(completed) > 0

    def test_start_canary(self, trainer):
        cases = [_gold_case(f"g{i}") for i in range(5)]
        trainer.train_from_cases("route_classifier", cases)
        mv = trainer.start_canary("route_classifier")
        assert mv is not None
        assert mv.status == ModelStatus.CANARY

    def test_evaluate_canary_promote(self, trainer):
        cases = [_gold_case(f"g{i}") for i in range(10)]
        trainer.train_from_cases("route_classifier", cases)
        trainer.start_canary("route_classifier")
        result = trainer.evaluate_canary("route_classifier")
        # With canary_hours=0, should evaluate immediately
        assert result in ("promoted", "rolled_back", None)

    def test_stats(self, trainer):
        cases = [_gold_case(f"g{i}") for i in range(5)]
        trainer.train_from_cases("route_classifier", cases)
        s = trainer.stats()
        assert s["training_runs"] == 1
        assert "model_store" in s


# ── MorningReportBuilder ─────────────────────────────────

class TestMorningReport:
    def test_build_empty(self):
        builder = MorningReportBuilder(profile="TEST")
        report = builder.build()
        assert report.total_cases == 0
        assert report.gold_rate == 0.0

    def test_build_with_cases(self):
        builder = MorningReportBuilder(profile="TEST")
        cases = [_gold_case(), _silver_case(), _bronze_case(), _quarantine_case()]
        report = builder.build(cases=cases)
        assert report.total_cases == 4
        assert report.gold_cases == 1
        assert report.silver_cases == 1
        assert report.bronze_cases == 1
        assert report.quarantine_cases == 1
        assert report.gold_rate == pytest.approx(0.25)

    def test_report_to_dict(self):
        builder = MorningReportBuilder(profile="TEST")
        report = builder.build(cases=[_gold_case()])
        d = report.to_dict()
        assert "cases" in d
        assert "models" in d
        assert d["profile"] == "TEST"

    def test_summary_text(self):
        builder = MorningReportBuilder(profile="TEST")
        report = builder.build(cases=[_gold_case()])
        text = report.summary_text()
        assert "Morning Report" in text
        assert "gold: 1" in text

    def test_recent_reports(self):
        builder = MorningReportBuilder(profile="TEST")
        builder.build(cases=[_gold_case()])
        builder.build(cases=[_silver_case()])
        recent = builder.recent_reports(limit=1)
        assert len(recent) == 1

    def test_suggested_goals_low_gold(self):
        builder = MorningReportBuilder(profile="TEST")
        cases = [_bronze_case(f"b{i}") for i in range(10)]
        report = builder.build(cases=cases)
        assert any("gold rate" in g.lower() for g in report.suggested_goals)


# ── NightLearningPipeline ────────────────────────────────

class TestNightLearningPipeline:
    @pytest.fixture
    def pipeline(self, tmp_path):
        store = ModelStore(store_path=str(tmp_path / "models.json"))
        trainer = SpecialistTrainer(
            model_store=store, min_samples=2, canary_hours=0,
        )
        return NightLearningPipeline(
            profile="TEST",
            specialist_trainer=trainer,
        )

    def test_run_cycle_empty(self, pipeline):
        result = pipeline.run_cycle()
        assert result.success is True
        assert result.cases_built == 0
        assert result.duration_s >= 0

    def test_run_cycle_with_cases(self, pipeline):
        cases = [_gold_case(), _silver_case(), _bronze_case()]
        result = pipeline.run_cycle(day_cases=cases)
        assert result.cases_built == 3
        assert result.gold_count == 1
        assert result.silver_count == 1
        assert result.bronze_count == 1

    def test_run_cycle_with_legacy(self, pipeline):
        records = [
            LegacyRecord(question="Q1", answer="A1", confidence=0.9,
                         source="memory", route_type="memory"),
            LegacyRecord(question="Q2", answer="A2", confidence=0.5,
                         source="llm", route_type="llm"),
        ]
        result = pipeline.run_cycle(legacy_records=records)
        assert result.legacy_converted == 2

    def test_run_cycle_trains_specialists(self, pipeline):
        # Need enough gold/silver cases to train
        cases = [_gold_case(f"g{i}") for i in range(5)]
        cases += [_silver_case(f"s{i}") for i in range(5)]
        result = pipeline.run_cycle(day_cases=cases)
        assert result.models_trained > 0

    def test_run_cycle_learns_procedures(self, pipeline):
        cases = [_gold_case(), _quarantine_case()]
        result = pipeline.run_cycle(day_cases=cases)
        assert result.procedures_learned >= 1
        assert result.anti_patterns_learned >= 1

    def test_run_cycle_generates_report(self, pipeline):
        cases = [_gold_case(), _bronze_case()]
        result = pipeline.run_cycle(day_cases=cases)
        assert result.report is not None
        assert result.report.total_cases == 2

    def test_recent_results(self, pipeline):
        pipeline.run_cycle(day_cases=[_gold_case()])
        pipeline.run_cycle(day_cases=[_silver_case()])
        recent = pipeline.recent_results(limit=1)
        assert len(recent) == 1

    def test_last_result(self, pipeline):
        pipeline.run_cycle(day_cases=[_gold_case()])
        last = pipeline.last_result()
        assert last is not None

    def test_stats(self, pipeline):
        pipeline.run_cycle(day_cases=[_gold_case()])
        s = pipeline.stats()
        assert s["profile"] == "TEST"
        assert s["total_cycles"] == 1
        assert "quality_gate" in s
        assert "specialist_trainer" in s

    def test_is_not_running_after_cycle(self, pipeline):
        pipeline.run_cycle(day_cases=[_gold_case()])
        assert pipeline.is_running is False

    def test_full_mixed_cycle(self, pipeline):
        """Full cycle with day cases + legacy data."""
        day_cases = [_gold_case(f"day_{i}") for i in range(3)]
        legacy = [
            LegacyRecord(question=f"Q{i}", answer=f"A{i}", confidence=0.8,
                         source="memory", route_type="memory")
            for i in range(3)
        ]
        result = pipeline.run_cycle(day_cases=day_cases, legacy_records=legacy)
        assert result.cases_built == 3
        assert result.legacy_converted == 3
        assert result.report is not None
        assert result.success is True

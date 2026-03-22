# SPDX-License-Identifier: Apache-2.0
"""Integration tests for prediction error ledger + capability confidence wiring.

Covers:
  - Low capability confidence → higher epistemic uncertainty
  - Morning report includes confidence trends
  - Dream mode receives capability_confidence and prioritises low-confidence cases
  - API endpoints return valid data
  - Prediction error is correctly recorded after verifier runs
  - Capability confidence updates with EMA
"""

from __future__ import annotations

import pytest

from waggledance.core.learning.capability_confidence import (
    CapabilityConfidenceTracker,
    DEFAULT_INITIAL_CONFIDENCE,
)
from waggledance.core.learning.prediction_error_ledger import PredictionErrorLedger
from waggledance.core.world.epistemic_uncertainty import compute_uncertainty


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def tracker(tmp_path):
    return CapabilityConfidenceTracker(
        persist_path=str(tmp_path / "confidence.json"),
    )


@pytest.fixture
def ledger(tmp_path):
    return PredictionErrorLedger(ledger_path=str(tmp_path / "errors.jsonl"))


class _FakeEntity:
    """Minimal entity-like object for uncertainty computation."""

    def __init__(self, entity_id: str, updated_at: float = 0.0):
        self.entity_id = entity_id
        self.updated_at = updated_at


# ── Low confidence → higher epistemic uncertainty ────────────


class TestLowConfidenceIncreasesUncertainty:
    def test_no_confidence_baseline(self):
        entities = [_FakeEntity("e1", updated_at=9999999999)]
        report = compute_uncertainty(
            entities=entities,
            baseline_keys={"e1.temp"},
            capability_confidence=None,
        )
        base_score = report.score
        assert report.low_confidence_capabilities == 0

        # Now add low-confidence capabilities
        report_with = compute_uncertainty(
            entities=entities,
            baseline_keys={"e1.temp"},
            capability_confidence={
                "solve.math": 0.3,
                "solve.thermal": 0.2,
                "solve.stats": 0.8,
            },
            low_confidence_threshold=0.5,
        )
        assert report_with.low_confidence_capabilities == 2
        assert report_with.score > base_score

    def test_all_high_confidence_no_increase(self):
        entities = [_FakeEntity("e1", updated_at=9999999999)]
        report_without = compute_uncertainty(
            entities=entities,
            baseline_keys={"e1.temp"},
        )
        report_with = compute_uncertainty(
            entities=entities,
            baseline_keys={"e1.temp"},
            capability_confidence={
                "solve.math": 0.9,
                "solve.thermal": 0.85,
            },
        )
        assert report_with.low_confidence_capabilities == 0
        assert report_with.score == report_without.score

    def test_threshold_boundary(self):
        entities = [_FakeEntity("e1", updated_at=9999999999)]
        # Exactly at threshold — not counted as low
        report = compute_uncertainty(
            entities=entities,
            baseline_keys={"e1.temp"},
            capability_confidence={"solve.math": 0.5},
            low_confidence_threshold=0.5,
        )
        assert report.low_confidence_capabilities == 0

        # Just below threshold — counted
        report2 = compute_uncertainty(
            entities=entities,
            baseline_keys={"e1.temp"},
            capability_confidence={"solve.math": 0.49},
            low_confidence_threshold=0.5,
        )
        assert report2.low_confidence_capabilities == 1


# ── Morning report includes confidence trends ───────────────


class TestMorningReportConfidenceTrends:
    def test_report_includes_trends(self):
        from waggledance.core.learning.morning_report import MorningReportBuilder

        builder = MorningReportBuilder(profile="TEST")
        trends = {
            "improving": [
                {"solver": "solve.math", "delta": 0.05},
            ],
            "degrading": [
                {"solver": "solve.thermal", "delta": -0.03},
            ],
        }
        report = builder.build(confidence_trends=trends)
        assert len(report.improving_solvers) == 1
        assert report.improving_solvers[0]["solver"] == "solve.math"
        assert len(report.degrading_solvers) == 1
        assert report.degrading_solvers[0]["solver"] == "solve.thermal"

        # Verify it shows in to_dict and summary
        d = report.to_dict()
        assert d["confidence_trends"]["improving"][0]["solver"] == "solve.math"
        text = report.summary_text()
        assert "solve.math" in text

    def test_report_without_trends(self):
        from waggledance.core.learning.morning_report import MorningReportBuilder

        builder = MorningReportBuilder(profile="TEST")
        report = builder.build()
        assert report.improving_solvers == []
        assert report.degrading_solvers == []

    def test_pipeline_passes_trends(self):
        from waggledance.core.learning.night_learning_pipeline import (
            NightLearningPipeline,
        )

        pipeline = NightLearningPipeline(profile="TEST")
        trends = {
            "improving": [{"solver": "solve.stats", "delta": 0.04}],
            "degrading": [],
        }
        result = pipeline.run_cycle(confidence_trends=trends)
        assert result.report is not None
        assert len(result.report.improving_solvers) == 1
        assert result.report.improving_solvers[0]["solver"] == "solve.stats"


# ── Dream mode receives capability_confidence ───────────────


class TestDreamModeCapabilityConfidence:
    def test_select_candidates_boosts_low_confidence(self):
        from waggledance.core.domain.autonomy import (
            CaseTrajectory,
            Goal,
            QualityGrade,
        )
        from waggledance.core.learning.dream_mode import select_candidates

        # Create a capability contract-like object
        class _FakeCap:
            def __init__(self, cid):
                self.capability_id = cid

        # Case that failed verification
        ct = CaseTrajectory(
            goal=Goal(description="calculate thermal load"),
            quality_grade=QualityGrade.BRONZE,
            selected_capabilities=[_FakeCap("solve.thermal")],
            verifier_result={"passed": False},
        )

        # Without confidence: score comes from bronze grade only
        candidates_no_conf = select_candidates([ct], capability_confidence=None)
        assert len(candidates_no_conf) == 1
        score_no_conf = candidates_no_conf[0].uncertainty_score

        # With low confidence: score should be boosted
        candidates_with = select_candidates(
            [ct],
            capability_confidence={"solve.thermal": 0.1},
        )
        assert len(candidates_with) == 1
        assert candidates_with[0].uncertainty_score > score_no_conf

    def test_run_dream_session_with_confidence(self):
        from waggledance.core.domain.autonomy import (
            CaseTrajectory,
            Goal,
            QualityGrade,
        )
        from waggledance.core.learning.dream_mode import run_dream_session

        class _FakeCap:
            def __init__(self, cid):
                self.capability_id = cid

        ct = CaseTrajectory(
            goal=Goal(description="solve math problem"),
            quality_grade=QualityGrade.QUARANTINE,
            selected_capabilities=[_FakeCap("solve.math")],
            verifier_result={"passed": False},
        )

        session = run_dream_session(
            day_trajectories=[ct],
            available_capabilities=["solve.math", "solve.stats"],
            capability_confidence={"solve.math": 0.2, "solve.stats": 0.9},
        )
        assert session.candidates_evaluated >= 1


# ── Prediction error correctly recorded after verifier ───────


class TestPredictionErrorRecording:
    def test_success_records_zero_error(self, ledger):
        entry = ledger.record("q1", "solve.math", verified=True, confidence=0.9)
        assert entry.error_magnitude == 0.0
        assert entry.expected_outcome == "pass"
        assert entry.actual_outcome == "pass"

    def test_failure_records_unit_error(self, ledger):
        entry = ledger.record("q2", "solve.math", verified=False, confidence=0.3)
        assert entry.error_magnitude == 1.0
        assert entry.actual_outcome == "fail"

    def test_ledger_is_append_only(self, ledger):
        for i in range(5):
            ledger.record(f"q{i}", "solve.math", i % 2 == 0)
        from pathlib import Path

        lines = Path(ledger._path).read_text().strip().split("\n")
        assert len(lines) == 5

        # Adding more only appends
        ledger.record("q5", "solve.math", True)
        lines = Path(ledger._path).read_text().strip().split("\n")
        assert len(lines) == 6


# ── Capability confidence EMA update ─────────────────────────


class TestCapabilityConfidenceEMA:
    def test_ema_formula(self, tracker):
        alpha = 0.95
        init = DEFAULT_INITIAL_CONFIDENCE

        conf = tracker.update("solve.math", verified=True)
        expected = alpha * init + (1 - alpha) * 1.0
        assert conf == pytest.approx(expected)

        # Second update: success again
        conf2 = tracker.update("solve.math", verified=True)
        expected2 = alpha * expected + (1 - alpha) * 1.0
        assert conf2 == pytest.approx(expected2)

    def test_ema_converges_to_success_rate(self, tracker):
        # 70% success rate over 200 observations
        for i in range(200):
            tracker.update("solve.stats", verified=(i % 10 < 7))
        conf = tracker.get_confidence("solve.stats")
        assert 0.55 < conf < 0.85  # roughly around 0.7


# ── API endpoint returns confidence map ──────────────────────


class TestAPIEndpoints:
    def test_service_capability_confidence_available(self):
        from waggledance.application.services.autonomy_service import AutonomyService

        svc = AutonomyService(profile="TEST")
        svc.start()
        try:
            result = svc.get_capability_confidence()
            assert result["available"] is True
            assert "scores" in result
            assert "lowest" in result
            assert "improving" in result
            assert "degrading" in result
        finally:
            svc.stop()

    def test_service_prediction_ledger_analysis(self):
        from waggledance.application.services.autonomy_service import AutonomyService

        svc = AutonomyService(profile="TEST")
        svc.start()
        try:
            result = svc.get_prediction_ledger_analysis()
            assert result["available"] is True
            assert "total_entries" in result
            assert "solver_profiles" in result
        finally:
            svc.stop()


# ── WorldModel passes capability_confidence to uncertainty ───


class TestWorldModelUncertaintyWiring:
    def test_compute_epistemic_uncertainty_with_confidence(self):
        from waggledance.core.world.world_model import WorldModel

        wm = WorldModel(cognitive_graph=None, profile="TEST")
        # Register an entity so uncertainty has something to measure
        wm.register_entity("sensor1", "sensor", {"updated_at": 9999999999})

        # Without confidence
        report1 = wm.compute_epistemic_uncertainty()
        assert report1.low_confidence_capabilities == 0

        # With low confidence capabilities
        report2 = wm.compute_epistemic_uncertainty(
            capability_confidence={"solve.math": 0.2, "solve.bad": 0.1},
        )
        assert report2.low_confidence_capabilities == 2
        assert report2.score >= report1.score

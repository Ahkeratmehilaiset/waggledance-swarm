"""Tests for BeeDomainEngine — colony health, swarm risk, honey yield, disease."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.reasoning.bee_domain_engine import BeeDomainEngine


class TestAssessColonyHealth:
    def setup_method(self):
        self.engine = BeeDomainEngine()

    def test_healthy_colony(self):
        result = self.engine.assess_colony_health({
            "temperature": 35.0, "weight_kg": 30.0, "activity_level": 0.8
        })
        assert result.overall_score >= 70
        assert result.grade in ("excellent", "good")

    def test_stressed_colony(self):
        result = self.engine.assess_colony_health({
            "temperature": 25.0, "weight_kg": 7.0, "activity_level": 0.05
        })
        assert result.overall_score < 40
        assert result.grade in ("poor", "critical")
        assert len(result.warnings) > 0

    def test_missing_metrics(self):
        result = self.engine.assess_colony_health({})
        assert result.overall_score == 50.0
        assert result.grade == "fair"

    def test_single_metric(self):
        result = self.engine.assess_colony_health({"temperature": 35.0})
        assert result.temperature_score == 100


class TestPredictSwarmRisk:
    def setup_method(self):
        self.engine = BeeDomainEngine()

    def test_high_risk_crowded_old_queen(self):
        result = self.engine.predict_swarm_risk(
            queen_age=2.5, empty_combs=1, total_combs=12,
            queen_cells=3, season_factor=1.0
        )
        assert result.probability_pct >= 70
        assert result.risk_level == "critical"
        assert len(result.recommendations) > 0

    def test_low_risk_spacious_new_queen(self):
        result = self.engine.predict_swarm_risk(
            queen_age=0.5, empty_combs=8, total_combs=12,
            queen_cells=0, season_factor=0.1
        )
        assert result.probability_pct < 40
        assert result.risk_level == "normal"

    def test_zero_total_combs(self):
        result = self.engine.predict_swarm_risk(
            queen_age=1.0, empty_combs=0, total_combs=0,
            queen_cells=0, season_factor=0.5
        )
        assert isinstance(result.probability_pct, float)

    def test_factors_present(self):
        result = self.engine.predict_swarm_risk(
            queen_age=1.0, empty_combs=3, total_combs=12,
            queen_cells=0, season_factor=0.5
        )
        assert "space_pressure" in result.factors
        assert "queen_age_factor" in result.factors


class TestEstimateHoneyYield:
    def setup_method(self):
        self.engine = BeeDomainEngine()

    def test_strong_colony(self):
        result = self.engine.estimate_honey_yield(
            colony_strength=60000, flow_days=60, forager_ratio=0.35
        )
        assert result.estimated_kg > 0

    def test_weak_colony_warning(self):
        result = self.engine.estimate_honey_yield(
            colony_strength=5000, flow_days=30
        )
        assert any("weak" in w.lower() for w in result.warnings)

    def test_short_flow_warning(self):
        result = self.engine.estimate_honey_yield(
            colony_strength=40000, flow_days=10
        )
        assert any("short" in w.lower() for w in result.warnings)


class TestDiagnoseDiseaseRisk:
    def setup_method(self):
        self.engine = BeeDomainEngine()

    def test_varroa_symptoms(self):
        result = self.engine.diagnose_disease_risk(["mite_visible", "deformed_wings"])
        assert len(result["diagnoses"]) > 0
        assert result["diagnoses"][0]["disease"] == "varroa"

    def test_no_symptoms(self):
        result = self.engine.diagnose_disease_risk([])
        assert result["diagnoses"] == []

    def test_unknown_symptom(self):
        result = self.engine.diagnose_disease_risk(["alien_invasion"])
        assert len(result["diagnoses"]) == 0


class TestGetTreatmentRecommendation:
    def setup_method(self):
        self.engine = BeeDomainEngine()

    def test_varroa_treatment(self):
        result = self.engine.get_treatment_recommendation("varroa", "high")
        assert "recommendation" in result
        assert result["severity"] == "high"

    def test_unknown_disease(self):
        result = self.engine.get_treatment_recommendation("unknown_disease")
        assert "error" in result
        assert "known_diseases" in result


class TestStats:
    def test_stats(self):
        engine = BeeDomainEngine()
        engine.assess_colony_health({"temperature": 35.0})
        s = engine.stats()
        assert s["assessment_count"] >= 1
        assert s["known_diseases"] > 0

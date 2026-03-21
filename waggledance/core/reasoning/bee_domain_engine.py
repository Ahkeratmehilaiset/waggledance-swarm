"""Bee domain engine — colony health, swarm risk, honey yield, disease diagnostics.

Provides domain-specific reasoning for beekeeping operations, wrapping
the axiom models in configs/axioms/cottage/ and thermal solver calculations.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ColonyHealthResult:
    """Result of colony health assessment."""
    overall_score: float = 0.0  # 0-100
    temperature_score: float = 0.0
    weight_score: float = 0.0
    activity_score: float = 0.0
    warnings: List[str] = field(default_factory=list)
    grade: str = "unknown"  # excellent, good, fair, poor, critical


@dataclass
class SwarmRiskResult:
    """Result of swarm risk prediction."""
    probability_pct: float = 0.0
    risk_level: str = "normal"  # normal, warning, critical
    factors: Dict[str, float] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class HoneyYieldResult:
    """Result of honey yield estimation."""
    estimated_kg: float = 0.0
    daily_nectar_kg: float = 0.0
    daily_honey_kg: float = 0.0
    warnings: List[str] = field(default_factory=list)


# Disease knowledge base
_DISEASE_DB = {
    "varroa": {
        "symptoms": ["mite_visible", "deformed_wings", "spotty_brood", "population_decline"],
        "severity_default": 0.7,
        "treatments": {
            "low": "Monitor mite drop, treat after harvest with oxalic acid",
            "medium": "Oxalic acid vaporization or formic acid strips",
            "high": "Immediate oxalic acid vaporization, consider ApiLife VAR",
        },
    },
    "nosema": {
        "symptoms": ["dysentery_stains", "crawling_bees", "population_decline", "spring_dwindling"],
        "severity_default": 0.5,
        "treatments": {
            "low": "Monitor — usually self-resolving in spring",
            "medium": "Fumagillin if available, replace old combs",
            "high": "Replace all combs, requeen, fumagillin treatment",
        },
    },
    "foulbrood_american": {
        "symptoms": ["sunken_cappings", "ropy_larvae", "foul_smell", "punctured_cells"],
        "severity_default": 0.95,
        "treatments": {
            "low": "Laboratory confirmation required — contact inspector",
            "medium": "Shook swarm method, destroy old combs",
            "high": "Destroy colony and equipment — legally required in Finland",
        },
    },
    "foulbrood_european": {
        "symptoms": ["twisted_larvae", "discolored_larvae", "patchy_brood"],
        "severity_default": 0.6,
        "treatments": {
            "low": "Requeen with hygienic stock",
            "medium": "Shook swarm, new foundation, requeen",
            "high": "Consider destroying colony, contact inspector",
        },
    },
    "chalkbrood": {
        "symptoms": ["white_mummies", "mummies_at_entrance", "patchy_brood"],
        "severity_default": 0.3,
        "treatments": {
            "low": "Requeen — usually resolves with new queen",
            "medium": "Remove affected combs, requeen, improve ventilation",
            "high": "Full comb replacement, requeen with hygienic stock",
        },
    },
}


class BeeDomainEngine:
    """Domain-specific reasoning engine for beekeeping operations.

    Provides colony health assessment, swarm risk prediction, honey yield
    estimation, disease diagnosis, and treatment recommendations.
    """

    def __init__(self, thermal_solver=None):
        self._thermal = thermal_solver
        self._assessment_count = 0

    def assess_colony_health(self, metrics: Dict[str, float]) -> ColonyHealthResult:
        """Assess overall colony health from sensor/inspection data.

        Args:
            metrics: Dict with optional keys:
                - temperature: cluster temperature (°C)
                - weight_kg: hive weight (kg)
                - activity_level: flight activity (0-1)
                - brood_frames: number of frames with brood

        Returns:
            ColonyHealthResult with overall score and component scores.
        """
        self._assessment_count += 1
        result = ColonyHealthResult()

        # Temperature score (ideal brood temp 34-36°C)
        temp = metrics.get("temperature")
        if temp is not None:
            if 34 <= temp <= 36:
                result.temperature_score = 100
            elif 30 <= temp < 34 or 36 < temp <= 38:
                result.temperature_score = 70
            elif 20 <= temp < 30:
                result.temperature_score = 40
                result.warnings.append(f"Low cluster temperature: {temp}°C")
            else:
                result.temperature_score = 20
                result.warnings.append(f"Critical temperature: {temp}°C")
        else:
            result.temperature_score = 50  # unknown

        # Weight score
        weight = metrics.get("weight_kg")
        if weight is not None:
            if weight >= 25:
                result.weight_score = 100
            elif weight >= 15:
                result.weight_score = 70
            elif weight >= 8:
                result.weight_score = 40
                result.warnings.append(f"Low hive weight: {weight}kg — check food reserves")
            else:
                result.weight_score = 10
                result.warnings.append(f"Critical hive weight: {weight}kg — emergency feeding needed")
        else:
            result.weight_score = 50

        # Activity score
        activity = metrics.get("activity_level")
        if activity is not None:
            result.activity_score = min(100, activity * 100)
            if activity < 0.1:
                result.warnings.append("Very low flight activity")
        else:
            result.activity_score = 50

        # Overall composite
        scores = [result.temperature_score, result.weight_score, result.activity_score]
        result.overall_score = sum(scores) / len(scores)

        # Grade
        if result.overall_score >= 80:
            result.grade = "excellent"
        elif result.overall_score >= 60:
            result.grade = "good"
        elif result.overall_score >= 40:
            result.grade = "fair"
        elif result.overall_score >= 20:
            result.grade = "poor"
        else:
            result.grade = "critical"

        return result

    def predict_swarm_risk(self, queen_age: float, empty_combs: int,
                           total_combs: int, queen_cells: int,
                           season_factor: float = 0.5) -> SwarmRiskResult:
        """Predict swarming probability based on hive conditions.

        Uses the swarm_risk axiom model formula:
        space_pressure = 1 - (empty_combs / total_combs)
        queen_age_factor = queen_age / 3.0
        score = space_pressure*40 + queen_age_factor*30 + queen_cells*20 + season_factor*10

        Args:
            queen_age: queen age in years
            empty_combs: number of empty/partially filled combs
            total_combs: total combs in hive
            queen_cells: number of queen cells visible
            season_factor: 0-1 seasonal swarming pressure

        Returns:
            SwarmRiskResult with probability and recommendations.
        """
        self._assessment_count += 1

        if total_combs <= 0:
            total_combs = 1
        space_pressure = 1.0 - (empty_combs / total_combs)
        queen_age_factor = queen_age / 3.0
        score = (space_pressure * 40 + queen_age_factor * 30
                 + queen_cells * 20 + season_factor * 10)
        probability = min(score, 100.0)

        result = SwarmRiskResult(
            probability_pct=round(probability, 1),
            factors={
                "space_pressure": round(space_pressure, 3),
                "queen_age_factor": round(queen_age_factor, 3),
                "queen_cells": queen_cells,
                "season_factor": season_factor,
            },
        )

        if probability >= 70:
            result.risk_level = "critical"
            result.recommendations = [
                "Immediate action needed — inspect every 5 days",
                "Add honey super or split colony",
                "Consider artificial swarm or queen cell removal",
            ]
        elif probability >= 40:
            result.risk_level = "warning"
            result.recommendations = [
                "Inspect every 7-10 days",
                "Add space (super or frames)",
                "Monitor queen cells closely",
            ]
        else:
            result.risk_level = "normal"
            result.recommendations = ["Continue regular inspections"]

        return result

    def estimate_honey_yield(self, colony_strength: float,
                             flow_days: float,
                             forager_ratio: float = 0.35) -> HoneyYieldResult:
        """Estimate seasonal honey yield.

        Uses the honey_yield axiom model:
        daily_foragers = colony_strength * forager_ratio
        daily_nectar = daily_foragers * 40mg * 10 flights / 1e6  (kg)
        daily_honey = daily_nectar * 0.25  (nectar-to-honey)
        season_honey = daily_honey * flow_days * 0.5  (surplus fraction)

        Args:
            colony_strength: total bee population
            flow_days: active nectar flow days
            forager_ratio: fraction of foragers (default 0.35)

        Returns:
            HoneyYieldResult with estimated kg.
        """
        self._assessment_count += 1

        nectar_load_mg = 40.0
        flights_per_day = 10.0
        nectar_to_honey = 0.25
        surplus_fraction = 0.5

        daily_foragers = colony_strength * forager_ratio
        daily_nectar = daily_foragers * nectar_load_mg * flights_per_day / 1_000_000
        daily_honey = daily_nectar * nectar_to_honey
        season_honey = daily_honey * flow_days * surplus_fraction

        result = HoneyYieldResult(
            estimated_kg=round(season_honey, 2),
            daily_nectar_kg=round(daily_nectar, 4),
            daily_honey_kg=round(daily_honey, 4),
        )

        if colony_strength < 10000:
            result.warnings.append("Colony too weak for surplus honey")
        if flow_days < 20:
            result.warnings.append("Very short nectar flow — yield will be low")

        return result

    def diagnose_disease_risk(self, symptoms: List[str]) -> Dict[str, Any]:
        """Rule-based disease diagnosis from observed symptoms.

        Args:
            symptoms: List of symptom identifiers (e.g. ["mite_visible", "deformed_wings"])

        Returns:
            Dict with possible diseases, match scores, and recommendations.
        """
        self._assessment_count += 1
        symptom_set = set(symptoms)
        if not symptom_set:
            return {"diagnoses": [], "message": "No symptoms provided"}

        diagnoses = []
        for disease, info in _DISEASE_DB.items():
            known = set(info["symptoms"])
            matched = symptom_set & known
            if matched:
                match_score = len(matched) / len(known)
                diagnoses.append({
                    "disease": disease,
                    "match_score": round(match_score, 2),
                    "matched_symptoms": sorted(matched),
                    "severity_default": info["severity_default"],
                })

        diagnoses.sort(key=lambda d: d["match_score"], reverse=True)
        return {"diagnoses": diagnoses, "symptom_count": len(symptoms)}

    def get_treatment_recommendation(self, disease: str,
                                     severity: str = "medium") -> Dict[str, Any]:
        """Get treatment recommendation for a diagnosed disease.

        Args:
            disease: disease identifier (e.g. "varroa", "nosema")
            severity: "low", "medium", or "high"

        Returns:
            Dict with recommendation text and disease info.
        """
        self._assessment_count += 1
        info = _DISEASE_DB.get(disease)
        if not info:
            return {"error": f"Unknown disease: {disease}",
                    "known_diseases": sorted(_DISEASE_DB.keys())}

        treatment = info["treatments"].get(severity, info["treatments"]["medium"])
        return {
            "disease": disease,
            "severity": severity,
            "recommendation": treatment,
            "severity_default": info["severity_default"],
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "assessment_count": self._assessment_count,
            "known_diseases": len(_DISEASE_DB),
            "thermal_solver_available": self._thermal is not None,
        }

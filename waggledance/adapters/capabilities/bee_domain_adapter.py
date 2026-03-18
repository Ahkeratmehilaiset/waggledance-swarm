"""Capability adapter for the BeeDomainEngine (cottage/apiary profiles)."""

from __future__ import annotations

import time
from typing import Any, Dict, List


class BeeDomainAdapter:
    """Wraps BeeDomainEngine as a capability executor.

    Provides colony health assessment, swarm risk prediction, honey yield
    estimation, and disease diagnosis through the standard capability interface.
    """

    CAPABILITY_ID = "solve.bee_domain"

    def __init__(self):
        self._engine = None
        self._available = False
        self._call_count = 0
        try:
            from waggledance.core.reasoning.bee_domain_engine import BeeDomainEngine
            self._engine = BeeDomainEngine()
            self._available = True
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self._available

    def execute(self, query: str = "", operation: str = "health",
                **kwargs) -> Dict[str, Any]:
        """Execute bee domain reasoning.

        Args:
            operation: One of "health", "swarm_risk", "honey_yield",
                       "diagnose", "treatment"
            **kwargs: Operation-specific parameters
        """
        t0 = time.time()
        self._call_count += 1

        if not self._available:
            return {
                "success": False,
                "error": "Bee domain engine not available",
                "capability_id": self.CAPABILITY_ID,
            }

        try:
            if operation == "health":
                metrics = kwargs.get("metrics", {})
                result = self._engine.assess_colony_health(metrics)
                data = {
                    "overall_score": result.overall_score,
                    "grade": result.grade,
                    "temperature_score": result.temperature_score,
                    "weight_score": result.weight_score,
                    "activity_score": result.activity_score,
                    "warnings": result.warnings,
                }
            elif operation == "swarm_risk":
                result = self._engine.predict_swarm_risk(
                    queen_age=kwargs.get("queen_age", 1.0),
                    empty_combs=kwargs.get("empty_combs", 2),
                    total_combs=kwargs.get("total_combs", 10),
                    queen_cells=kwargs.get("queen_cells", 0),
                    season_factor=kwargs.get("season_factor", 0.5),
                )
                data = {
                    "probability_pct": result.probability_pct,
                    "risk_level": result.risk_level,
                    "factors": result.factors,
                    "recommendations": result.recommendations,
                }
            elif operation == "honey_yield":
                result = self._engine.estimate_honey_yield(
                    colony_strength=kwargs.get("colony_strength", 40000),
                    flow_days=kwargs.get("flow_days", 30),
                    forager_ratio=kwargs.get("forager_ratio", 0.35),
                )
                data = {
                    "estimated_kg": result.estimated_kg,
                    "daily_nectar_kg": result.daily_nectar_kg,
                    "daily_honey_kg": result.daily_honey_kg,
                    "warnings": result.warnings,
                }
            elif operation == "diagnose":
                symptoms = kwargs.get("symptoms", [])
                data = self._engine.diagnose_disease_risk(symptoms)
            elif operation == "treatment":
                disease = kwargs.get("disease", "")
                severity = kwargs.get("severity", "medium")
                data = self._engine.get_treatment_recommendation(disease, severity)
            else:
                data = {"error": f"Unknown operation: {operation}"}

            elapsed = round((time.time() - t0) * 1000, 2)
            return {
                "success": True,
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "gold",
                "latency_ms": elapsed,
                "operation": operation,
                "result": data,
            }

        except Exception as exc:
            elapsed = round((time.time() - t0) * 1000, 2)
            return {
                "success": False,
                "capability_id": self.CAPABILITY_ID,
                "latency_ms": elapsed,
                "error": str(exc),
            }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self._available,
            "call_count": self._call_count,
            "engine_stats": self._engine.stats() if self._engine else {},
        }

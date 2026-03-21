"""Adapter wrapping OptimizationEngine as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.reasoning.optimization_engine import OptimizationEngine

logger = logging.getLogger(__name__)


class OptimizationAdapter:
    """Capability adapter for the OptimizationEngine reasoning engine."""

    CAPABILITY_ID = "optimize.schedule"

    def __init__(self):
        self._engine = OptimizationEngine()
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return True

    def execute(self, problem_type: str = "energy_cost",
                inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute optimization.

        Args:
            problem_type: energy_cost, allocate_resources, or schedule_tasks
            inputs: problem-specific parameters
        """
        t0 = time.monotonic()
        self._call_count += 1
        inputs = inputs or {}

        if problem_type == "allocate_resources":
            result = self._engine.allocate_resources(
                tasks=inputs.get("tasks", []),
                capacity=inputs.get("capacity", 100),
            )
        elif problem_type == "schedule_tasks":
            result = self._engine.schedule_tasks(
                tasks=inputs.get("tasks", []),
                time_slots=inputs.get("time_slots", 24),
            )
        else:
            result = self._engine.minimize_energy_cost(
                hourly_prices=inputs.get("hourly_prices", [10] * 24),
                required_kwh=inputs.get("required_kwh", 10),
                max_power_kw=inputs.get("max_power_kw", 3.0),
            )

        elapsed = (time.monotonic() - t0) * 1000

        if result.success:
            self._success_count += 1
            return {
                "success": True,
                "objective_value": result.objective_value,
                "solution": result.solution,
                "schedule": result.schedule,
                "improvement_pct": result.improvement_pct,
                "method": result.method,
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "gold",
                "latency_ms": round(elapsed, 2),
            }
        return {
            "success": False,
            "error": "Optimization failed (empty input or infeasible)",
            "capability_id": self.CAPABILITY_ID,
            "latency_ms": round(elapsed, 2),
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "successes": self._success_count,
            "success_rate": (self._success_count / self._call_count
                             if self._call_count else 0.0),
        }

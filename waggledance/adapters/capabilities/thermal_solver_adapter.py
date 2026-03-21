"""Adapter wrapping ThermalSolver as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from waggledance.core.reasoning.thermal_solver import ThermalSolver

logger = logging.getLogger(__name__)


class ThermalSolverAdapter:
    """Capability adapter for the ThermalSolver reasoning engine."""

    CAPABILITY_ID = "solve.thermal"

    def __init__(self):
        self._solver = ThermalSolver()
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return True

    def execute(self, query: str = "", calc_type: str = "",
                inputs: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Execute thermal calculation.

        Args:
            query: optional text query (used to infer calc_type if not given)
            calc_type: explicit calculation type (heat_loss, heating_cost, frost_risk, etc.)
            inputs: calculation parameters
        """
        t0 = time.monotonic()
        self._call_count += 1
        inputs = inputs or {}

        if not calc_type and query:
            calc_type = self._infer_calc_type(query)
        if not calc_type:
            calc_type = "heat_loss"

        result = self._solver.solve(calc_type, inputs)
        elapsed = (time.monotonic() - t0) * 1000

        if result.success:
            self._success_count += 1
            return {
                "success": True,
                "value": result.value,
                "unit": result.unit,
                "formula": result.formula,
                "inputs_used": result.inputs_used,
                "warnings": result.warnings,
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "gold",
                "latency_ms": round(elapsed, 2),
            }
        return {
            "success": False,
            "error": "; ".join(result.warnings) or "Calculation failed",
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

    @staticmethod
    def _infer_calc_type(query: str) -> str:
        q = query.lower()
        if any(w in q for w in ("frost", "pakkas", "freeze", "pipe")):
            return "frost_risk"
        if any(w in q for w in ("cost", "kustannus", "price", "hinta")):
            return "heating_cost"
        if any(w in q for w in ("hive", "pesä", "colony", "cluster")):
            return "hive_thermal"
        if any(w in q for w in ("cop", "pump", "pumppu")):
            return "heat_pump_cop"
        return "heat_loss"

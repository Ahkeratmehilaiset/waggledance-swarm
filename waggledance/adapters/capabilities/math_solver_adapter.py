"""Adapter wrapping legacy core.math_solver.MathSolver as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MathSolver = None


def _get_solver():
    global _MathSolver
    if _MathSolver is None:
        try:
            from core.math_solver import MathSolver
            _MathSolver = MathSolver
        except ImportError:
            _MathSolver = None
    return _MathSolver


class MathSolverAdapter:
    """Capability adapter for the legacy MathSolver.

    Exposes MathSolver.is_math() and MathSolver.solve() through the
    autonomy capability interface.
    """

    CAPABILITY_ID = "solve.math"

    def __init__(self):
        self._solver_cls = _get_solver()
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return self._solver_cls is not None

    def can_handle(self, query: str) -> bool:
        """Check if the query contains a math expression."""
        if not self._solver_cls:
            return False
        return self._solver_cls.is_math(query)

    def execute(self, query: str) -> Dict[str, Any]:
        """Execute math solving and return structured result."""
        t0 = time.monotonic()
        self._call_count += 1
        if not self._solver_cls:
            return {"success": False, "error": "MathSolver not available"}

        result = self._solver_cls.solve(query)
        elapsed = (time.monotonic() - t0) * 1000

        if result is not None:
            self._success_count += 1
            return {
                "success": True,
                "value": result,
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "gold",
                "latency_ms": round(elapsed, 2),
            }
        return {
            "success": False,
            "error": "Could not evaluate expression",
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

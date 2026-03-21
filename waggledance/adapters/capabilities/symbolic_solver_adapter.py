"""Adapter wrapping legacy core.symbolic_solver as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SymbolicSolver = None
_ModelRegistry = None


def _get_classes():
    global _SymbolicSolver, _ModelRegistry
    if _SymbolicSolver is None:
        try:
            from core.symbolic_solver import SymbolicSolver, ModelRegistry
            _SymbolicSolver = SymbolicSolver
            _ModelRegistry = ModelRegistry
        except ImportError:
            pass


class SymbolicSolverAdapter:
    """Capability adapter for the legacy SymbolicSolver.

    Wraps the axiom-based formula evaluation engine for use in the
    autonomy capability pipeline.
    """

    CAPABILITY_ID = "solve.symbolic"

    def __init__(self, solver=None, registry=None):
        _get_classes()
        self._registry = registry
        self._solver = solver
        self._call_count = 0
        self._success_count = 0

        if self._solver is None and _SymbolicSolver:
            try:
                self._registry = _ModelRegistry() if _ModelRegistry and not registry else registry
                self._solver = _SymbolicSolver(registry=self._registry)
            except Exception as exc:
                logger.warning("SymbolicSolverAdapter: init failed: %s", exc)

    @property
    def available(self) -> bool:
        return self._solver is not None

    def can_handle(self, model_id: str) -> bool:
        """Check if a model_id exists in the registry."""
        if not self._registry:
            return False
        return self._registry.get(model_id) is not None

    def list_models(self):
        """List available axiom models."""
        if not self._registry:
            return []
        return self._registry.list_models()

    def execute(self, model_id: str, inputs: Dict[str, Any] = None,
                query: str = "") -> Dict[str, Any]:
        """Execute symbolic solving."""
        t0 = time.monotonic()
        self._call_count += 1
        if not self._solver:
            return {"success": False, "error": "SymbolicSolver not available"}

        try:
            if query:
                result = self._solver.solve_for_chat(model_id, query, extra_inputs=inputs)
                elapsed = (time.monotonic() - t0) * 1000
                self._success_count += 1
                return {
                    "success": True,
                    "value": result.value if hasattr(result, "value") else str(result),
                    "capability_id": self.CAPABILITY_ID,
                    "quality_path": "gold",
                    "model_id": model_id,
                    "latency_ms": round(elapsed, 2),
                }
            else:
                result = self._solver.solve(model_id, inputs)
                elapsed = (time.monotonic() - t0) * 1000
                if result.success:
                    self._success_count += 1
                    return {
                        "success": True,
                        "value": result.value,
                        "unit": result.unit,
                        "formulas": result.formulas_used,
                        "capability_id": self.CAPABILITY_ID,
                        "quality_path": "gold",
                        "model_id": model_id,
                        "latency_ms": round(elapsed, 2),
                    }
                return {
                    "success": False,
                    "error": result.error or "Solver returned no result",
                    "warnings": result.warnings,
                    "capability_id": self.CAPABILITY_ID,
                    "latency_ms": round(elapsed, 2),
                }
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return {
                "success": False,
                "error": str(exc),
                "capability_id": self.CAPABILITY_ID,
                "latency_ms": round(elapsed, 2),
            }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "models": self.list_models(),
            "calls": self._call_count,
            "successes": self._success_count,
        }

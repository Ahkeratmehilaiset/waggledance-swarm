"""
Model-based interface — abstract solver interface and ModelResult.

Provides the base contract for all domain solvers (symbolic, ML, hybrid).
Each solver loads an axiom YAML definition and returns a ModelResult.
"""
from __future__ import annotations

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class ModelResult:
    """Result from a model-based solver."""
    success: bool
    value: Optional[float] = None
    unit: str = ""
    formula_used: str = ""
    inputs_used: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    derivation_steps: list[dict[str, Any]] = field(default_factory=list)
    validation: list[dict[str, Any]] = field(default_factory=list)
    compute_time_ms: float = 0.0
    confidence: float = 0.0
    risk_level: str = "normal"
    fallback_suggested: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "value": self.value,
            "unit": self.unit,
            "formula_used": self.formula_used,
            "inputs_used": self.inputs_used,
            "assumptions": list(self.assumptions),
            "derivation_steps": list(self.derivation_steps),
            "validation": list(self.validation),
            "compute_time_ms": round(self.compute_time_ms, 2),
            "confidence": round(self.confidence, 3),
            "risk_level": self.risk_level,
            "fallback_suggested": self.fallback_suggested,
            "error": self.error,
        }

    def to_natural_language(self, lang: str = "fi") -> str:
        """Format result as human-readable text."""
        if not self.success:
            if lang == "fi":
                return f"Laskenta epaonnistui: {self.error or 'tuntematon virhe'}"
            return f"Calculation failed: {self.error or 'unknown error'}"

        # Build derivation chain
        steps_str = ""
        if self.derivation_steps:
            parts = []
            for step in self.derivation_steps:
                name = step.get("name", "?")
                val = step.get("value")
                unit = step.get("unit", "")
                formula = step.get("formula", "")
                if val is not None:
                    parts.append(f"  {name} = {val:.4g} {unit}".rstrip())
                    if formula:
                        parts[-1] += f"  ({formula})"
            steps_str = "\n".join(parts)

        # Assumptions
        assumptions_str = ""
        if self.assumptions:
            if lang == "fi":
                assumptions_str = "\nOletukset: " + ", ".join(self.assumptions)
            else:
                assumptions_str = "\nAssumptions: " + ", ".join(self.assumptions)

        # Risk warning
        risk_str = ""
        if self.risk_level == "critical":
            risk_str = "\n(!!) " + ("KRIITTINEN" if lang == "fi" else "CRITICAL")
        elif self.risk_level == "warning":
            risk_str = "\n(!) " + ("VAROITUS" if lang == "fi" else "WARNING")

        if lang == "fi":
            result = f"Tulos: {self.value:.4g} {self.unit}"
            if self.formula_used:
                result += f"\nKaava: {self.formula_used}"
            if steps_str:
                result += f"\nLaskenta:\n{steps_str}"
        else:
            result = f"Result: {self.value:.4g} {self.unit}"
            if self.formula_used:
                result += f"\nFormula: {self.formula_used}"
            if steps_str:
                result += f"\nDerivation:\n{steps_str}"

        return result + assumptions_str + risk_str


class BaseModel(ABC):
    """Abstract base for all domain solvers."""

    def __init__(self, model_id: str, axiom_data: dict[str, Any]):
        self.model_id = model_id
        self.axiom = axiom_data

    @abstractmethod
    def solve(self, inputs: dict[str, Any]) -> ModelResult:
        """Solve with given inputs. Must be implemented by subclass."""
        ...

    @abstractmethod
    def get_required_inputs(self) -> list[dict[str, Any]]:
        """Return list of required input variable specs."""
        ...

    def validate_result(self, result: ModelResult) -> ModelResult:
        """Run validation checks on a result."""
        return result

    def solve_safe(self, inputs: dict[str, Any]) -> ModelResult:
        """Safe wrapper: catches exceptions, measures time."""
        t0 = time.perf_counter()
        try:
            result = self.solve(inputs)
            result.compute_time_ms = (time.perf_counter() - t0) * 1000
            result = self.validate_result(result)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            log.error("Solver %s failed: %s", self.model_id, e)
            return ModelResult(
                success=False,
                error=str(e),
                compute_time_ms=elapsed,
                fallback_suggested="llm_reasoning",
            )

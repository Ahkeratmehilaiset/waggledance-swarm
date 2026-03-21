"""
Symbolic solver for axiom-based formula evaluation.
Loads YAML model definitions, evaluates formulas with safe math context.
"""

import logging
import math
import re
import time
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

from core.model_interface import ModelResult
from core.safe_eval import safe_eval, SafeEvalError

log = logging.getLogger(__name__)


@dataclass
class SolverResult:
    success: bool
    value: Any
    unit: str
    all_values: Dict[str, Any]
    formulas_used: List[str]
    inputs_used: Dict[str, Any]
    assumptions: List[str]
    derivation_steps: List[Dict[str, Any]]
    validation: List[Dict[str, Any]]
    risk_level: str
    compute_time_ms: float
    warnings: List[str]
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "value": self.value,
            "unit": self.unit,
            "all_values": self.all_values,
            "formulas_used": self.formulas_used,
            "inputs_used": self.inputs_used,
            "assumptions": self.assumptions,
            "derivation_steps": self.derivation_steps,
            "validation": self.validation,
            "risk_level": self.risk_level,
            "compute_time_ms": self.compute_time_ms,
            "warnings": self.warnings,
            "error": self.error,
        }

    def to_model_result(self) -> "ModelResult":
        """Convert to ModelResult for chat pipeline integration."""
        # Normalize derivation_steps: "result" -> "value" for ModelResult
        steps = []
        for s in self.derivation_steps:
            step = dict(s)
            if "result" in step and "value" not in step:
                step["value"] = step.pop("result")
            steps.append(step)
        return ModelResult(
            success=self.success,
            value=self.value if isinstance(self.value, (int, float)) else None,
            unit=self.unit,
            formula_used=self.formulas_used[-1] if self.formulas_used else "",
            inputs_used=self.inputs_used,
            assumptions=self.assumptions,
            derivation_steps=steps,
            validation=self.validation,
            compute_time_ms=self.compute_time_ms,
            confidence=0.9 if self.success and not self.warnings else 0.6,
            risk_level=self.risk_level,
            fallback_suggested="llm_reasoning" if not self.success else None,
            error=self.error,
        )


class ModelRegistry:
    """Loads and caches axiom YAML models from configs/axioms/."""

    def __init__(self, axioms_dir: Optional[str] = None):
        if axioms_dir is None:
            here = Path(__file__).parent.parent
            axioms_dir = str(here / "configs" / "axioms")
        self._dir = Path(axioms_dir)
        self._cache: Dict[str, Dict] = {}
        self._loaded = False

    def _load_all(self):
        if self._loaded:
            return
        if not self._dir.exists():
            self._loaded = True
            return
        for yaml_file in self._dir.rglob("*.yaml"):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    model = yaml.safe_load(f)
                if model and "model_id" in model:
                    self._cache[model["model_id"]] = model
            except Exception:
                pass
        self._loaded = True

    def get(self, model_id: str) -> Optional[Dict]:
        self._load_all()
        return self._cache.get(model_id)

    def list_models(self) -> List[str]:
        self._load_all()
        return list(self._cache.keys())

    def reload(self):
        self._cache.clear()
        self._loaded = False
        self._load_all()

    @classmethod
    def from_capsule(cls, capsule_data: Dict,
                     base_dir: Optional[str] = None) -> "ModelRegistry":
        """Create registry pre-loaded with models referenced in a capsule."""
        if base_dir is None:
            base_dir = str(Path(__file__).parent.parent)
        reg = cls(axioms_dir=str(Path(base_dir) / "configs" / "axioms"))
        # Load all axioms from disk, then verify capsule models exist
        reg._load_all()
        capsule_models = capsule_data.get("models", [])
        for m in capsule_models:
            mid = m.get("id")
            if mid and mid not in reg._cache:
                # Try loading from explicit axiom_file
                axiom_file = m.get("axiom_file")
                if axiom_file:
                    path = Path(base_dir) / axiom_file
                    if path.exists():
                        try:
                            with open(path, encoding="utf-8") as f:
                                data = yaml.safe_load(f)
                            if data:
                                reg._cache[mid] = data
                        except Exception:
                            pass
        return reg


# ── Input extraction from natural language ────────────────────

# Patterns: "-15 astetta" / "-15°C" / "astetta -15" / "temperature -15"
_TEMP_PATTERNS = [
    re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:astetta|°C|celsius|C\b)", re.IGNORECASE),
    re.compile(r"(?:astetta|lampotila|temperature|ulkona)\s*(?:on\s+)?(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:pakkasta)", re.IGNORECASE),
]

_AREA_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:m2|m²|nelio)", re.IGNORECASE),
]

_PRICE_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:c/kwh|sentti|cent)", re.IGNORECASE),
    re.compile(r"(?:hinta|price|spot)\s*(?:on\s+)?(\d+(?:\.\d+)?)", re.IGNORECASE),
]


def extract_inputs_from_query(query: str, model_id: str = "") -> Dict[str, Any]:
    """Extract numeric inputs from a natural language query.

    Returns a dict of variable_name -> value for recognized patterns.
    """
    inputs: Dict[str, Any] = {}

    # Temperature extraction
    for pat in _TEMP_PATTERNS:
        m = pat.search(query)
        if m:
            val = float(m.group(1))
            # "pakkasta" = frost = negative
            if "pakkasta" in query.lower() and val > 0:
                val = -val
            inputs["T_outdoor"] = val
            break

    # Area extraction
    for pat in _AREA_PATTERNS:
        m = pat.search(query)
        if m:
            inputs["area_m2"] = float(m.group(1))
            break

    # Price extraction
    for pat in _PRICE_PATTERNS:
        m = pat.search(query)
        if m:
            inputs["spot_price_ckwh"] = float(m.group(1))
            break

    return inputs


class SymbolicSolver:
    """Evaluates axiom formula models with safe math context."""

    def __init__(self, registry: Optional[ModelRegistry] = None):
        self.registry = registry or ModelRegistry()

    def solve(self, model_id: str, inputs: Optional[Dict[str, Any]] = None) -> SolverResult:
        t0 = time.monotonic()
        inputs = inputs or {}

        model = self.registry.get(model_id)
        if model is None:
            return SolverResult(
                success=False, value=None, unit="", all_values={},
                formulas_used=[], inputs_used={}, assumptions=[],
                derivation_steps=[], validation=[], risk_level="unknown",
                compute_time_ms=0.0, warnings=[],
                error=f"Model not found: {model_id}",
            )

        variables = model.get("variables", {})
        formulas = model.get("formulas", [])
        validations = model.get("validation", [])
        risk_levels = model.get("risk_levels", {})

        # Step 1: Fill inputs + defaults
        context: Dict[str, Any] = {}
        assumptions: List[str] = []
        warnings: List[str] = []
        inputs_used: Dict[str, Any] = {}

        for var_name, var_def in variables.items():
            if var_name in inputs:
                val = inputs[var_name]
                context[var_name] = val
                inputs_used[var_name] = val
            elif "default" in var_def:
                val = var_def["default"]
                context[var_name] = val
                assumptions.append(f"{var_name}={val} ({var_def.get('unit', '')})")

        # Step 2: Range checks → warnings
        for var_name, var_def in variables.items():
            if var_name not in context:
                continue
            val = context[var_name]
            rng = var_def.get("range")
            if rng and isinstance(val, (int, float)):
                lo, hi = rng
                if val < lo:
                    warnings.append(
                        f"{var_name}={val} below minimum {lo} {var_def.get('unit', '')}"
                    )
                elif val > hi:
                    warnings.append(
                        f"{var_name}={val} above maximum {hi} {var_def.get('unit', '')}"
                    )

        # Step 3: Evaluate formulas sequentially
        eval_ctx = dict(context)

        derivation_steps: List[Dict[str, Any]] = []
        formulas_used: List[str] = []
        last_value = None
        last_unit = ""
        eval_error: Optional[str] = None

        for formula_def in formulas:
            fname = formula_def.get("name", "")
            fexpr = formula_def.get("formula", "")
            funit = formula_def.get("output_unit", "")
            fdesc = formula_def.get("description", "")

            try:
                result = safe_eval(fexpr, eval_ctx)
                eval_ctx[fname] = result
                last_value = result
                last_unit = funit
                formulas_used.append(fname)
                derivation_steps.append({
                    "name": fname,
                    "formula": fexpr,
                    "result": result,
                    "unit": funit,
                    "description": fdesc,
                })
            except SafeEvalError as exc:
                eval_error = f"Formula '{fname}': {exc}"
                derivation_steps.append({
                    "name": fname,
                    "formula": fexpr,
                    "result": None,
                    "unit": funit,
                    "description": fdesc,
                    "error": str(exc),
                })
                break
            except Exception as exc:
                eval_error = f"Formula '{fname}': {exc}"
                derivation_steps.append({
                    "name": fname,
                    "formula": fexpr,
                    "result": None,
                    "unit": funit,
                    "description": fdesc,
                    "error": str(exc),
                })
                break

        # Step 4: Validate results
        validation_results: List[Dict[str, Any]] = []
        for v in validations:
            check = v.get("check", "")
            msg = v.get("message", "")
            try:
                passed = bool(safe_eval(check, eval_ctx))
            except SafeEvalError:
                passed = False
            except Exception as exc:
                passed = False
                msg = f"{msg} [eval error: {exc}]"
            validation_results.append({"check": check, "passed": passed, "message": msg})

        # Step 5: Risk level
        risk_level = "unknown"
        for level in ("critical", "warning", "normal"):
            expr = risk_levels.get(level)
            if not expr:
                continue
            try:
                if bool(safe_eval(expr, eval_ctx)):
                    risk_level = level
                    break
            except SafeEvalError:
                pass
            except Exception:
                pass

        compute_time_ms = (time.monotonic() - t0) * 1000

        return SolverResult(
            success=eval_error is None,
            value=last_value,
            unit=last_unit,
            all_values={k: eval_ctx[k] for k in formulas_used if k in eval_ctx},
            formulas_used=formulas_used,
            inputs_used=inputs_used,
            assumptions=assumptions,
            derivation_steps=derivation_steps,
            validation=validation_results,
            risk_level=risk_level,
            compute_time_ms=compute_time_ms,
            warnings=warnings,
            error=eval_error,
        )

    def solve_for_chat(self, model_id: str, query: str,
                       extra_inputs: Optional[Dict[str, Any]] = None
                       ) -> ModelResult:
        """Solve for chat: extract inputs from query, solve, return ModelResult."""
        inputs = extract_inputs_from_query(query, model_id)
        if extra_inputs:
            inputs.update(extra_inputs)
        result = self.solve(model_id, inputs)
        return result.to_model_result()

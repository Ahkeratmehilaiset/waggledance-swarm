"""
Explainability engine: builds step-by-step decision traces with natural language output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExplanationStep:
    step_num: int
    action: str        # "input", "assumption", "formula", "validation", "risk_assessment"
    name: str
    description: str
    result: Any
    unit: str = ""
    formula: str = ""
    passed: Optional[bool] = None  # for validation steps

    def to_dict(self) -> Dict:
        d: Dict[str, Any] = {
            "step": self.step_num,
            "action": self.action,
            "name": self.name,
            "description": self.description,
            "result": self.result,
        }
        if self.unit:
            d["unit"] = self.unit
        if self.formula:
            d["formula"] = self.formula
        if self.passed is not None:
            d["passed"] = self.passed
        return d

    def to_natural_language(self, lang: str = "fi") -> str:
        return self._to_fi() if lang == "fi" else self._to_en()

    # ------------------------------------------------------------------

    def _fmt_val(self) -> str:
        if isinstance(self.result, float):
            return f"{self.result:.4g}"
        return str(self.result)

    def _to_fi(self) -> str:
        if self.action == "input":
            return f"  Syöte: {self.name} = {self._fmt_val()} {self.unit}".rstrip()
        if self.action == "assumption":
            return f"  Oletus: {self.name} = {self._fmt_val()} {self.unit} (oletusarvo)".rstrip()
        if self.action == "formula":
            suffix = f"  [{self.formula}]" if self.formula else ""
            return f"  Laskettu: {self.name} = {self._fmt_val()} {self.unit}{suffix}".rstrip()
        if self.action == "validation":
            status = "✓ OK" if self.passed else "✗ VIRHE"
            return f"  Tarkistus {status}: {self.description}"
        if self.action == "risk_assessment":
            return f"  Riskiluokka: {self.result}"
        return f"  {self.name}: {self._fmt_val()}"

    def _to_en(self) -> str:
        if self.action == "input":
            return f"  Input: {self.name} = {self._fmt_val()} {self.unit}".rstrip()
        if self.action == "assumption":
            return f"  Assumed: {self.name} = {self._fmt_val()} {self.unit} (default)".rstrip()
        if self.action == "formula":
            suffix = f"  [{self.formula}]" if self.formula else ""
            return f"  Computed: {self.name} = {self._fmt_val()} {self.unit}{suffix}".rstrip()
        if self.action == "validation":
            status = "PASS" if self.passed else "FAIL"
            return f"  Validation {status}: {self.description}"
        if self.action == "risk_assessment":
            return f"  Risk level: {self.result}"
        return f"  {self.name}: {self._fmt_val()}"


@dataclass
class Explanation:
    model_id: str
    model_name: str
    steps: List[ExplanationStep] = field(default_factory=list)
    conclusion: str = ""
    risk_level: str = "unknown"

    def add_step(self, **kwargs) -> "Explanation":
        n = len(self.steps) + 1
        self.steps.append(ExplanationStep(step_num=n, **kwargs))
        return self

    def to_dict(self) -> Dict:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "steps": [s.to_dict() for s in self.steps],
            "conclusion": self.conclusion,
            "risk_level": self.risk_level,
        }

    def to_natural_language(self, lang: str = "fi") -> str:
        lines: List[str] = []
        header = f"=== {self.model_name} ==="
        lines.append(header)
        lines.append("Laskentavaiheet:" if lang == "fi" else "Calculation steps:")
        for step in self.steps:
            lines.append(step.to_natural_language(lang))
        if self.conclusion:
            label = "Johtopäätös" if lang == "fi" else "Conclusion"
            lines.append(f"{label}: {self.conclusion}")
        return "\n".join(lines)


class ExplainabilityEngine:
    """Builds Explanation objects from SolverResult data."""

    def from_solver_result(
        self,
        solver_result: Any,
        model_id: str = "",
        model_name: str = "",
    ) -> Explanation:
        """Create an Explanation from a SolverResult (object or dict)."""
        r: Dict = solver_result.to_dict() if hasattr(solver_result, "to_dict") else solver_result

        expl = Explanation(model_id=model_id, model_name=model_name or model_id)

        # Inputs provided by caller
        for k, v in r.get("inputs_used", {}).items():
            expl.add_step(action="input", name=k, description="Provided input", result=v)

        # Defaults assumed
        for assumption in r.get("assumptions", []):
            # format: "name=val (unit)"
            name, _, rest = assumption.partition("=")
            val_str = rest.split("(")[0].strip()
            unit_str = ""
            if "(" in rest and ")" in rest:
                unit_str = rest.split("(")[1].rstrip(")")
            try:
                val: Any = float(val_str)
                if val == int(val):
                    val = int(val)
            except (ValueError, OverflowError):
                val = val_str
            expl.add_step(
                action="assumption",
                name=name,
                description="Default value used",
                result=val,
                unit=unit_str,
            )

        # Formula derivations
        for step in r.get("derivation_steps", []):
            expl.add_step(
                action="formula",
                name=step.get("name", ""),
                description=step.get("description", ""),
                result=step.get("result"),
                unit=step.get("unit", ""),
                formula=step.get("formula", ""),
            )

        # Validation checks
        for v in r.get("validation", []):
            expl.add_step(
                action="validation",
                name=v.get("check", ""),
                description=v.get("message", ""),
                result=v.get("passed"),
                passed=v.get("passed"),
            )

        # Risk assessment
        risk = r.get("risk_level", "unknown")
        expl.risk_level = risk
        expl.add_step(
            action="risk_assessment",
            name="risk_level",
            description="Overall risk assessment",
            result=risk,
        )

        # Conclusion
        val = r.get("value")
        unit = r.get("unit", "")
        if val is not None:
            fmt = f"{val:.4g}" if isinstance(val, float) else str(val)
            expl.conclusion = f"{fmt} {unit}".strip()

        return expl

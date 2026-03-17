"""
Constraint engine: evaluates rule conditions against a sensor/state context.
Supports ==, !=, <, <=, >, >=, IN, NOT IN compound (AND/OR/NOT) conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

_SEVERITY_ORDER = {"critical": 3, "high": 3, "warning": 2, "info": 1, "ok": 0}


def _normalize_severity(s: str) -> str:
    """Normalize severity levels (high -> critical, etc.)."""
    s = s.lower()
    if s in ("high", "critical"):
        return "critical"
    if s in ("medium", "warning"):
        return "warning"
    if s in ("low", "info"):
        return "info"
    return s


@dataclass
class RuleResult:
    rule_id: str
    triggered: bool
    matched_conditions: List[str]
    unmatched_conditions: List[str]
    severity: str
    message: str
    context_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "rule_id": self.rule_id,
            "triggered": self.triggered,
            "matched_conditions": self.matched_conditions,
            "unmatched_conditions": self.unmatched_conditions,
            "severity": self.severity,
            "message": self.message,
        }

    def to_natural_language(self, lang: str = "fi") -> str:
        if lang == "fi":
            status = "LAUKAISI" if self.triggered else "ei laukaissut"
            return f"Sääntö '{self.rule_id}' {status}: {self.message}"
        status = "TRIGGERED" if self.triggered else "not triggered"
        return f"Rule '{self.rule_id}' {status}: {self.message}"


@dataclass
class ConstraintResult:
    triggered_rules: List[RuleResult]
    all_results: List[RuleResult]
    highest_severity: str
    summary: str

    def to_dict(self) -> Dict:
        return {
            "triggered_rules": [r.to_dict() for r in self.triggered_rules],
            "all_results": [r.to_dict() for r in self.all_results],
            "highest_severity": self.highest_severity,
            "summary": self.summary,
        }

    def to_natural_language(self, lang: str = "fi") -> str:
        if not self.triggered_rules:
            if lang == "fi":
                return "Kaikki tarkistukset OK — ei aktiivisia varoituksia."
            return "All checks OK — no active warnings."
        return "\n".join(r.to_natural_language(lang) for r in self.triggered_rules)


class ConstraintEngine:
    """Evaluates a list of rules against a context dict."""

    def __init__(self):
        self._rules: List[Dict] = []

    def load_rules(self, rules: List[Dict]):
        """Load rule definitions.

        Rule format::

            {
              "id": "low_battery",
              "conditions": [{"field": "battery_pct", "op": "<", "value": 20}],
              "logic": "AND",       # optional, default AND
              "severity": "warning",
              "message": "Battery low"
            }
        """
        self._rules = list(rules)

    def load_capsule_rules(self, capsule_rules: List[Dict]):
        """Load rules from capsule YAML format (expression-based conditions).

        Capsule rule format::

            {
              "id": "frost_pipe_check",
              "condition": "outdoor_temp < -5 and pipe_insulated == False",
              "severity": "high",
              "message_fi": "...",
              "message_en": "..."
            }
        """
        for rule in capsule_rules:
            converted = {
                "id": rule.get("id", "unknown"),
                "severity": _normalize_severity(rule.get("severity", "info")),
                "message": rule.get("message_fi", rule.get("message_en", "")),
                "message_fi": rule.get("message_fi", ""),
                "message_en": rule.get("message_en", ""),
                # Store expression for eval-based evaluation
                "expression": rule.get("condition", ""),
                "conditions": [],  # empty — uses expression instead
            }
            self._rules.append(converted)

    def evaluate(self, context: Dict[str, Any]) -> ConstraintResult:
        all_results = [self._eval_rule(rule, context) for rule in self._rules]
        triggered = [r for r in all_results if r.triggered]

        highest = "ok"
        for r in triggered:
            if _SEVERITY_ORDER.get(r.severity, 0) > _SEVERITY_ORDER.get(highest, 0):
                highest = r.severity

        summary = (
            f"{len(triggered)} rule(s) triggered, highest severity: {highest}"
            if triggered
            else "No rules triggered"
        )

        return ConstraintResult(
            triggered_rules=triggered,
            all_results=all_results,
            highest_severity=highest,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def evaluate_with_lang(self, context: Dict[str, Any],
                           lang: str = "fi") -> ConstraintResult:
        """Evaluate and use language-specific messages."""
        result = self.evaluate(context)
        msg_key = f"message_{lang}"
        for r in result.all_results:
            # Find original rule and use localized message
            for rule in self._rules:
                if rule.get("id") == r.rule_id and msg_key in rule:
                    if r.triggered:
                        r.message = rule[msg_key]
                    break
        return result

    def _eval_rule(self, rule: Dict, context: Dict[str, Any]) -> RuleResult:
        rule_id = rule.get("id", "unknown")
        conditions = rule.get("conditions", [])
        logic = rule.get("logic", "AND").upper()
        severity = rule.get("severity", "info")
        message = rule.get("message", "")

        # Expression-based evaluation (capsule rules)
        expression = rule.get("expression", "")
        if expression and not conditions:
            try:
                triggered = bool(eval(  # noqa: S307
                    expression, {"__builtins__": {}},
                    {**context, "True": True, "False": False}))
            except Exception:
                triggered = False
            return RuleResult(
                rule_id=rule_id,
                triggered=triggered,
                matched_conditions=[expression] if triggered else [],
                unmatched_conditions=[] if triggered else [expression],
                severity=severity if triggered else "ok",
                message=message if triggered else "",
                context_snapshot=dict(context),
            )

        matched: List[str] = []
        unmatched: List[str] = []

        for cond in conditions:
            desc, result = self._eval_condition(cond, context)
            (matched if result else unmatched).append(desc)

        if logic == "OR":
            triggered = len(matched) > 0
        else:  # AND (default)
            triggered = len(conditions) > 0 and len(unmatched) == 0

        return RuleResult(
            rule_id=rule_id,
            triggered=triggered,
            matched_conditions=matched,
            unmatched_conditions=unmatched,
            severity=severity if triggered else "ok",
            message=message if triggered else "",
            context_snapshot=dict(context),
        )

    def _eval_condition(self, cond: Any, context: Dict[str, Any]) -> Tuple[str, bool]:
        if not isinstance(cond, dict):
            return (str(cond), False)

        # NOT wrapper
        if "NOT" in cond:
            desc, result = self._eval_condition(cond["NOT"], context)
            return (f"NOT ({desc})", not result)

        # AND / OR compound
        for logic_key in ("AND", "OR"):
            if logic_key in cond:
                sub = [self._eval_condition(c, context) for c in cond[logic_key]]
                descs = [d for d, _ in sub]
                bools = [r for _, r in sub]
                combined = all(bools) if logic_key == "AND" else any(bools)
                return (f"{logic_key}({', '.join(descs)})", combined)

        # Simple comparison
        fld = cond.get("field")
        op = cond.get("op", "==")
        val = cond.get("value")

        if fld is None:
            return ("(invalid condition)", False)

        ctx_val = context.get(fld)
        if ctx_val is None:
            return (f"{fld} {op} {val}", False)

        return (f"{fld} {op} {val}", self._compare(ctx_val, op, val))

    @staticmethod
    def _compare(a: Any, op: str, b: Any) -> bool:
        try:
            if op == "==":      return a == b
            if op == "!=":      return a != b
            if op == "<":       return a < b
            if op == "<=":      return a <= b
            if op == ">":       return a > b
            if op == ">=":      return a >= b
            if op == "IN":      return a in b
            if op == "NOT IN":  return a not in b
        except Exception:
            pass
        return False

"""Adapter wrapping legacy core.constraint_engine as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_ConstraintEngine = None


def _get_engine():
    global _ConstraintEngine
    if _ConstraintEngine is None:
        try:
            from core.constraint_engine import ConstraintEngine
            _ConstraintEngine = ConstraintEngine
        except ImportError:
            pass
    return _ConstraintEngine


class ConstraintEngineAdapter:
    """Capability adapter for the legacy ConstraintEngine.

    Wraps rule evaluation against a context dictionary, returning
    triggered rules with severity and messages.
    """

    CAPABILITY_ID = "solve.constraints"

    def __init__(self, engine=None):
        cls = _get_engine()
        self._engine = engine or (cls() if cls else None)
        self._call_count = 0
        self._triggered_count = 0

    @property
    def available(self) -> bool:
        return self._engine is not None

    def load_rules(self, rules: List[Dict]) -> None:
        """Load rules into the engine."""
        if self._engine:
            self._engine.load_rules(rules)

    def load_capsule_rules(self, capsule_rules: List[Dict]) -> None:
        """Load capsule-format rules."""
        if self._engine:
            self._engine.load_capsule_rules(capsule_rules)

    def execute(self, context: Dict[str, Any],
                lang: str = "fi") -> Dict[str, Any]:
        """Evaluate all loaded rules against context."""
        t0 = time.monotonic()
        self._call_count += 1
        if not self._engine:
            return {"success": False, "error": "ConstraintEngine not available"}

        try:
            result = self._engine.evaluate_with_lang(context, lang=lang)
            elapsed = (time.monotonic() - t0) * 1000

            triggered = result.triggered_rules if hasattr(result, "triggered_rules") else []
            if triggered:
                self._triggered_count += 1

            rules_data = []
            for r in (result.all_results if hasattr(result, "all_results") else []):
                rules_data.append({
                    "rule_id": r.rule_id,
                    "triggered": r.triggered,
                    "severity": r.severity,
                    "message": r.message,
                })

            return {
                "success": True,
                "triggered_count": len(triggered),
                "highest_severity": (result.highest_severity
                                     if hasattr(result, "highest_severity") else ""),
                "summary": result.summary if hasattr(result, "summary") else "",
                "rules": rules_data,
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "gold",
                "latency_ms": round(elapsed, 2),
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "capability_id": self.CAPABILITY_ID,
                "latency_ms": round((time.monotonic() - t0) * 1000, 2),
            }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "triggered": self._triggered_count,
        }

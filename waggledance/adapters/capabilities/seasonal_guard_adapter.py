"""Adapter wrapping legacy core.seasonal_guard.SeasonalGuard as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SeasonalGuard = None


def _get_class():
    global _SeasonalGuard
    if _SeasonalGuard is None:
        try:
            from core.seasonal_guard import SeasonalGuard
            _SeasonalGuard = SeasonalGuard
        except ImportError:
            _SeasonalGuard = None
    return _SeasonalGuard


class SeasonalGuardAdapter:
    """Capability adapter for seasonal rule checking.

    Wraps SeasonalGuard.check() to detect seasonal violations
    in beekeeping/cottage/energy advice through the autonomy
    capability interface. Deterministic, <0.1ms per check.
    """

    CAPABILITY_ID = "detect.seasonal_rules"

    def __init__(self, guard=None, month: Optional[int] = None):
        cls = _get_class()
        if guard:
            self._guard = guard
        elif cls:
            self._guard = cls(month=month)
        else:
            self._guard = None
        self._call_count = 0
        self._violation_count = 0

    @property
    def available(self) -> bool:
        return self._guard is not None

    def execute(self, text: str = "", **kwargs) -> Dict[str, Any]:
        """Check text for seasonal rule violations.

        Args:
            text: Finnish or English text to check
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._guard:
            return {"success": False, "error": "SeasonalGuard not available",
                    "capability_id": self.CAPABILITY_ID}

        violations = self._guard.check(text)
        elapsed = (time.monotonic() - t0) * 1000

        has_violations = len(violations) > 0
        if has_violations:
            self._violation_count += 1

        return {
            "success": True,
            "has_violations": has_violations,
            "violations": [v.to_dict() for v in violations],
            "violation_count": len(violations),
            "current_month": self._guard.current_month,
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
            "latency_ms": round(elapsed, 2),
        }

    def stats(self) -> Dict[str, Any]:
        guard_info = {}
        if self._guard:
            guard_info = {
                "rule_count": self._guard.rule_count,
                "current_month": self._guard.current_month,
            }
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "violations_found": self._violation_count,
            "guard": guard_info,
        }

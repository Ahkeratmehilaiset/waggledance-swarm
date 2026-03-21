"""Adapter wrapping legacy core.smart_router_v2.SmartRouterV2 as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SmartRouterV2 = None


def _get_class():
    global _SmartRouterV2
    if _SmartRouterV2 is None:
        try:
            from core.smart_router_v2 import SmartRouterV2
            _SmartRouterV2 = SmartRouterV2
        except ImportError:
            _SmartRouterV2 = None
    return _SmartRouterV2


class IntentClassifierAdapter:
    """Capability adapter for SmartRouterV2 intent classification.

    Wraps SmartRouterV2.route() to expose keyword-based intent
    classification through the autonomy capability interface.
    """

    CAPABILITY_ID = "sense.intent_classify"

    def __init__(self, smart_router=None):
        self._router = smart_router
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return self._router is not None or _get_class() is not None

    def execute(self, query: str = "", **kwargs) -> Dict[str, Any]:
        """Classify the intent of a query.

        Args:
            query: user query text to classify
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._router:
            # Fall back to SolverRouter's static classify_intent
            try:
                from waggledance.core.reasoning.solver_router import SolverRouter
                intent = SolverRouter.classify_intent(query)
                elapsed = (time.monotonic() - t0) * 1000
                self._success_count += 1
                return {
                    "success": True,
                    "intent": intent,
                    "source": "solver_router",
                    "capability_id": self.CAPABILITY_ID,
                    "quality_path": "silver",
                    "latency_ms": round(elapsed, 2),
                }
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                return {"success": False, "error": str(exc),
                        "capability_id": self.CAPABILITY_ID,
                        "latency_ms": round(elapsed, 2)}

        route_result = self._router.route(query)
        elapsed = (time.monotonic() - t0) * 1000
        self._success_count += 1

        return {
            "success": True,
            "intent": route_result.layer,
            "confidence": route_result.confidence,
            "reason": route_result.reason,
            "decision_id": route_result.decision_id,
            "source": "smart_router_v2",
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
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
            "legacy_router_available": self._router is not None,
        }

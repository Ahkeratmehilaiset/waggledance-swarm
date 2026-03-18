"""Adapter wrapping RouteEngine as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from waggledance.core.reasoning.route_engine import RouteEngine

logger = logging.getLogger(__name__)


class RouteAnalyzerAdapter:
    """Capability adapter for the RouteEngine reasoning engine."""

    CAPABILITY_ID = "analyze.routing"

    def __init__(self):
        self._engine = RouteEngine()
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return True

    def execute(self, query_type: str = "metrics") -> Dict[str, Any]:
        """Execute route analysis.

        Args:
            query_type: metrics, recommendations, or accuracy
        """
        t0 = time.monotonic()
        self._call_count += 1

        if query_type == "recommendations":
            recs = self._engine.recommend_improvements()
            result = {"recommendations": recs}
        elif query_type == "accuracy":
            result = {
                "overall_accuracy": self._engine.get_route_accuracy(),
                "llm_fallback_rate": self._engine.get_llm_fallback_rate(),
                "quality_distribution": self._engine.get_quality_distribution(),
            }
        else:
            metrics = self._engine.get_route_metrics()
            result = {"metrics": [m.to_dict() for m in metrics]}

        elapsed = (time.monotonic() - t0) * 1000
        self._success_count += 1

        return {
            "success": True,
            "result": result,
            "query_type": query_type,
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
            "latency_ms": round(elapsed, 2),
        }

    @property
    def engine(self) -> RouteEngine:
        """Expose engine for pre-loading data in tests or production."""
        return self._engine

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "successes": self._success_count,
            "success_rate": (self._success_count / self._call_count
                             if self._call_count else 0.0),
        }

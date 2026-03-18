"""Adapter wrapping StatsEngine as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from waggledance.core.reasoning.stats_engine import StatsEngine

logger = logging.getLogger(__name__)


class StatsEngineAdapter:
    """Capability adapter for the StatsEngine reasoning engine."""

    CAPABILITY_ID = "solve.stats"

    def __init__(self):
        self._engine = StatsEngine()
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return True

    def execute(self, metric: str = "", operation: str = "summarize",
                **kwargs) -> Dict[str, Any]:
        """Execute statistical analysis.

        Args:
            metric: metric name to analyze
            operation: one of summarize, compare, correlation
            **kwargs: extra params (metric_b for correlation, window sizes for compare)
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not metric:
            metric = "default"

        if operation == "compare":
            result = self._engine.compare(
                metric,
                window_a=kwargs.get("window_a", 50),
                window_b=kwargs.get("window_b", 50),
            )
        elif operation == "correlation":
            metric_b = kwargs.get("metric_b", "")
            corr = self._engine.correlation(metric, metric_b)
            result = {
                "metric_a": metric,
                "metric_b": metric_b,
                "correlation": round(corr, 4) if corr is not None else None,
            }
        else:
            summary = self._engine.summarize(metric)
            result = summary.to_dict()

        elapsed = (time.monotonic() - t0) * 1000
        self._success_count += 1

        return {
            "success": True,
            "result": result,
            "operation": operation,
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
            "latency_ms": round(elapsed, 2),
        }

    @property
    def engine(self) -> StatsEngine:
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

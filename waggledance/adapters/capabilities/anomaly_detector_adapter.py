"""Adapter wrapping AnomalyEngine as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.reasoning.anomaly_engine import AnomalyEngine

logger = logging.getLogger(__name__)


class AnomalyDetectorAdapter:
    """Capability adapter for the AnomalyEngine reasoning engine."""

    CAPABILITY_ID = "detect.anomaly"

    def __init__(self):
        self._engine = AnomalyEngine()
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return True

    def execute(self, metric: str = "", current: float = 0.0,
                baseline: Optional[float] = None) -> Dict[str, Any]:
        """Run all anomaly detection methods on a metric value.

        Args:
            metric: name of the metric being checked
            current: current observed value
            baseline: optional known baseline for residual check
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not metric:
            metric = "unknown"

        results = self._engine.check_all(metric, current, baseline)
        elapsed = (time.monotonic() - t0) * 1000

        any_anomaly = any(r.is_anomaly for r in results)
        max_severity = max((r.severity for r in results), default=0.0)

        self._success_count += 1
        return {
            "success": True,
            "is_anomaly": any_anomaly,
            "max_severity": round(max_severity, 4),
            "checks": [r.to_dict() for r in results],
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
        }

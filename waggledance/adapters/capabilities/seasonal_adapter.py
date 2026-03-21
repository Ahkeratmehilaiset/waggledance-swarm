"""Capability adapter for the SeasonalEngine (cottage/apiary profiles)."""

from __future__ import annotations

import time
from typing import Any, Dict


class SeasonalAdapter:
    """Wraps SeasonalEngine as a capability executor.

    Only available for cottage/apiary profiles where the seasonal YAML
    config is present.
    """

    CAPABILITY_ID = "sense.seasonal"

    def __init__(self):
        self._engine = None
        self._available = False
        self._call_count = 0
        try:
            from waggledance.core.reasoning.seasonal_engine import SeasonalEngine
            engine = SeasonalEngine()
            if engine._data is not None:
                self._engine = engine
                self._available = True
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self._available

    def execute(self, query: str = "", month: int = None, **kwargs) -> Dict[str, Any]:
        """Execute seasonal query.

        Returns seasonal recommendations for the given month.
        """
        t0 = time.time()
        self._call_count += 1

        if not self._available:
            return {
                "success": False,
                "error": "Seasonal engine not available (missing YAML config)",
                "capability_id": self.CAPABILITY_ID,
            }

        result = self._engine.get_recommendations(month=month)
        elapsed = round((time.time() - t0) * 1000, 2)

        return {
            "success": True,
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
            "latency_ms": elapsed,
            "result": result,
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self._available,
            "call_count": self._call_count,
            "engine_stats": self._engine.stats() if self._engine else {},
        }

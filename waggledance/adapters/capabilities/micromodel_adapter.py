"""Adapter wrapping legacy core.micro_model V1/V2 as capabilities."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MicroModel = None


def _get_micromodel():
    global _MicroModel
    if _MicroModel is None:
        try:
            from core.micro_model import MicroModel
            _MicroModel = MicroModel
        except ImportError:
            pass
    return _MicroModel


class MicroModelAdapter:
    """Capability adapter for the legacy MicroModel (V1 pattern match + V2 classifier).

    V1 (solve.pattern_match): pattern database lookup
    V2 (solve.neural_classifier): PyTorch 768→256→128→N classifier
    """

    CAPABILITY_ID = "solve.pattern_match"       # default binding
    CAPABILITY_ID_V1 = "solve.pattern_match"
    CAPABILITY_ID_V2 = "solve.neural_classifier"

    def __init__(self, micromodel=None):
        cls = _get_micromodel()
        self._model = micromodel or (cls() if cls else None)
        self._v1_calls = 0
        self._v2_calls = 0
        self._v1_hits = 0
        self._v2_hits = 0

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def v2_available(self) -> bool:
        if not self._model:
            return False
        return getattr(self._model, "v2_ready", False)

    def execute(self, query: str = "", **kwargs) -> Dict[str, Any]:
        """Standard capability executor — delegates to V1 pattern matching."""
        return self.execute_v1(query, threshold=kwargs.get("threshold", 0.85))

    def execute_v1(self, query: str, threshold: float = 0.85) -> Dict[str, Any]:
        """Execute V1 pattern matching."""
        t0 = time.monotonic()
        self._v1_calls += 1
        if not self._model:
            return {"success": False, "error": "MicroModel not available"}

        try:
            result = None
            if hasattr(self._model, "lookup"):
                result = self._model.lookup(query)
            elif hasattr(self._model, "match"):
                result = self._model.match(query)
            elapsed = (time.monotonic() - t0) * 1000

            if result and isinstance(result, dict):
                confidence = result.get("confidence", 0)
                if confidence >= threshold:
                    self._v1_hits += 1
                    return {
                        "success": True,
                        "answer": result.get("answer", ""),
                        "confidence": confidence,
                        "capability_id": self.CAPABILITY_ID_V1,
                        "quality_path": "silver",
                        "latency_ms": round(elapsed, 2),
                    }
            return {
                "success": False,
                "error": "No pattern match above threshold",
                "capability_id": self.CAPABILITY_ID_V1,
                "latency_ms": round(elapsed, 2),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def execute_v2(self, query: str, embedding: list = None) -> Dict[str, Any]:
        """Execute V2 neural classification."""
        t0 = time.monotonic()
        self._v2_calls += 1
        if not self._model or not self.v2_available:
            return {"success": False, "error": "MicroModel V2 not available"}

        try:
            result = None
            if hasattr(self._model, "classify"):
                result = self._model.classify(query, embedding=embedding)
            elapsed = (time.monotonic() - t0) * 1000

            if result and isinstance(result, dict):
                self._v2_hits += 1
                return {
                    "success": True,
                    "classification": result.get("class", ""),
                    "confidence": result.get("confidence", 0),
                    "capability_id": self.CAPABILITY_ID_V2,
                    "quality_path": "silver",
                    "latency_ms": round(elapsed, 2),
                }
            return {"success": False, "error": "V2 classification failed",
                    "latency_ms": round(elapsed, 2)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def stats(self) -> Dict[str, Any]:
        return {
            "v1_available": self.available,
            "v2_available": self.v2_available,
            "v1_calls": self._v1_calls,
            "v1_hits": self._v1_hits,
            "v2_calls": self._v2_calls,
            "v2_hits": self._v2_hits,
        }

"""Adapter wrapping legacy core.hallucination_checker.HallucinationChecker as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HallucinationChecker = None


def _get_class():
    global _HallucinationChecker
    if _HallucinationChecker is None:
        try:
            from core.hallucination_checker import HallucinationChecker
            _HallucinationChecker = HallucinationChecker
        except ImportError:
            _HallucinationChecker = None
    return _HallucinationChecker


class HallucinationCheckerAdapter:
    """Capability adapter for hallucination detection.

    Wraps HallucinationChecker.check() through the autonomy capability interface.
    Uses embedding similarity + keyword overlap to flag suspicious LLM answers.
    """

    CAPABILITY_ID = "verify.hallucination"

    def __init__(self, checker=None):
        cls = _get_class()
        self._checker = checker or (cls() if cls else None)
        self._call_count = 0
        self._suspicious_count = 0

    @property
    def available(self) -> bool:
        return self._checker is not None

    def execute(self, question: str = "", answer: str = "",
                q_vec: Optional[List[float]] = None,
                a_vec: Optional[List[float]] = None) -> Dict[str, Any]:
        """Check if an LLM answer is potentially hallucinated.

        Args:
            question: the original question text
            answer: the LLM-generated answer text
            q_vec: optional question embedding
            a_vec: optional answer embedding
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._checker:
            return {"success": False, "error": "HallucinationChecker not available",
                    "capability_id": self.CAPABILITY_ID}

        result = self._checker.check(question, answer, q_vec, a_vec)
        elapsed = (time.monotonic() - t0) * 1000

        if result.is_suspicious:
            self._suspicious_count += 1

        return {
            "success": True,
            "passed": not result.is_suspicious,
            "is_suspicious": result.is_suspicious,
            "relevance": round(result.relevance, 4),
            "keyword_overlap": round(result.keyword_overlap, 4),
            "source_grounding": round(getattr(result, 'source_grounding', 1.0), 4),
            "self_consistency": round(getattr(result, 'self_consistency', 1.0), 4),
            "corrections_penalty": round(getattr(result, 'corrections_penalty', 1.0), 4),
            "combined_score": round(getattr(result, 'combined_score', 0.0), 4),
            "reason": result.reason,
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
            "latency_ms": round(elapsed, 2),
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "suspicious_count": self._suspicious_count,
            "suspicious_rate": (self._suspicious_count / self._call_count
                                if self._call_count else 0.0),
        }

"""Adapter wrapping legacy core.normalizer.normalize_fi as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_normalize_fi = None


def _get_fn():
    global _normalize_fi
    if _normalize_fi is None:
        try:
            from core.normalizer import normalize_fi
            _normalize_fi = normalize_fi
        except ImportError:
            _normalize_fi = None
    return _normalize_fi


class FinnishNormalizerAdapter:
    """Capability adapter for Finnish text normalization (Voikko-powered).

    Wraps normalize_fi() which performs Finnish lemmatization,
    compound word expansion, and stopword removal.
    """

    CAPABILITY_ID = "normalize.finnish"

    def __init__(self):
        self._fn = _get_fn()
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return self._fn is not None

    def execute(self, text: str = "", sort_words: bool = False,
                **kwargs) -> Dict[str, Any]:
        """Normalize Finnish text.

        Args:
            text: Finnish text to normalize
            sort_words: if True, sort lemmas alphabetically (for cache keys)
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._fn:
            return {"success": False, "error": "Finnish normalizer not available",
                    "capability_id": self.CAPABILITY_ID}

        normalized = self._fn(text, sort_words=sort_words)
        elapsed = (time.monotonic() - t0) * 1000

        self._success_count += 1
        return {
            "success": True,
            "original": text,
            "normalized": normalized,
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

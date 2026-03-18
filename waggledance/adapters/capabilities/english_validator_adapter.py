"""Adapter wrapping legacy core.en_validator.ENValidator as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_ENValidator = None


def _get_class():
    global _ENValidator
    if _ENValidator is None:
        try:
            from core.en_validator import ENValidator
            _ENValidator = ENValidator
        except ImportError:
            _ENValidator = None
    return _ENValidator


class EnglishValidatorAdapter:
    """Capability adapter for English output quality validation.

    Wraps ENValidator.validate() to correct domain terminology
    and validate English LLM output quality.
    """

    CAPABILITY_ID = "verify.english_output"

    def __init__(self, validator=None):
        cls = _get_class()
        self._validator = validator or (cls() if cls else None)
        self._call_count = 0
        self._correction_count = 0

    @property
    def available(self) -> bool:
        return self._validator is not None

    def execute(self, text: str = "", **kwargs) -> Dict[str, Any]:
        """Validate and correct English text.

        Args:
            text: English text to validate and correct
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._validator:
            return {"success": False, "error": "ENValidator not available",
                    "capability_id": self.CAPABILITY_ID}

        result = self._validator.validate(text)
        elapsed = (time.monotonic() - t0) * 1000

        if result.was_corrected:
            self._correction_count += 1

        return {
            "success": True,
            "passed": not result.was_corrected,
            "original": result.original,
            "corrected": result.corrected,
            "corrections": result.corrections,
            "correction_count": result.correction_count,
            "method": result.method,
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
            "latency_ms": round(elapsed, 2),
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "corrections": self._correction_count,
            "correction_rate": (self._correction_count / self._call_count
                                if self._call_count else 0.0),
        }

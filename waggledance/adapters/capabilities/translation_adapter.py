"""Adapter wrapping legacy core.translation_proxy.TranslationProxy as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_TranslationProxy = None


def _get_class():
    global _TranslationProxy
    if _TranslationProxy is None:
        try:
            from core.translation_proxy import TranslationProxy
            _TranslationProxy = TranslationProxy
        except ImportError:
            _TranslationProxy = None
    return _TranslationProxy


class TranslationAdapter:
    """Capability adapter for Finnish ↔ English translation.

    Wraps TranslationProxy (Voikko + domain dictionary + Opus-MT fallback)
    through the autonomy capability interface.
    """

    CAPABILITY_ID = "normalize.translate_fi_en"

    def __init__(self, proxy=None):
        cls = _get_class()
        self._proxy = proxy or (cls() if cls else None)
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return self._proxy is not None

    def execute(self, text: str = "", direction: str = "fi_to_en",
                **kwargs) -> Dict[str, Any]:
        """Translate text between Finnish and English.

        Args:
            text: text to translate
            direction: "fi_to_en" or "en_to_fi"
        """
        t0 = time.monotonic()
        self._call_count += 1

        if not self._proxy:
            return {"success": False, "error": "TranslationProxy not available",
                    "capability_id": self.CAPABILITY_ID}

        try:
            if direction == "en_to_fi":
                translated = self._proxy.en_to_fi(text)
            else:
                translated = self._proxy.fi_to_en(text)
            elapsed = (time.monotonic() - t0) * 1000

            self._success_count += 1
            return {
                "success": True,
                "original": text,
                "translated": translated,
                "direction": direction,
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "gold",
                "latency_ms": round(elapsed, 2),
            }
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return {
                "success": False,
                "error": str(exc),
                "capability_id": self.CAPABILITY_ID,
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

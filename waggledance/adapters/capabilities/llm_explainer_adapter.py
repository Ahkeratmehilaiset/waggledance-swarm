"""Adapter wrapping LLM (Ollama) as the explain.llm_reasoning capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LLMExplainerAdapter:
    """Capability adapter for LLM-based explanations.

    Uses the existing LLMPort (OllamaAdapter or StubLLMAdapter)
    for natural language generation, explanations, and reports.
    Quality path is always BRONZE — LLM is not authoritative.
    """

    CAPABILITY_ID = "explain.llm_reasoning"

    def __init__(self, llm_port=None):
        self._llm = llm_port
        self._call_count = 0
        self._success_count = 0
        self._total_latency_ms = 0.0

    @property
    def available(self) -> bool:
        return self._llm is not None

    async def execute(self, prompt: str, model: str = "default",
                      temperature: float = 0.7,
                      max_tokens: int = 500) -> Dict[str, Any]:
        """Generate LLM response. Always bronze quality path."""
        t0 = time.monotonic()
        self._call_count += 1
        if not self._llm:
            return {"success": False, "error": "LLM port not available",
                    "quality_path": "bronze"}

        try:
            result = await self._llm.generate(
                prompt=prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            elapsed = (time.monotonic() - t0) * 1000
            self._total_latency_ms += elapsed

            if result:
                self._success_count += 1
                return {
                    "success": True,
                    "response": result,
                    "capability_id": self.CAPABILITY_ID,
                    "quality_path": "bronze",
                    "latency_ms": round(elapsed, 2),
                }
            return {
                "success": False,
                "error": "Empty LLM response",
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "bronze",
                "latency_ms": round(elapsed, 2),
            }
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return {
                "success": False,
                "error": str(exc),
                "capability_id": self.CAPABILITY_ID,
                "quality_path": "bronze",
                "latency_ms": round(elapsed, 2),
            }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "successes": self._success_count,
            "avg_latency_ms": round(
                self._total_latency_ms / self._call_count
                if self._call_count else 0, 2
            ),
        }

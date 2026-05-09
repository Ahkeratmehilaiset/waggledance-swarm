# SPDX-License-Identifier: Apache-2.0
"""Local-ollama provider — Tier 2 of the fallback chain.

Stage 1 implementation deliberately lazy-imports the ``ollama`` Python
package inside ``call()`` so Profile S (no LLM allowed) does not pay
the import cost. ``is_available()`` checks for the package without
importing it via ``importlib.util.find_spec``.
"""
from __future__ import annotations

import importlib.util
import time

from ..types import LLMRequest, LLMResponse, FallbackLevel
from .base import ProviderError, ProviderPlugin


class OllamaProvider(ProviderPlugin):
    name = "local-ollama"
    fallback_level = FallbackLevel.LOCAL_LLM

    def __init__(self, model: str = "llama3.2:3b-instruct-q4_K_M",
                  host: str = "http://localhost:11434"):
        self._model = model
        self._host = host

    def is_available(self) -> bool:
        # find_spec does NOT import the module — Profile S stays clean.
        return importlib.util.find_spec("ollama") is not None

    def call(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            raise ProviderError(
                "ollama package not installed; falling through to next tier"
            )
        # Lazy import so Profile S doesn't pay the cost.
        import ollama  # type: ignore[import-not-found]

        start = time.perf_counter()
        try:
            client = ollama.Client(host=self._host)
            result = client.chat(
                model=self._model,
                messages=[{"role": "user", "content": request.prompt}],
                options={
                    # Honor latency budget loosely; ollama doesn't expose
                    # a hard timeout per call, but we cap retries.
                    "num_predict": 256,
                },
            )
            text = (result.get("message") or {}).get("content", "")
            tokens_in = int(result.get("prompt_eval_count", 0))
            tokens_out = int(result.get("eval_count", 0))
            latency_ms = (time.perf_counter() - start) * 1000
            return LLMResponse(
                text=text,
                fallback_level=FallbackLevel.LOCAL_LLM,
                provider=self.name,
                success=True,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception as exc:  # ollama not running, model missing, etc.
            raise ProviderError(f"ollama call failed: {exc}") from exc

# SPDX-License-Identifier: BUSL-1.1
"""LLMPort adapter routing through BridgeLLMClient's 4-tier fallback chain.

Audit H49 / operator decision D3.4: container.llm used to return
OllamaAdapter directly, bypassing the entire BridgeLLMClient stack
(cache -> local-ollama -> cloud -> heuristic) along with its
operator-decision-4 PII redactor. This adapter restores the chain:
- LLMPort.generate() now routes via BridgeLLMClient.run()
- Cloud calls (Tier 3) fire through BridgeLLMRedactor first
- Profile S short-circuits to heuristic (Tier 4) — no cloud reach
- The original OllamaAdapter is kept as a hard-fallback for when
  BridgeLLMClient itself fails to construct or run

The orchestrator does not need to change: it still depends on
LLMPort. The four-tier behavior becomes the implicit default for
all `self._llm.generate(...)` calls.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from waggledance.core.bridge_llm.types import LLMRequest

log = logging.getLogger(__name__)


class BridgeLLMAdapter:
    """LLMPort wrapper that delegates to BridgeLLMClient + falls back.

    Construction takes a BridgeLLMClient instance and a sync/async
    fallback adapter implementing LLMPort. The adapter is used when
    BridgeLLMClient is disabled (Profile S short-circuited at a
    higher level, OR a runtime exception in client.run()).
    """

    def __init__(
        self,
        client: Any,  # BridgeLLMClient — duck-typed to avoid Profile S import side-effects
        fallback_adapter: Any,
        injection_point: str = "orchestrator.generate",
    ) -> None:
        self._client = client
        self._fallback = fallback_adapter
        self._injection_point = injection_point

    async def generate(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """Generate via the bridge LLM chain, fall back to direct on failure."""
        # Profile S / explicit disable — never even attempt the chain;
        # use the fallback adapter directly so the heuristic provider
        # doesn't shadow real local Ollama in dev environments.
        if not self._is_client_enabled():
            return await self._fallback.generate(
                prompt=prompt, model=model,
                temperature=temperature, max_tokens=max_tokens,
            )

        request = LLMRequest(
            injection_point=self._injection_point,
            prompt=prompt,
            metadata={
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        try:
            # BridgeLLMClient.run is sync (it manages its own provider
            # plugins, some of which may block). Wrap in to_thread so
            # the asyncio event loop stays free.
            response = await asyncio.to_thread(self._client.run, request)
            text = (response.text or "").strip()
            if text:
                return text
            log.debug(
                "BridgeLLMClient returned empty text "
                "(fallback_level=%s, provider=%s); using direct adapter",
                getattr(response, "fallback_level", None),
                getattr(response, "provider", None),
            )
        except Exception as exc:  # noqa: BLE001 — fail-safe
            log.warning(
                "BridgeLLMClient.run raised; falling back to direct adapter: %s",
                exc,
            )
        return await self._fallback.generate(
            prompt=prompt, model=model,
            temperature=temperature, max_tokens=max_tokens,
        )

    async def is_available(self) -> bool:
        """Available if either the bridge OR the fallback adapter is.

        We don't probe the bridge providers directly (cache is always
        available, heuristic is always available, so the bridge would
        report True regardless of real LLM presence). Defer to the
        fallback adapter's existing readiness check.
        """
        if hasattr(self._fallback, "is_available"):
            try:
                return await self._fallback.is_available()
            except Exception:  # noqa: BLE001
                return False
        return True

    async def close(self) -> None:
        """Close the underlying fallback adapter (BridgeLLMClient has no close)."""
        if hasattr(self._fallback, "close"):
            try:
                await self._fallback.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("Fallback adapter close failed: %s", exc)

    def _is_client_enabled(self) -> bool:
        """Mirror BridgeLLMClient.is_enabled() with defensive handling."""
        try:
            return bool(self._client.is_enabled())
        except Exception:  # noqa: BLE001
            return False

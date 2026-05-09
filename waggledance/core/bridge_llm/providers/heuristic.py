# SPDX-License-Identifier: Apache-2.0
"""Pure-heuristic fallback — Tier 4 of the fallback chain.

This is the floor. It must work offline, with no LLM library, on any
profile. Returns a deterministic non-LLM response derived from the
request's injection_point and intent.

A caller that wants a smarter heuristic may register their own
provider with name='heuristic' to replace this one.
"""
from __future__ import annotations

import time

from ..types import LLMRequest, LLMResponse, FallbackLevel
from .base import ProviderPlugin


class HeuristicProvider(ProviderPlugin):
    name = "heuristic"
    fallback_level = FallbackLevel.HEURISTIC

    def is_available(self) -> bool:
        return True

    def call(self, request: LLMRequest) -> LLMResponse:
        start = time.perf_counter()
        # Deterministic placeholder text. Real callers can register a
        # site-specific heuristic for their injection_point.
        text = (
            f"[heuristic-fallback] "
            f"injection_point={request.injection_point} "
            f"intent={request.intent or '<none>'}"
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return LLMResponse(
            text=text,
            fallback_level=FallbackLevel.HEURISTIC,
            provider=self.name,
            success=True,
            latency_ms=latency_ms,
        )

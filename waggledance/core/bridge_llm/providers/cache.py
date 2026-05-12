# SPDX-License-Identifier: Apache-2.0
"""Exact-match cache provider — Tier 1 of the fallback chain."""
from __future__ import annotations

import hashlib
import json
import threading
import time

from ..types import LLMRequest, LLMResponse, FallbackLevel
from .base import ProviderError, ProviderPlugin


class ExactCacheProvider(ProviderPlugin):
    """In-memory exact-prompt cache.

    Keyed by sha256(injection_point + '\\n' + prompt). Returns hits as
    ``FallbackLevel.CACHE`` so the client knows it didn't pay LLM cost.
    Stage 1 keeps the cache process-local; Stage 2 will move it to
    semantic-similarity matching backed by an embedding store.
    """

    name = "cache"
    fallback_level = FallbackLevel.CACHE

    def __init__(self, max_entries: int = 1024):
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}
        self._max_entries = max_entries

    @staticmethod
    def _key(request: LLMRequest) -> str:
        blob = json.dumps(
            {
                "injection_point": request.injection_point,
                "prompt": request.prompt,
                "model": request.model,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "metadata_model": request.metadata.get("model"),
                "metadata_temperature": request.metadata.get("temperature"),
                "metadata_max_tokens": request.metadata.get("max_tokens"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def store(self, request: LLMRequest, text: str) -> None:
        """Manually seed an entry — useful for tests + warm-up."""
        with self._lock:
            if len(self._entries) >= self._max_entries:
                # Drop the oldest entry; insertion order preserves it.
                self._entries.pop(next(iter(self._entries)))
            self._entries[self._key(request)] = text

    def is_available(self) -> bool:
        return True

    def call(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            text = self._entries.get(self._key(request))
        if text is None:
            raise ProviderError("cache miss")
        return LLMResponse(
            text=text,
            fallback_level=FallbackLevel.CACHE,
            provider=self.name,
            success=True,
            latency_ms=0.0,
            cached=True,
            call_id=self._key(request)[:16],
        )

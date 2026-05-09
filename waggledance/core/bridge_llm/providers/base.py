# SPDX-License-Identifier: Apache-2.0
"""ProviderPlugin abstract base + global registry."""
from __future__ import annotations

import abc
from typing import Optional

from ..types import LLMRequest, LLMResponse, FallbackLevel


class ProviderError(Exception):
    """Raised by a provider plugin when the call cannot be served."""


class ProviderPlugin(abc.ABC):
    """Abstract base class for BridgeLLMClient provider plugins.

    Implementations must be safe to instantiate even when the backing
    dependency (Ollama, Anthropic SDK, etc.) is not installed; the
    constructor should not import the heavy dependency. Lazy-import
    inside ``call()`` only when actually needed, and raise
    ``ProviderError`` if the dependency is missing — the client will
    then fall through to the next tier.
    """

    name: str = "base"
    fallback_level: FallbackLevel = FallbackLevel.HEURISTIC

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider can serve calls right now.

        Used by BridgeLLMClient to skip a tier without paying the
        construction or import cost. Implementations should be cheap
        and side-effect-free.
        """

    @abc.abstractmethod
    def call(self, request: LLMRequest) -> LLMResponse:
        """Serve one request. Must raise ProviderError to fall through."""


# ── Global registry ──────────────────────────────────────────────

_REGISTRY: dict[str, ProviderPlugin] = {}


def register_provider(provider: ProviderPlugin) -> None:
    """Register a plugin globally so BridgeLLMClient can discover it."""
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> Optional[ProviderPlugin]:
    return _REGISTRY.get(name)


def all_providers() -> dict[str, ProviderPlugin]:
    return dict(_REGISTRY)

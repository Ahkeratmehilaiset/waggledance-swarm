# SPDX-License-Identifier: Apache-2.0
"""BridgeLLMClient provider plugins.

The base.ProviderPlugin interface plus the first-party plugins:
``cache`` (ExactCacheProvider), ``local-ollama`` (OllamaProvider),
``anthropic-api`` (AnthropicProvider — the live Tier-3 cloud provider,
aliased into the chain's canonical ``cloud`` slot) and ``heuristic``
(HeuristicProvider). Each plugin must work even when its backing
dependency is missing — failure modes degrade to the next fallback tier
rather than raising.

``CloudStubProvider`` is the retired, deliberately-unavailable cloud
placeholder. It is kept exported for backward compatibility but is no
longer wired into the default chain; AnthropicProvider replaced it.
Additional cloud backends (OpenAI / Vertex / Cohere / Groq / etc.) land
in follow-up PRs as further named providers.
"""
from __future__ import annotations

from .base import ProviderPlugin, ProviderError, register_provider, get_provider
from .cache import ExactCacheProvider
from .cloud_stub import CloudStubProvider
from .ollama import OllamaProvider
from .heuristic import HeuristicProvider
from .anthropic import AnthropicProvider

__all__ = [
    "ProviderPlugin",
    "ProviderError",
    "register_provider",
    "get_provider",
    "ExactCacheProvider",
    "CloudStubProvider",
    "OllamaProvider",
    "HeuristicProvider",
    "AnthropicProvider",
]

# SPDX-License-Identifier: Apache-2.0
"""BridgeLLMClient provider plugins.

The base.ProviderPlugin interface plus four first-party plugins:
``cache``, ``ollama``, ``cloud``, ``heuristic``. Each plugin must work even when
its backing dependency is missing — failure modes degrade to the next
fallback tier rather than raising. Cloud provider plugins land in
follow-up PRs (Anthropic / OpenAI / Vertex / Cohere / Groq / etc.).
"""
from __future__ import annotations

from .base import ProviderPlugin, ProviderError, register_provider, get_provider
from .cache import ExactCacheProvider
from .cloud_stub import CloudStubProvider
from .ollama import OllamaProvider
from .heuristic import HeuristicProvider

__all__ = [
    "ProviderPlugin",
    "ProviderError",
    "register_provider",
    "get_provider",
    "ExactCacheProvider",
    "CloudStubProvider",
    "OllamaProvider",
    "HeuristicProvider",
]

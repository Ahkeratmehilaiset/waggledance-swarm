# SPDX-License-Identifier: Apache-2.0
"""R20.2 — BridgeLLMClient runtime LLM augmentation client.

Public exports:

- ``BridgeLLMClient`` — the entry point. Reads
  ``<AGENT_BRIDGE_RUNTIME_ROOT>/llm_config.json`` (or a per-call
  override) and serves requests through a four-tier fallback chain:
  cache → local LLM → cloud LLM → heuristic.
- ``LLMRequest`` / ``LLMResponse`` — the call contract.
- ``BudgetExhausted`` / ``ProviderError`` — exceptions.
- ``register_provider`` — plugin registration hook.

Profile S (no internet, no LLM) is the floor: the cache and heuristic
providers must work without any LLM SDK installed. Local and cloud
providers are loaded lazily and fail open to the next tier when the
backing library is missing.
"""
from __future__ import annotations

from .types import (
    LLMRequest,
    LLMResponse,
    FallbackLevel,
    CallBudget,
)
from .client import BridgeLLMClient
from .budget import BudgetExhausted
from .providers.base import ProviderError, register_provider
from .ab_harness import ABHarness, ABResult
from .redactor import (
    BridgeLLMRedactor,
    BridgeLLMRehydrator,
    RedactionResult,
)

__all__ = [
    "BridgeLLMClient",
    "LLMRequest",
    "LLMResponse",
    "FallbackLevel",
    "CallBudget",
    "BudgetExhausted",
    "ProviderError",
    "register_provider",
    "ABHarness",
    "ABResult",
    "BridgeLLMRedactor",
    "BridgeLLMRehydrator",
    "RedactionResult",
]

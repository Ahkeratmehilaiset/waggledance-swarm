# SPDX-License-Identifier: Apache-2.0
"""Call contract for BridgeLLMClient (R20.2)."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class FallbackLevel(enum.IntEnum):
    """Four-tier fallback per R20 master prompt §2.2."""

    CACHE = 1
    LOCAL_LLM = 2
    CLOUD_LLM = 3
    HEURISTIC = 4


@dataclass
class CallBudget:
    """Per-call budget controls. All fields optional with safe defaults."""

    max_latency_ms: int = 5000
    max_cost_cents: int = 0
    max_retries: int = 1
    allow_cloud: bool = False
    allow_pii_to_cloud: bool = False
    require_json: bool = False
    fallback_policy: str = "best_effort"  # or "fail_fast"


@dataclass
class LLMRequest:
    """One BridgeLLMClient call.

    The injection_point is the WaggleDance call site — required so
    telemetry can measure quality + latency per site.
    """

    injection_point: str
    prompt: str
    intent: str = ""
    budget: CallBudget = field(default_factory=CallBudget)
    speculative: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Response from a single BridgeLLMClient call."""

    text: str
    fallback_level: FallbackLevel
    provider: str
    success: bool
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cents: int = 0
    error_class: str | None = None
    cached: bool = False
    redaction_applied: bool = False
    call_id: str = ""

    def is_cache_hit(self) -> bool:
        return self.fallback_level == FallbackLevel.CACHE and self.cached

    def is_heuristic(self) -> bool:
        return self.fallback_level == FallbackLevel.HEURISTIC

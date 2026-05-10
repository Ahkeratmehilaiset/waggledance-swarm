# SPDX-License-Identifier: Apache-2.0
"""Unavailable cloud-tier placeholder for the four-tier fallback chain."""
from __future__ import annotations

from ..types import LLMRequest, LLMResponse, FallbackLevel
from .base import ProviderError, ProviderPlugin


class CloudStubProvider(ProviderPlugin):
    """Tier 3 placeholder until a real cloud plugin is registered.

    It is deliberately unavailable: the client records the tier as
    skipped and falls through to heuristic instead of pretending cloud
    serving exists.
    """

    name = "cloud"
    fallback_level = FallbackLevel.CLOUD_LLM

    def is_available(self) -> bool:
        return False

    def call(self, request: LLMRequest) -> LLMResponse:
        raise ProviderError("cloud provider plugin not configured")

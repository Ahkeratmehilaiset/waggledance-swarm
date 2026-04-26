# SPDX-License-Identifier: BUSL-1.1
"""Provider registry — Phase 9 §J.

Catalogs available providers + their capabilities, daily budgets,
warm/cold availability, and session affinity hints.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import PROVIDERS


@dataclass(frozen=True)
class ProviderRecord:
    schema_version: int
    provider_id: str
    provider_type: str
    daily_budget_calls: float
    warm: bool
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.provider_type not in PROVIDERS:
            raise ValueError(
                f"unknown provider_type: {self.provider_type!r}; "
                f"allowed: {PROVIDERS}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "daily_budget_calls": self.daily_budget_calls,
            "warm": self.warm,
            "capabilities": list(self.capabilities),
        }


@dataclass
class ProviderRegistry:
    providers: dict[str, ProviderRecord] = field(default_factory=dict)

    def register(self, p: ProviderRecord) -> "ProviderRegistry":
        self.providers[p.provider_id] = p
        return self

    def get(self, provider_id: str) -> ProviderRecord | None:
        return self.providers.get(provider_id)

    def list_by_type(self, provider_type: str) -> list[ProviderRecord]:
        return sorted(
            (p for p in self.providers.values()
              if p.provider_type == provider_type),
            key=lambda p: p.provider_id,
        )

    def warm_providers(self) -> list[ProviderRecord]:
        return [p for p in self.providers.values() if p.warm]

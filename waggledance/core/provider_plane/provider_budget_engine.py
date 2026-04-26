"""Provider-plane budget engine — Phase 9 §J.

Per-provider daily budget tracking. Distinct from the kernel
budget_engine (which tracks per-tick budgets).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderBudgetEntry:
    provider_id: str
    daily_cap_calls: float
    consumed_calls: float = 0.0

    def remaining(self) -> float:
        return max(0.0, self.daily_cap_calls - self.consumed_calls)

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "daily_cap_calls": self.daily_cap_calls,
            "consumed_calls": self.consumed_calls,
            "remaining": self.remaining(),
        }


@dataclass
class ProviderBudgetState:
    entries: dict[str, ProviderBudgetEntry] = field(default_factory=dict)

    def register(self, e: ProviderBudgetEntry) -> "ProviderBudgetState":
        self.entries[e.provider_id] = e
        return self

    def consume(self, provider_id: str, amount: float
                  ) -> tuple["ProviderBudgetState", str | None]:
        if amount < 0:
            return self, "negative_amount"
        e = self.entries.get(provider_id)
        if e is None:
            return self, "unknown_provider"
        if e.consumed_calls + amount > e.daily_cap_calls:
            return self, "over_consumed"
        new_e = ProviderBudgetEntry(
            provider_id=e.provider_id,
            daily_cap_calls=e.daily_cap_calls,
            consumed_calls=e.consumed_calls + amount,
        )
        self.entries[provider_id] = new_e
        return self, None

    def reset_daily(self) -> "ProviderBudgetState":
        for pid, e in list(self.entries.items()):
            self.entries[pid] = ProviderBudgetEntry(
                provider_id=e.provider_id,
                daily_cap_calls=e.daily_cap_calls,
                consumed_calls=0.0,
            )
        return self

    def to_dict(self) -> dict:
        return {pid: e.to_dict()
                for pid, e in sorted(self.entries.items())}

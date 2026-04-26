"""Provider router — Phase 9 §J.

Routes a request_pack to a provider following:
1. task_class priority (constitution-level default)
2. session affinity (preferred agent for capsule_context)
3. budget remaining
4. warm vs cold availability

Returns the chosen provider_id; never makes the call itself.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import PROVIDERS, TASK_CLASS_PRIORITY
from .provider_registry import ProviderRecord, ProviderRegistry


@dataclass(frozen=True)
class RoutingDecision:
    chosen_provider_id: str | None
    chosen_provider_type: str | None
    rationale: str
    fallback_chain: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "chosen_provider_id": self.chosen_provider_id,
            "chosen_provider_type": self.chosen_provider_type,
            "rationale": self.rationale,
            "fallback_chain": list(self.fallback_chain),
        }


def route(*,
             task_class: str,
             registry: ProviderRegistry,
             agent_id_hint: str | None = None,
             require_warm: bool = False,
             ) -> RoutingDecision:
    """Pick a provider per the task_class priority, honoring
    agent_id_hint if it points at a registered provider."""
    if task_class not in TASK_CLASS_PRIORITY:
        return RoutingDecision(
            chosen_provider_id=None, chosen_provider_type=None,
            rationale=f"unknown task_class {task_class!r}",
            fallback_chain=(),
        )

    # 1. Honor agent_id_hint
    if agent_id_hint:
        hint = registry.get(agent_id_hint)
        if hint is not None and (not require_warm or hint.warm):
            return RoutingDecision(
                chosen_provider_id=hint.provider_id,
                chosen_provider_type=hint.provider_type,
                rationale=f"session affinity hint matched {agent_id_hint!r}",
                fallback_chain=TASK_CLASS_PRIORITY[task_class],
            )

    # 2. Priority order
    chain = TASK_CLASS_PRIORITY[task_class]
    for ptype in chain:
        candidates = registry.list_by_type(ptype)
        if require_warm:
            candidates = [c for c in candidates if c.warm]
        if not candidates:
            continue
        # Pick the candidate with the largest daily_budget_calls
        chosen = max(candidates, key=lambda c: c.daily_budget_calls)
        return RoutingDecision(
            chosen_provider_id=chosen.provider_id,
            chosen_provider_type=chosen.provider_type,
            rationale=(
                f"task_class {task_class}: priority={ptype}; "
                f"budget_calls={chosen.daily_budget_calls}"
            ),
            fallback_chain=chain,
        )

    return RoutingDecision(
        chosen_provider_id=None, chosen_provider_type=None,
        rationale=f"no available provider in chain for {task_class}",
        fallback_chain=chain,
    )

"""Autonomy attention allocator — Phase 9 §F.attention_allocator.

Routes attention across lanes/capsules. Attention allocation is
ROUTING ONLY — never mutation. The allocator computes per-lane
weights from recent activity, breaker state, budget remaining, and
capsule priority — and emits a deterministic weighted ordering.

Crown-jewel area waggledance/core/autonomy/*
(BUSL Change Date 2030-03-19).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import kernel_state as ks


@dataclass(frozen=True)
class AttentionWeight:
    lane: str
    weight: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"lane": self.lane, "weight": self.weight,
                "reasons": list(self.reasons)}


# Default base weights per lane; sum to 1.0 by construction
_BASE_WEIGHTS = {
    "provider_plane": 0.25,
    "builder_lane": 0.20,
    "solver_synthesis": 0.15,
    "ingestion": 0.15,
    "memory_tiers": 0.10,
    "promotion": 0.05,
    "self_inspection": 0.05,
    "wait": 0.05,
}


def allocate(state: ks.KernelState,
                base_weights: dict[str, float] | None = None,
                capsule_priority: dict[str, float] | None = None,
                ) -> tuple[AttentionWeight, ...]:
    """Compute attention weights deterministically from KernelState.

    Effects (additive on base weight, then renormalized):
    - breaker open / quarantined → weight × 0.0 (suppressed)
    - breaker half_open → weight × 0.5
    - budget remaining < 10% of cap → weight × 0.3
    - capsule_priority adjusts the lane's weight by + delta
      (only the kernel's current capsule_context is considered)
    """
    base = dict(base_weights or _BASE_WEIGHTS)
    cap_pri = dict(capsule_priority or {})

    breaker_state = {b.name: ("quarantined" if b.quarantined else b.state)
                      for b in state.circuit_breakers}
    budget_share: dict[str, float] = {}
    for b in state.budgets:
        if b.hard_cap > 0:
            budget_share[b.name] = b.remaining() / b.hard_cap
        else:
            budget_share[b.name] = 0.0

    out: list[AttentionWeight] = []
    for lane, w in sorted(base.items()):
        reasons: list[str] = [f"base={w:.3f}"]
        bs = breaker_state.get(lane, "closed")
        if bs in ("open", "quarantined"):
            w = 0.0
            reasons.append(f"breaker_{bs}_zeroes")
        elif bs == "half_open":
            w = w * 0.5
            reasons.append("breaker_half_open_halves")

        # Budget pressure: provider_plane consumes provider_calls_per_tick
        # builder_lane / solver_synthesis consume builder_invocations
        budget_name = {
            "provider_plane": "provider_calls_per_tick",
            "builder_lane": "builder_invocations_per_tick",
            "solver_synthesis": "builder_invocations_per_tick",
        }.get(lane)
        if budget_name and budget_share.get(budget_name, 1.0) < 0.10:
            w = w * 0.3
            reasons.append(f"budget_{budget_name}_low")

        # Capsule-priority delta (only when matching context)
        delta = cap_pri.get(state.capsule_context, 0.0)
        if delta and lane != "wait":
            w = w + delta
            reasons.append(f"capsule_{state.capsule_context}_delta={delta:+.3f}")
            if w < 0.0:
                w = 0.0

        out.append(AttentionWeight(lane=lane, weight=round(w, 6),
                                       reasons=tuple(reasons)))

    # Renormalize so weights sum to 1.0 (or all-zero if everything
    # was suppressed)
    total = sum(a.weight for a in out)
    if total > 0:
        out = [
            AttentionWeight(lane=a.lane,
                              weight=round(a.weight / total, 6),
                              reasons=a.reasons + (f"normalized_total={total:.3f}",))
            for a in out
        ]
    return tuple(out)


def ordered_lanes(weights: tuple[AttentionWeight, ...]) -> tuple[str, ...]:
    """Return lane names sorted by weight desc, then lane ascending."""
    return tuple(w.lane for w in sorted(
        weights, key=lambda x: (-x.weight, x.lane)
    ))

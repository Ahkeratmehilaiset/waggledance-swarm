"""Action gate — Phase 9 §F.action_gate.

The ONLY exit point from the autonomy kernel (constitution hard rule
`action_gate_is_only_exit`). Receives ActionRecommendations from the
governor, evaluates them against:

1. policy_core (hard + adaptive rules)
2. budget_engine (per-tick budget caps)
3. circuit breaker state

Returns a verdict per recommendation:
- ADMIT_TO_LANE → recommendation may be handed to its lane's queue
- DEFER → put back in mission_queue, retry later
- REJECT_HARD → policy/constitution/budget violation
- REJECT_SOFT → advisory blockers; may retry next tick

CRITICAL CONTRACT (Prompt_1_Master §GLOBAL NO-AUTO-ENACTMENT):
- the action_gate itself executes nothing; it only authorizes
  hand-off to a lane queue
- ADMIT_TO_LANE is NOT live execution; it is permission to enqueue

Crown-jewel area waggledance/core/autonomy/*
(BUSL Change Date 2030-03-19).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import (
    budget_engine as be,
    governor as gov,
    kernel_state as ks,
    policy_core as pc,
)


GATE_VERDICTS = ("ADMIT_TO_LANE", "DEFER", "REJECT_HARD", "REJECT_SOFT")


@dataclass(frozen=True)
class GateVerdict:
    recommendation_id: str
    verdict: str                  # one of GATE_VERDICTS
    reason: str
    blocking_rule_ids: tuple[str, ...] = ()
    advisory_rule_ids: tuple[str, ...] = ()
    breaker_state: str | None = None
    budget_violation: dict | None = None

    def to_dict(self) -> dict:
        d = {
            "recommendation_id": self.recommendation_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "blocking_rule_ids": list(self.blocking_rule_ids),
            "advisory_rule_ids": list(self.advisory_rule_ids),
        }
        if self.breaker_state is not None:
            d["breaker_state"] = self.breaker_state
        if self.budget_violation is not None:
            d["budget_violation"] = self.budget_violation
        return d


@dataclass(frozen=True)
class GateBatchReport:
    tick_id: int
    verdicts: tuple[GateVerdict, ...]
    counts_by_verdict: dict[str, int]
    has_fatal_budget: bool

    def to_dict(self) -> dict:
        return {
            "tick_id": self.tick_id,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "counts_by_verdict": dict(sorted(self.counts_by_verdict.items())),
            "has_fatal_budget": self.has_fatal_budget,
        }


def _breaker_state_for_lane(state: ks.KernelState, lane: str) -> str:
    for b in state.circuit_breakers:
        if b.name == lane:
            if b.quarantined:
                return "quarantined"
            return b.state
    # Unknown lane → treat as closed (open assumption would lock the
    # gate when new lanes are introduced; better to allow + audit).
    return "closed"


def _budget_for_kind(kind: str) -> str:
    """Map a recommendation kind to the budget it consumes."""
    mapping = {
        "consultation_request": "provider_calls_per_tick",
        "builder_request": "builder_invocations_per_tick",
        "solver_synthesis_request": "builder_invocations_per_tick",
        # noop, ingest_request, memory_tier_move, calibration_check,
        # shadow_replay_request, promotion_review_request → no budget
        # consumption at the gate level
    }
    return mapping.get(kind, "")


def evaluate_one(
    *,
    recommendation: gov.ActionRecommendation,
    state: ks.KernelState,
    hard_rules: tuple[pc.HardRule, ...],
    adaptive_rules: tuple[pc.PolicyRule, ...] = (),
    budgets: tuple[ks.BudgetEntry, ...] | None = None,
) -> GateVerdict:
    """Run policy + breaker + budget checks on one recommendation."""
    budgets = budgets if budgets is not None else state.budgets

    # 1. Circuit breaker
    breaker_state = _breaker_state_for_lane(state, recommendation.lane)
    if breaker_state in ("open", "quarantined"):
        return GateVerdict(
            recommendation_id=recommendation.recommendation_id,
            verdict="DEFER",
            reason=f"circuit breaker for lane {recommendation.lane!r} is {breaker_state!r}",
            breaker_state=breaker_state,
        )

    # 2. Policy
    ev = pc.evaluate(
        action_id=recommendation.recommendation_id,
        action_kind=recommendation.kind,
        action_lane=recommendation.lane,
        requires_human_review=recommendation.requires_human_review,
        no_runtime_mutation=recommendation.no_runtime_mutation,
        hard_rules=hard_rules,
        adaptive_rules=adaptive_rules,
        capsule_context=recommendation.capsule_context,
    )
    if not ev.allowed:
        return GateVerdict(
            recommendation_id=recommendation.recommendation_id,
            verdict="REJECT_HARD",
            reason="policy block: " + (ev.reasons[0] if ev.reasons else "unspecified"),
            blocking_rule_ids=ev.blocking_rule_ids,
            advisory_rule_ids=ev.advisory_rule_ids,
            breaker_state=breaker_state,
        )

    # 3. Budget reservation (no actual consumption — that happens in
    #    the lane after work succeeds)
    budget_name = _budget_for_kind(recommendation.kind)
    budget_violation = None
    if budget_name:
        amount = 1.0
        if recommendation.budget_estimate:
            if "provider_calls" in recommendation.budget_estimate:
                amount = float(recommendation.budget_estimate["provider_calls"])
            elif "builder_invocations" in recommendation.budget_estimate:
                amount = float(recommendation.budget_estimate["builder_invocations"])
        _, v = be.reserve(budgets, name=budget_name, amount=amount)
        if v is not None:
            budget_violation = v.to_dict()
            if v.severity == "fatal":
                return GateVerdict(
                    recommendation_id=recommendation.recommendation_id,
                    verdict="REJECT_HARD",
                    reason=f"budget fatal: {v.kind} on {v.budget_name}",
                    advisory_rule_ids=ev.advisory_rule_ids,
                    breaker_state=breaker_state,
                    budget_violation=budget_violation,
                )
            # recoverable → DEFER (back-off)
            return GateVerdict(
                recommendation_id=recommendation.recommendation_id,
                verdict="DEFER",
                reason=f"budget over-reserved on {v.budget_name}",
                advisory_rule_ids=ev.advisory_rule_ids,
                breaker_state=breaker_state,
                budget_violation=budget_violation,
            )

    return GateVerdict(
        recommendation_id=recommendation.recommendation_id,
        verdict="ADMIT_TO_LANE",
        reason=("admitted; advisories: "
                + ",".join(ev.advisory_rule_ids) if ev.advisory_rule_ids
                else "admitted"),
        advisory_rule_ids=ev.advisory_rule_ids,
        breaker_state=breaker_state,
    )


def evaluate_batch(
    *,
    recommendations: Iterable[gov.ActionRecommendation],
    state: ks.KernelState,
    hard_rules: tuple[pc.HardRule, ...],
    adaptive_rules: tuple[pc.PolicyRule, ...] = (),
) -> GateBatchReport:
    """Run gate evaluation across a batch. Budget reservations are
    tracked across the batch so cumulative over-reservation correctly
    DEFERs subsequent items."""
    counts = {v: 0 for v in GATE_VERDICTS}
    verdicts: list[GateVerdict] = []
    running_budgets = state.budgets
    has_fatal = False

    for rec in recommendations:
        verdict = evaluate_one(
            recommendation=rec, state=state,
            hard_rules=hard_rules, adaptive_rules=adaptive_rules,
            budgets=running_budgets,
        )
        verdicts.append(verdict)
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
        # If admitted, persist the reservation onto the running budgets
        if verdict.verdict == "ADMIT_TO_LANE":
            budget_name = _budget_for_kind(rec.kind)
            if budget_name:
                amount = 1.0
                if rec.budget_estimate:
                    if "provider_calls" in rec.budget_estimate:
                        amount = float(rec.budget_estimate["provider_calls"])
                    elif "builder_invocations" in rec.budget_estimate:
                        amount = float(rec.budget_estimate["builder_invocations"])
                running_budgets, _ = be.reserve(
                    running_budgets, name=budget_name, amount=amount,
                )
        if verdict.budget_violation and \
                verdict.budget_violation.get("severity") == "fatal":
            has_fatal = True

    tick_id = state.last_tick.tick_id if state.last_tick else 0
    return GateBatchReport(
        tick_id=tick_id,
        verdicts=tuple(verdicts),
        counts_by_verdict=counts,
        has_fatal_budget=has_fatal,
    )

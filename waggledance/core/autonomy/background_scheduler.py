"""Autonomy background scheduler — Phase 9 §F.background_scheduler.

Orchestrates one background pass: select missions from the queue
according to attention weights + breaker state + budget remaining,
hand them to action_gate (which never executes), and produce a
deterministic dispatch report.

CRITICAL CONTRACT:
- the scheduler does NOT execute missions
- it only DECIDES which missions are admissible this tick and writes
  them as ActionRecommendations into a deterministic ordering
"""
from __future__ import annotations

from dataclasses import dataclass

from . import (
    action_gate as ag,
    attention_allocator as aa,
    governor as gov,
    kernel_state as ks,
    mission_queue as mq,
    policy_core as pc,
)


@dataclass(frozen=True)
class DispatchReport:
    tick_id: int
    selected_missions: tuple[mq.Mission, ...]
    deferred_missions: tuple[mq.Mission, ...]
    blocked_missions: tuple[mq.Mission, ...]
    attention_weights: tuple[aa.AttentionWeight, ...]
    gate_report: ag.GateBatchReport

    def to_dict(self) -> dict:
        return {
            "tick_id": self.tick_id,
            "selected": [m.to_dict() for m in self.selected_missions],
            "deferred": [m.to_dict() for m in self.deferred_missions],
            "blocked": [m.to_dict() for m in self.blocked_missions],
            "attention_weights": [w.to_dict() for w in self.attention_weights],
            "gate_report": self.gate_report.to_dict(),
        }


def _mission_to_recommendation(m: mq.Mission, tick_id: int
                                  ) -> gov.ActionRecommendation:
    return gov.make_recommendation(
        tick_id=tick_id, kind=m.kind, lane=m.lane,
        intent=m.intent, rationale=m.rationale,
        capsule_context=m.capsule_context,
        evidence_refs=m.evidence_refs,
    )


def schedule_one_tick(*,
                          state: ks.KernelState,
                          missions: list[mq.Mission],
                          hard_rules: tuple[pc.HardRule, ...],
                          adaptive_rules: tuple[pc.PolicyRule, ...] = (),
                          max_dispatched: int = 20,
                          ) -> DispatchReport:
    """Run one scheduling pass.

    Algorithm (deterministic):
    1. compute attention weights from KernelState
    2. take only open missions
    3. order by deterministic_order (priority desc, mission_id asc)
    4. filter through circuit breakers
    5. send admissible missions through action_gate as
       ActionRecommendations
    6. cap selected to max_dispatched
    """
    weights = aa.allocate(state)

    open_m = mq.open_missions(missions)
    ordered = mq.deterministic_order(open_m)

    breaker_states = {b.name: ("quarantined" if b.quarantined else b.state)
                        for b in state.circuit_breakers}
    admissible, blocked = mq.filter_by_breakers(ordered, breaker_states)

    tick_id = state.last_tick.tick_id if state.last_tick else 0

    recs = [_mission_to_recommendation(m, tick_id) for m in admissible]
    gate_report = ag.evaluate_batch(
        recommendations=recs, state=state,
        hard_rules=hard_rules, adaptive_rules=adaptive_rules,
    )

    # Map verdicts back to missions
    verdict_by_id = {v.recommendation_id: v for v in gate_report.verdicts}
    selected: list[mq.Mission] = []
    deferred: list[mq.Mission] = []
    for m, r in zip(admissible, recs):
        v = verdict_by_id.get(r.recommendation_id)
        if v is None:
            deferred.append(m)
            continue
        if v.verdict == "ADMIT_TO_LANE" and len(selected) < max_dispatched:
            selected.append(m)
        elif v.verdict == "DEFER":
            deferred.append(m)
        elif v.verdict in ("REJECT_HARD", "REJECT_SOFT"):
            blocked.append(m)
        else:
            deferred.append(m)

    return DispatchReport(
        tick_id=tick_id,
        selected_missions=tuple(selected),
        deferred_missions=tuple(deferred),
        blocked_missions=tuple(blocked),
        attention_weights=weights,
        gate_report=gate_report,
    )

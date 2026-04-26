"""Autonomy micro-learning lane — Phase 9 §F.micro_learning_lane.

Fast reprioritization lane that DOES NOT mutate runtime or solver
state. It only updates per-mission priority hints based on quick
feedback signals: gate verdicts, deferral counts, breaker state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import action_gate as ag, mission_queue as mq


@dataclass(frozen=True)
class PriorityHint:
    mission_id: str
    delta: float        # additive change to mission.priority on next reprioritize
    reason: str

    def to_dict(self) -> dict:
        return {"mission_id": self.mission_id, "delta": self.delta,
                "reason": self.reason}


def hints_from_gate_report(report: ag.GateBatchReport,
                                missions: Iterable[mq.Mission]
                                ) -> tuple[PriorityHint, ...]:
    """Generate priority hints from a gate report.

    - DEFER → -0.05 (deprioritize next round)
    - REJECT_HARD → -1.0 (push to bottom)
    - REJECT_SOFT → -0.10
    - ADMIT_TO_LANE → 0.0 (no change; success will adjust elsewhere)
    """
    mission_by_intent = {m.intent: m for m in missions}
    hints: list[PriorityHint] = []
    for v in report.verdicts:
        # Recommendations carry no mission_id directly; we keyed
        # by recommendation_id which derives from (tick, kind, lane,
        # intent). For Phase F the simpler mapping is by intent.
        # The proper bidirectional map will arrive when missions
        # carry recommendation_id directly in a later session.
        pass
    # Generate hints based on verdict counts only (lane-level signal
    # rather than per-mission for now)
    for v in report.verdicts:
        if v.verdict == "DEFER":
            hints.append(PriorityHint(
                mission_id=v.recommendation_id,
                delta=-0.05,
                reason=f"DEFER: {v.reason[:80]}",
            ))
        elif v.verdict == "REJECT_HARD":
            hints.append(PriorityHint(
                mission_id=v.recommendation_id,
                delta=-1.0,
                reason=f"REJECT_HARD: {v.reason[:80]}",
            ))
        elif v.verdict == "REJECT_SOFT":
            hints.append(PriorityHint(
                mission_id=v.recommendation_id,
                delta=-0.10,
                reason=f"REJECT_SOFT: {v.reason[:80]}",
            ))
    return tuple(hints)


def apply_hints(missions: list[mq.Mission],
                  hints: Iterable[PriorityHint],
                  recommendation_to_mission: dict[str, str] | None = None,
                  ) -> list[mq.Mission]:
    """Apply hints to mission priorities. recommendation_to_mission
    maps recommendation_id → mission_id. If not supplied, hints
    addressed to recommendation_ids are no-ops on missions.

    Returns a new list; never mutates the input list or its missions.
    """
    delta_by_mission: dict[str, float] = {}
    rmap = recommendation_to_mission or {}
    for h in hints:
        target_mid = rmap.get(h.mission_id, h.mission_id)
        delta_by_mission[target_mid] = (
            delta_by_mission.get(target_mid, 0.0) + h.delta
        )
    out: list[mq.Mission] = []
    for m in missions:
        delta = delta_by_mission.get(m.mission_id, 0.0)
        if delta == 0.0:
            out.append(m)
            continue
        new_priority = max(0.0, m.priority + delta)
        out.append(mq.Mission(
            schema_version=m.schema_version,
            mission_id=m.mission_id,
            kind=m.kind, lane=m.lane,
            priority=new_priority,
            intent=m.intent, rationale=m.rationale,
            lifecycle_status=m.lifecycle_status,
            no_runtime_mutation=m.no_runtime_mutation,
            created_tick_id=m.created_tick_id,
            capsule_context=m.capsule_context,
            evidence_refs=m.evidence_refs,
            completed_tick_id=m.completed_tick_id,
            max_retries=m.max_retries,
            retry_count=m.retry_count,
            circuit_breaker_lane=m.circuit_breaker_lane,
        ))
    return out

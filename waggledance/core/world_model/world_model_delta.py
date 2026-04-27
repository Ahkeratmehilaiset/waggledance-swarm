# SPDX-License-Identifier: BUSL-1.1
"""World model delta — Phase 9 §I.

Diff between two consecutive WorldModelSnapshots: which external
facts appeared/disappeared/changed confidence, which causal relations
were added/removed, which predictions were evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .world_model_snapshot import WorldModelSnapshot


@dataclass(frozen=True)
class WorldModelDelta:
    schema_version: int
    from_snapshot_id: str
    to_snapshot_id: str
    facts_added: tuple[str, ...]
    facts_removed: tuple[str, ...]
    facts_confidence_shifted: tuple[dict, ...]
    causes_added: tuple[tuple[str, str], ...]
    causes_removed: tuple[tuple[str, str], ...]
    predictions_evaluated: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "from_snapshot_id": self.from_snapshot_id,
            "to_snapshot_id": self.to_snapshot_id,
            "facts_added": list(self.facts_added),
            "facts_removed": list(self.facts_removed),
            "facts_confidence_shifted":
                [dict(s) for s in self.facts_confidence_shifted],
            "causes_added": [list(c) for c in self.causes_added],
            "causes_removed": [list(c) for c in self.causes_removed],
            "predictions_evaluated": list(self.predictions_evaluated),
        }


def compute_delta(prev: WorldModelSnapshot,
                       curr: WorldModelSnapshot,
                       *,
                       confidence_shift_threshold: float = 0.10,
                       ) -> WorldModelDelta:
    prev_facts = {f.fact_id: f for f in prev.external_facts}
    curr_facts = {f.fact_id: f for f in curr.external_facts}
    added = sorted(set(curr_facts) - set(prev_facts))
    removed = sorted(set(prev_facts) - set(curr_facts))
    shifted: list[dict] = []
    for fid in sorted(set(prev_facts) & set(curr_facts)):
        d = curr_facts[fid].confidence - prev_facts[fid].confidence
        if abs(d) >= confidence_shift_threshold:
            shifted.append({
                "fact_id": fid,
                "from_confidence": prev_facts[fid].confidence,
                "to_confidence": curr_facts[fid].confidence,
                "delta": round(d, 6),
            })

    prev_causes = {(r.cause_fact_id, r.effect_fact_id)
                    for r in prev.causal_relations}
    curr_causes = {(r.cause_fact_id, r.effect_fact_id)
                    for r in curr.causal_relations}
    causes_added = tuple(sorted(curr_causes - prev_causes))
    causes_removed = tuple(sorted(prev_causes - curr_causes))

    prev_evaluated = {p.prediction_id for p in prev.predictions
                       if p.actual_value is not None}
    curr_evaluated = {p.prediction_id for p in curr.predictions
                       if p.actual_value is not None}
    newly_evaluated = tuple(sorted(curr_evaluated - prev_evaluated))

    return WorldModelDelta(
        schema_version=1,
        from_snapshot_id=prev.snapshot_id,
        to_snapshot_id=curr.snapshot_id,
        facts_added=tuple(added),
        facts_removed=tuple(removed),
        facts_confidence_shifted=tuple(shifted),
        causes_added=causes_added,
        causes_removed=causes_removed,
        predictions_evaluated=newly_evaluated,
    )

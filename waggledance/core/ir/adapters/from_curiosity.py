# SPDX-License-Identifier: BUSL-1.1
"""Adapter: Session A curiosity log → IR.curiosity objects."""
from __future__ import annotations

from typing import Iterable

from ..cognition_ir import IRObject, Provenance, make_ir


def adapt_curiosity_log(log: Iterable[dict],
                              provenance: Provenance,
                              capsule_context: str = "neutral_v1",
                              ) -> list[IRObject]:
    out: list[IRObject] = []
    for row in log:
        cell = row.get("candidate_cell") or "_unattributed"
        cur_id = row.get("curiosity_id", "")
        gap_kind = row.get("suspected_gap_type", "unknown")
        ev_value = float(row.get("estimated_value") or 0.0)
        risk = "high" if ev_value >= 8.0 else \
            ("medium" if ev_value >= 4.0 else "low")
        out.append(make_ir(
            ir_type="curiosity",
            payload={
                "candidate_cell": cell,
                "curiosity_id": cur_id,
                "suspected_gap_type": gap_kind,
                "estimated_value": ev_value,
                "count": int(row.get("count") or 0),
                "fallback_rate": float(row.get("fallback_rate") or 0.0),
            },
            provenance=provenance,
            capsule_context=capsule_context,
            risk=risk,
            evidence_refs=(cur_id,) if cur_id else (),
        ))
    return out

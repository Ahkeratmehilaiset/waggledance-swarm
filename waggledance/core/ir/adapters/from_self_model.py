# SPDX-License-Identifier: BUSL-1.1
"""Adapter: Session B self-model → IR.tension + IR.blind_spot."""
from __future__ import annotations

from ..cognition_ir import IRObject, Provenance, make_ir


_SEVERITY_TO_RISK = {"low": "low", "medium": "medium", "high": "high"}


def adapt_self_model(snapshot: dict,
                          provenance: Provenance,
                          capsule_context: str = "neutral_v1",
                          ) -> list[IRObject]:
    out: list[IRObject] = []
    for t in snapshot.get("workspace_tensions") or []:
        sev = t.get("severity", "low")
        out.append(make_ir(
            ir_type="tension",
            payload={
                "tension_id": t.get("tension_id", ""),
                "type": t.get("type", "unknown"),
                "claim": t.get("claim", ""),
                "observation": t.get("observation", ""),
                "severity": sev,
                "lifecycle": t.get("lifecycle_status"),
                "resolution_path": t.get("resolution_path"),
            },
            provenance=provenance,
            capsule_context=capsule_context,
            risk=_SEVERITY_TO_RISK.get(sev, "low"),
            lifecycle_status=("persisting" if t.get("lifecycle_status")
                                 == "persisting" else "active"),
            evidence_refs=tuple(t.get("evidence_refs") or ()),
        ))
    for bs in snapshot.get("blind_spots") or []:
        sev = bs.get("severity", "low")
        out.append(make_ir(
            ir_type="blind_spot",
            payload={
                "domain": bs.get("domain", "unknown"),
                "severity": sev,
                "detectors": list(bs.get("detectors") or ()),
            },
            provenance=provenance,
            capsule_context=capsule_context,
            risk=_SEVERITY_TO_RISK.get(sev, "low"),
            evidence_refs=tuple(bs.get("detectors") or ()),
        ))
    return out

"""Adapter: Session C dream curriculum + meta proposals → IR.dream_target / IR.shadow_candidate / IR.meta_proposal."""
from __future__ import annotations

from ..cognition_ir import IRObject, Provenance, make_ir


def adapt_dream_curriculum(curriculum: dict,
                                  provenance: Provenance,
                                  capsule_context: str = "neutral_v1",
                                  ) -> list[IRObject]:
    out: list[IRObject] = []
    for n in curriculum.get("nights") or []:
        items = n.get("target_items") or []
        for it in items:
            cell = it.get("candidate_cell")
            tid = it.get("source_id") or ""
            out.append(make_ir(
                ir_type="dream_target",
                payload={
                    "source_tension_id": tid,
                    "candidate_cell": cell,
                    "mode": n.get("mode") or "wait",
                    "night_index": n.get("night_index"),
                    "uncertainty": n.get("uncertainty"),
                },
                provenance=provenance,
                capsule_context=capsule_context,
                risk="medium",
                evidence_refs=(tid,) if tid else (),
            ))
    return out


def adapt_dream_meta_proposal(mp: dict,
                                       provenance: Provenance,
                                       capsule_context: str = "neutral_v1",
                                       ) -> list[IRObject]:
    """One Session C dream_meta_proposal.json → IR.shadow_candidate
    + IR.meta_proposal."""
    out: list[IRObject] = []
    sel = mp.get("selected_proposal") or {}
    if sel:
        out.append(make_ir(
            ir_type="shadow_candidate",
            payload={
                "solver_name": sel.get("solver_name", ""),
                "cell_id": sel.get("cell_id"),
                "solver_hash": sel.get("solver_hash"),
                "proposal_id": sel.get("proposal_id"),
            },
            provenance=provenance,
            capsule_context=capsule_context,
            risk="high",
            promotion_state="shadow",
            evidence_refs=(sel.get("solver_hash") or "",),
        ))
    if mp.get("structurally_promising"):
        out.append(make_ir(
            ir_type="meta_proposal",
            payload={
                "proposal_type": "solver_family_growth",
                "scope_class": "solver_library",
                "canonical_target": (sel.get("cell_id") or "general"),
                "expected_value_of_merging": float(
                    mp.get("expected_value_of_merging") or 0.0
                ),
                "confidence": float(mp.get("confidence") or 0.0),
                "structurally_promising": True,
            },
            provenance=provenance,
            capsule_context=capsule_context,
            risk="medium",
            promotion_state="review_ready",
            evidence_refs=tuple(mp.get("source_tension_ids") or ()),
        ))
    return out

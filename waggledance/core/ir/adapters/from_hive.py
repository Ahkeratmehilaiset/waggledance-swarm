"""Adapter: Session D hive proposals + review bundle → IR.review_candidate / IR.meta_proposal."""
from __future__ import annotations

from ..cognition_ir import IRObject, Provenance, make_ir


_RISK_MAP = {"low": "low", "medium": "medium", "high": "high"}


def adapt_hive_proposals(hive_proposals: dict,
                                provenance: Provenance,
                                capsule_context: str = "neutral_v1",
                                ) -> list[IRObject]:
    out: list[IRObject] = []
    for p in hive_proposals.get("proposals") or []:
        out.append(make_ir(
            ir_type="meta_proposal",
            payload={
                "proposal_type": p.get("proposal_type", "unknown"),
                "scope_class": p.get("scope_class", "review_only"),
                "canonical_target": p.get("canonical_target") or "",
                "expected_value": float(p.get("expected_value") or 0.0),
                "confidence": float(p.get("confidence") or 0.0),
                "proposal_priority": float(p.get("proposal_priority") or 0.0),
                "meta_proposal_id_session_d": p.get("meta_proposal_id"),
                "impacted_cells": list(p.get("impacted_cells") or ()),
            },
            provenance=provenance,
            capsule_context=capsule_context,
            risk=_RISK_MAP.get(p.get("risk", "low"), "low"),
            promotion_state="review_ready",
            lifecycle_status=p.get("lifecycle_status") or "active",
            evidence_refs=tuple(p.get("source_tension_ids") or ()),
        ))
    return out


def adapt_review_bundle(bundle: dict,
                              provenance: Provenance,
                              capsule_context: str = "neutral_v1",
                              ) -> list[IRObject]:
    out: list[IRObject] = []
    for entry in bundle.get("proposals") or []:
        action = entry.get("recommended_next_human_action", "")
        promo = "review_ready"
        if action == "post_campaign_runtime_review_candidate":
            promo = "post_campaign_runtime_review_candidate"
        elif action == "archive_as_low_value":
            promo = "archived"
        out.append(make_ir(
            ir_type="review_candidate",
            payload={
                "meta_proposal_id_session_d": entry.get("meta_proposal_id", ""),
                "canonical_target": entry.get("meta_proposal_id", ""),
                "recommended_next_human_action": action,
                "proposal_priority": float(entry.get("proposal_priority") or 0.0),
                "confidence": float(entry.get("confidence") or 0.0),
            },
            provenance=provenance,
            capsule_context=capsule_context,
            risk="low",
            promotion_state=promo,
        ))
    return out

"""Identity anchor validation — Phase 9 §H.

Foundational vector additions enter `candidate` state first. Sampled
consistency + contradiction validation runs before promotion to
`supportive`. Final promotion to `foundational` (weight 1.0) is
human-gated by default.

CRITICAL HARD RULE (constitution.no_foundational_auto_promotion):
foundational anchors must NOT be promoted from candidate / supportive
to foundational without explicit human approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import ANCHOR_STATUSES
from .vector_provenance_graph import VectorNode


@dataclass(frozen=True)
class AnchorValidation:
    node_id: str
    consistency_score: float    # [0, 1]
    contradiction_count: int
    sample_size: int
    promoted_to: str            # one of ANCHOR_STATUSES
    rationale: str

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "consistency_score": self.consistency_score,
            "contradiction_count": self.contradiction_count,
            "sample_size": self.sample_size,
            "promoted_to": self.promoted_to,
            "rationale": self.rationale,
        }


# Default thresholds (capsule manifests may narrow)
DEFAULT_PROMOTE_TO_SUPPORTIVE_CONSISTENCY = 0.70
DEFAULT_REJECT_CONTRADICTION_COUNT = 3


def evaluate_candidate(*,
                            candidate: VectorNode,
                            siblings_in_graph: Iterable[VectorNode],
                            min_consistency: float = DEFAULT_PROMOTE_TO_SUPPORTIVE_CONSISTENCY,
                            max_contradictions: int = DEFAULT_REJECT_CONTRADICTION_COUNT,
                            ) -> AnchorValidation:
    """Evaluate a candidate against a sample of sibling nodes in the
    graph. Returns a validation record with proposed promotion target.

    NOTE: this routine NEVER auto-promotes to foundational. The
    highest it may propose is `supportive`. Human review (separate
    artifact, future Phase Z) handles foundational promotion.
    """
    siblings = list(siblings_in_graph)
    sample_size = len(siblings)
    contradictions = 0
    supports = 0
    extends = 0
    for s in siblings:
        for edge in s.lineage:
            if edge.target_node_id != candidate.node_id:
                continue
            if edge.relation == "contradicts":
                contradictions += 1
            elif edge.relation in ("supports", "extends",
                                       "specializes", "translation"):
                supports += 1
            elif edge.relation == "generalizes":
                extends += 1
    consistency = 0.0
    if sample_size > 0:
        consistency = round(
            max(0.0, (supports + extends - contradictions) / sample_size), 6
        )
    promoted_to = candidate.anchor_status
    rationale = ""
    if contradictions >= max_contradictions:
        promoted_to = "rejected"
        rationale = (
            f"contradictions {contradictions} ≥ max {max_contradictions} "
            f"(sample={sample_size}); rejected"
        )
    elif consistency >= min_consistency and sample_size > 0:
        promoted_to = "supportive"
        rationale = (
            f"consistency {consistency:.3f} ≥ {min_consistency} "
            f"(sample={sample_size}); promoted to supportive"
        )
    elif sample_size == 0:
        promoted_to = "candidate"
        rationale = "no siblings in graph; remains candidate"
    else:
        promoted_to = "candidate"
        rationale = (
            f"consistency {consistency:.3f} < {min_consistency} "
            f"(sample={sample_size}); remains candidate"
        )

    return AnchorValidation(
        node_id=candidate.node_id,
        consistency_score=consistency,
        contradiction_count=contradictions,
        sample_size=sample_size,
        promoted_to=promoted_to,
        rationale=rationale,
    )


def assert_no_auto_promote_to_foundational(*,
                                                from_status: str,
                                                to_status: str,
                                                human_approval_id: str | None = None,
                                                ) -> None:
    """Constitution hard rule: refuse foundational promotion without
    explicit human approval."""
    if to_status == "foundational" and not human_approval_id:
        raise PermissionError(
            "foundational anchor promotion requires explicit "
            "human_approval_id; current request has none. "
            "Hard rule constitution.no_foundational_auto_promotion."
        )

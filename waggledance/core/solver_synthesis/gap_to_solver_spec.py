"""Gap → solver spec router (U3 entrypoint) — Phase 9 §U3.

Per Prompt_1_Master §U1 ROUTING RULES:
- match_confidence > 0.8 → U1 declarative path
- match_confidence < 0.5 → U3 free-form synthesis
- 0.5 ≤ confidence ≤ 0.8 → try U1 first, fall back to U3

This module routes gap signals (curiosity items, tensions) to the
appropriate path. U1 wins whenever possible because it is faster,
cheaper, more auditable, less hallucination-prone.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import U1_HIGH_CONFIDENCE_THRESHOLD, U1_LOW_CONFIDENCE_THRESHOLD
from .bulk_rule_extractor import FamilyMatch, extract_from_table


@dataclass(frozen=True)
class GapRoutingDecision:
    gap_ref: str
    chosen_path: str           # "U1_compile" | "U3_residual" | "U1_with_U3_fallback"
    best_match: FamilyMatch | None
    rationale: str

    def to_dict(self) -> dict:
        return {
            "gap_ref": self.gap_ref,
            "chosen_path": self.chosen_path,
            "best_match": (self.best_match.to_dict()
                            if self.best_match else None),
            "rationale": self.rationale,
        }


def route_gap(*,
                  gap_ref: str,
                  table_hint: dict | None = None,
                  ) -> GapRoutingDecision:
    """If table_hint is present, run extract_from_table and pick the
    highest-confidence FamilyMatch. Otherwise → U3."""
    if not table_hint:
        return GapRoutingDecision(
            gap_ref=gap_ref, chosen_path="U3_residual",
            best_match=None,
            rationale="no table hint; routes to U3 free-form synthesis",
        )
    matches = extract_from_table(table_hint)
    best = max(matches, key=lambda m: m.match_confidence)
    if best.match_confidence > U1_HIGH_CONFIDENCE_THRESHOLD:
        path = "U1_compile"
    elif best.match_confidence < U1_LOW_CONFIDENCE_THRESHOLD:
        path = "U3_residual"
    else:
        path = "U1_with_U3_fallback"
    return GapRoutingDecision(
        gap_ref=gap_ref, chosen_path=path,
        best_match=best,
        rationale=(
            f"best match {best.family_kind} at conf="
            f"{best.match_confidence:.2f}; chose {path}"
        ),
    )

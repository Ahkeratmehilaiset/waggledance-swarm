# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.gap_to_solver_spec.

Per Prompt_1_Master §U1 ROUTING RULES:

- match_confidence > 0.8 (`U1_HIGH_CONFIDENCE_THRESHOLD`)
  → U1 declarative path (`U1_compile`)
- match_confidence < 0.5 (`U1_LOW_CONFIDENCE_THRESHOLD`)
  → U3 free-form synthesis (`U3_residual`)
- 0.5 ≤ confidence ≤ 0.8 → U1_with_U3_fallback

The router decides whether each gap signal goes through the
deterministic U1 compiler (cheap, auditable, no LLM) or the U3
residual path (LLM-driven, expensive, used only when U1 cannot match).

A regression here can route gaps to the wrong path silently:
expensive LLM synthesis for trivial gaps, or a bad U1 compile when
the match was too weak. Direct test coverage on this file was zero
before this PR.

Pinned invariants:

- No `table_hint` → always `U3_residual`.
- With `table_hint`, route picks the highest-confidence FamilyMatch
  and applies the threshold map (>0.8 / <0.5 / between).
- The chosen path strings are exactly:
  `U1_compile`, `U3_residual`, `U1_with_U3_fallback`.
- `to_dict` round-trips fields including a serialised best_match.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from waggledance.core.solver_synthesis import (
    U1_HIGH_CONFIDENCE_THRESHOLD,
    U1_LOW_CONFIDENCE_THRESHOLD,
)
from waggledance.core.solver_synthesis.bulk_rule_extractor import FamilyMatch
from waggledance.core.solver_synthesis.gap_to_solver_spec import (
    GapRoutingDecision,
    route_gap,
)


# --- helpers --------------------------------------------------------

def _match(confidence: float, family_kind: str = "scalar_unit_conversion") -> FamilyMatch:
    return FamilyMatch(
        family_kind=family_kind,
        match_confidence=confidence,
        extracted_spec={"factor": 1.0},
        rationale=f"test match at {confidence}",
    )


# --- no table_hint → U3_residual ------------------------------------

def test_route_gap_with_no_table_hint_goes_to_U3_residual():
    decision = route_gap(gap_ref="gap_x", table_hint=None)
    assert decision.chosen_path == "U3_residual"
    assert decision.best_match is None
    assert "no table hint" in decision.rationale


def test_route_gap_with_empty_table_hint_dict_also_goes_to_U3():
    """Empty dict is falsy → same as None."""
    decision = route_gap(gap_ref="gap_x", table_hint={})
    assert decision.chosen_path == "U3_residual"
    assert decision.best_match is None


# --- threshold routing (with mocked extract_from_table) -------------

def test_route_gap_high_confidence_routes_to_U1_compile():
    """match_confidence > 0.8 → U1_compile."""
    high_match = _match(0.95)
    with patch(
        "waggledance.core.solver_synthesis.gap_to_solver_spec.extract_from_table",
        return_value=[high_match],
    ):
        decision = route_gap(gap_ref="gap_x", table_hint={"some": "table"})
    assert decision.chosen_path == "U1_compile"
    assert decision.best_match is high_match


def test_route_gap_low_confidence_routes_to_U3_residual():
    """match_confidence < 0.5 → U3_residual."""
    low_match = _match(0.30)
    with patch(
        "waggledance.core.solver_synthesis.gap_to_solver_spec.extract_from_table",
        return_value=[low_match],
    ):
        decision = route_gap(gap_ref="gap_x", table_hint={"some": "table"})
    assert decision.chosen_path == "U3_residual"
    assert decision.best_match is low_match


def test_route_gap_mid_confidence_routes_to_U1_with_U3_fallback():
    """0.5 ≤ confidence ≤ 0.8 → U1_with_U3_fallback."""
    mid_match = _match(0.65)
    with patch(
        "waggledance.core.solver_synthesis.gap_to_solver_spec.extract_from_table",
        return_value=[mid_match],
    ):
        decision = route_gap(gap_ref="gap_x", table_hint={"some": "table"})
    assert decision.chosen_path == "U1_with_U3_fallback"


def test_route_gap_at_high_threshold_boundary_is_strictly_greater():
    """The high threshold check is `>`, not `>=`. Confidence exactly
    at U1_HIGH_CONFIDENCE_THRESHOLD does NOT promote to U1_compile —
    it falls into U1_with_U3_fallback."""
    boundary_match = _match(U1_HIGH_CONFIDENCE_THRESHOLD)
    with patch(
        "waggledance.core.solver_synthesis.gap_to_solver_spec.extract_from_table",
        return_value=[boundary_match],
    ):
        decision = route_gap(gap_ref="gap_x", table_hint={"some": "table"})
    assert decision.chosen_path == "U1_with_U3_fallback"


def test_route_gap_at_low_threshold_boundary_is_strictly_less():
    """The low threshold check is `<`, not `<=`. Confidence exactly
    at U1_LOW_CONFIDENCE_THRESHOLD does NOT route to U3_residual —
    it falls into U1_with_U3_fallback."""
    boundary_match = _match(U1_LOW_CONFIDENCE_THRESHOLD)
    with patch(
        "waggledance.core.solver_synthesis.gap_to_solver_spec.extract_from_table",
        return_value=[boundary_match],
    ):
        decision = route_gap(gap_ref="gap_x", table_hint={"some": "table"})
    assert decision.chosen_path == "U1_with_U3_fallback"


# --- best_match selection picks max confidence ----------------------

def test_route_gap_picks_highest_confidence_match_among_candidates():
    matches = [
        _match(0.30, "lookup_table"),
        _match(0.95, "scalar_unit_conversion"),
        _match(0.60, "threshold_rule"),
    ]
    with patch(
        "waggledance.core.solver_synthesis.gap_to_solver_spec.extract_from_table",
        return_value=matches,
    ):
        decision = route_gap(gap_ref="gap_x", table_hint={"some": "table"})
    assert decision.best_match.match_confidence == pytest.approx(0.95)
    assert decision.best_match.family_kind == "scalar_unit_conversion"
    assert decision.chosen_path == "U1_compile"


# --- to_dict round-trip ---------------------------------------------

def test_decision_to_dict_serialises_best_match_block():
    high_match = _match(0.95, "lookup_table")
    with patch(
        "waggledance.core.solver_synthesis.gap_to_solver_spec.extract_from_table",
        return_value=[high_match],
    ):
        decision = route_gap(gap_ref="gap_x", table_hint={"any": "v"})
    d = decision.to_dict()
    assert d["gap_ref"] == "gap_x"
    assert d["chosen_path"] == "U1_compile"
    assert d["best_match"] is not None
    assert d["best_match"]["family_kind"] == "lookup_table"


def test_decision_to_dict_emits_none_best_match_when_no_table_hint():
    decision = route_gap(gap_ref="gap_x", table_hint=None)
    d = decision.to_dict()
    assert d["best_match"] is None

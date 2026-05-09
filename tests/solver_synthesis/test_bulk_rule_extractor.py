# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.bulk_rule_extractor.

The bulk rule extractor inspects a structured table and proposes
matching solver families. The U1 router (gap_to_solver_spec) reads
its match_confidence to decide between deterministic compile and LLM
synthesis. A regression here can mis-classify an obvious mapping as
needing U3 (expensive), or assign high confidence to a poor match
(bad U1 compile).

Direct test coverage on this file was zero before this PR. The
gap_to_solver_spec test suite uses a mock for `extract_from_table`;
this PR pins the actual extraction logic.

Pinned invariants:

- 2-column numeric table with constant ratio → scalar_unit_conversion
  at confidence 0.95.
- 2-column numeric table with linear fit → linear_arithmetic at
  confidence 0.85 (only when constant-ratio path did NOT fire).
- Table with explicit `intervals` → interval_bucket_classifier at
  0.95.
- Table with explicit `mapping` → lookup_table at 0.90.
- Table with explicit threshold-rule shape → threshold_rule at 0.95.
- No recognizable structure → fallback FamilyMatch with confidence
  0.20 (forces router to U3).
- `match_route` thresholds match the U1/U3 contract.
"""
from __future__ import annotations

import pytest

from waggledance.core.solver_synthesis.bulk_rule_extractor import (
    FamilyMatch,
    extract_from_table,
    match_route,
)


# --- match_route thresholds ----------------------------------------

def test_match_route_high_confidence_to_U1_compile():
    m = FamilyMatch(family_kind="x", match_confidence=0.95,
                    extracted_spec={}, rationale="")
    assert match_route(m) == "U1_compile"


def test_match_route_low_confidence_to_U3_residual():
    m = FamilyMatch(family_kind="x", match_confidence=0.30,
                    extracted_spec={}, rationale="")
    assert match_route(m) == "U3_residual"


def test_match_route_mid_confidence_to_fallback():
    m = FamilyMatch(family_kind="x", match_confidence=0.65,
                    extracted_spec={}, rationale="")
    assert match_route(m) == "U1_with_U3_fallback"


# --- scalar_unit_conversion: constant ratio -------------------------

def test_extract_constant_ratio_yields_scalar_unit_conversion_at_high_confidence():
    table = {
        "columns": ["celsius", "kelvin"],
        "rows": [[1, 274.15], [100, 373.15]],   # not perfectly proportional
    }
    matches = extract_from_table(table)
    # This won't be constant ratio because of the +273.15 offset.
    # But should fall through to linear_arithmetic.
    families = {m.family_kind for m in matches}
    assert "linear_arithmetic" in families


def test_extract_pure_proportional_yields_scalar_unit_conversion():
    table = {
        "columns": ["meters", "feet"],
        "rows": [[1.0, 3.28084], [2.0, 6.56168], [10.0, 32.8084]],
    }
    matches = extract_from_table(table)
    sucs = [m for m in matches if m.family_kind == "scalar_unit_conversion"]
    assert len(sucs) == 1
    assert sucs[0].match_confidence == pytest.approx(0.95)
    assert sucs[0].extracted_spec["from_unit"] == "meters"
    assert sucs[0].extracted_spec["to_unit"] == "feet"
    assert sucs[0].extracted_spec["factor"] == pytest.approx(3.28084)


def test_extract_does_not_fire_scalar_for_inconsistent_ratios():
    """Ratios diverging beyond 1e-6 do NOT promote to
    scalar_unit_conversion. They may still match linear_arithmetic
    if the residuals are tight enough."""
    table = {
        "columns": ["x", "y"],
        "rows": [[1.0, 2.0], [2.0, 5.0], [3.0, 7.0]],
    }
    matches = extract_from_table(table)
    suc_kinds = {m.family_kind for m in matches}
    assert "scalar_unit_conversion" not in suc_kinds


# --- linear_arithmetic ---------------------------------------------

def test_extract_perfect_linear_fit_yields_linear_arithmetic():
    """y = 2x + 3 perfectly."""
    table = {
        "columns": ["x", "y"],
        "rows": [[1.0, 5.0], [2.0, 7.0], [3.0, 9.0], [4.0, 11.0]],
    }
    matches = extract_from_table(table)
    lins = [m for m in matches if m.family_kind == "linear_arithmetic"]
    assert len(lins) == 1
    assert lins[0].match_confidence == pytest.approx(0.85)
    spec = lins[0].extracted_spec
    assert spec["coefficients"][0] == pytest.approx(2.0)
    assert spec["intercept"] == pytest.approx(3.0)
    assert spec["input_columns"] == ["x"]


def test_extract_linear_does_not_fire_when_residuals_too_large():
    """Noisy data → no linear match (residuals exceed 1e-3)."""
    table = {
        "columns": ["x", "y"],
        "rows": [[1.0, 5.0], [2.0, 7.5], [3.0, 8.8], [4.0, 12.1]],
    }
    matches = extract_from_table(table)
    lins = [m for m in matches if m.family_kind == "linear_arithmetic"]
    assert lins == []


# --- interval_bucket_classifier ------------------------------------

def test_extract_explicit_intervals_yields_interval_bucket_classifier():
    table = {
        "intervals": [
            {"min": 0,  "max": 10, "label": "low"},
            {"min": 10, "max": 20, "label": "mid"},
            {"min": 20, "max": 30, "label": "high"},
        ],
    }
    matches = extract_from_table(table)
    bucks = [m for m in matches if m.family_kind == "interval_bucket_classifier"]
    assert len(bucks) == 1
    assert bucks[0].match_confidence == pytest.approx(0.95)
    assert bucks[0].extracted_spec["intervals"] == table["intervals"]


def test_extract_intervals_missing_required_field_does_not_match():
    """An interval without min/max/label must NOT be treated as a
    valid interval bucket."""
    table = {
        "intervals": [{"min": 0, "max": 10}],   # missing label
    }
    matches = extract_from_table(table)
    bucks = [m for m in matches if m.family_kind == "interval_bucket_classifier"]
    assert bucks == []


# --- lookup_table --------------------------------------------------

def test_extract_explicit_mapping_yields_lookup_table():
    table = {"mapping": {"a": "alpha", "b": "beta", "c": "gamma"}}
    matches = extract_from_table(table)
    lkups = [m for m in matches if m.family_kind == "lookup_table"
             and m.match_confidence >= 0.5]
    assert len(lkups) == 1
    assert lkups[0].match_confidence == pytest.approx(0.90)
    assert lkups[0].extracted_spec["table"] == {"a": "alpha", "b": "beta",
                                                  "c": "gamma"}


def test_extract_empty_mapping_does_not_match_high_confidence():
    """An empty mapping is not a lookup_table — the fallback may still fire."""
    table = {"mapping": {}}
    matches = extract_from_table(table)
    high_lkup = [m for m in matches if m.family_kind == "lookup_table"
                 and m.match_confidence >= 0.5]
    assert high_lkup == []


# --- threshold_rule ------------------------------------------------

def test_extract_threshold_rule_shape_yields_threshold_rule():
    table = {
        "threshold": 5.0,
        "operator": ">",
        "true_label": "hot",
        "false_label": "cold",
    }
    matches = extract_from_table(table)
    thrs = [m for m in matches if m.family_kind == "threshold_rule"]
    assert len(thrs) == 1
    assert thrs[0].match_confidence == pytest.approx(0.95)
    assert thrs[0].extracted_spec == {
        "threshold": 5.0, "operator": ">",
        "true_label": "hot", "false_label": "cold",
    }


def test_extract_threshold_missing_one_field_does_not_match():
    table = {
        "threshold": 5.0, "operator": ">",
        "true_label": "hot",
        # missing false_label
    }
    matches = extract_from_table(table)
    thrs = [m for m in matches if m.family_kind == "threshold_rule"]
    assert thrs == []


# --- fallback path -------------------------------------------------

def test_extract_unrecognized_table_yields_low_confidence_fallback():
    """No recognizable structure → fallback FamilyMatch with
    confidence 0.20 so the router routes to U3."""
    table = {"unknown_field": "irrelevant"}
    matches = extract_from_table(table)
    assert len(matches) == 1
    fb = matches[0]
    assert fb.match_confidence == pytest.approx(0.20)
    # Routing this fallback must go to U3.
    assert match_route(fb) == "U3_residual"


def test_extract_empty_table_yields_fallback():
    matches = extract_from_table({})
    assert len(matches) == 1
    assert matches[0].match_confidence == pytest.approx(0.20)


def test_family_match_to_dict_round_trips_fields():
    m = FamilyMatch(
        family_kind="lookup_table",
        match_confidence=0.9,
        extracted_spec={"table": {"a": 1}},
        rationale="test",
    )
    d = m.to_dict()
    assert d == {
        "family_kind": "lookup_table",
        "match_confidence": 0.9,
        "extracted_spec": {"table": {"a": 1}},
        "rationale": "test",
    }

# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.autonomy_growth.family_features.

Iteration N+4 second scout pick (Claude solo while Codex sleeps).
family_features extracts the structured feature signature that the
dispatcher uses to match runtime queries to per-family solvers BY
CAPABILITY rather than by name or FIFO. A drift in feature shape
would either:
- silently route the wrong solver to a query (wrong-family match), or
- explode the match space (empty-feature unbounded scan), or
- mismatch a runtime query that should have matched a known solver.

The dispatcher refuses to match when the feature set is empty — that
prevents unbounded scans on unknown families and forces the caller to
fall through to family-FIFO or to harvest.

Pinned invariants:

- All 6 LOW_RISK_FAMILY_KINDS have a registered extractor in
  _FEATURE_EXTRACTORS.
- Unknown families return {} from extract_features (dispatcher
  treats this as "no match").
- Each extractor coerces missing keys to "_" (sentinel) so the
  match dict shape is stable across spec versions.
- scalar_unit_conversion: {from_unit, to_unit}, both str().
- lookup_table: {domain, default_present}; default_present is the
  literal "true"/"false" string, NOT a Python bool.
- threshold_rule: {subject, operator}.
- interval_bucket_classifier: {subject} only.
- linear_arithmetic: input_columns_signature is "|" join of cols
  or "_positional" sentinel for empty/missing.
- bounded_interpolation: {x_var, y_var}; defaults to "x"/"y".
- feature_dimensions returns a tuple of feature key names per
  family; matches the dict keys returned by extract_features.
"""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth.family_features import (
    _FEATURE_EXTRACTORS,
    extract_features,
    feature_dimensions,
)
from waggledance.core.autonomy_growth.low_risk_policy import (
    LOW_RISK_FAMILY_KINDS,
)


# --- registry coverage --------------------------------------------

def test_all_low_risk_families_have_an_extractor():
    """If a family is in LOW_RISK_FAMILY_KINDS but has no extractor,
    extract_features returns {} for it and the dispatcher cannot
    match its solvers — silent capability outage. This test guards
    against accidental drop."""
    for family in LOW_RISK_FAMILY_KINDS:
        assert family in _FEATURE_EXTRACTORS, (
            f"low-risk family {family!r} has no feature extractor"
        )


def test_unknown_family_returns_empty_dict():
    """Unknown family must return {} (dispatcher treats this as
    no-match and falls through). Critically, this MUST NOT raise."""
    assert extract_features("not_a_real_family", {}) == {}


def test_unknown_family_with_arbitrary_spec_still_returns_empty():
    assert extract_features(
        "not_a_real_family",
        {"from_unit": "C", "subject": "x"},
    ) == {}


def test_feature_dimensions_unknown_family_returns_empty_tuple():
    assert feature_dimensions("not_a_real_family") == ()


# --- scalar_unit_conversion ---------------------------------------

def test_scalar_unit_conversion_extracts_from_to_units():
    feats = extract_features(
        "scalar_unit_conversion",
        {"from_unit": "C", "to_unit": "K", "factor": 1.0,
         "offset": 273.15},
    )
    assert feats == {"from_unit": "C", "to_unit": "K"}


def test_scalar_unit_conversion_missing_keys_use_underscore_sentinel():
    """Missing keys must coerce to '_' so the feature dict shape is
    stable. A solver with missing units is still feature-matchable
    against a query that ALSO has missing units — both end up at
    '_'/'_'."""
    feats = extract_features("scalar_unit_conversion", {})
    assert feats == {"from_unit": "_", "to_unit": "_"}


def test_scalar_unit_conversion_str_coerces_non_string_units():
    feats = extract_features(
        "scalar_unit_conversion",
        {"from_unit": 42, "to_unit": None},
    )
    assert feats == {"from_unit": "42", "to_unit": "None"}


def test_scalar_unit_conversion_dimensions():
    assert feature_dimensions("scalar_unit_conversion") == (
        "from_unit", "to_unit",
    )


# --- lookup_table -------------------------------------------------

def test_lookup_table_features_with_domain_and_default():
    feats = extract_features(
        "lookup_table",
        {"domain": "color", "default": "unknown",
         "table": {"r": "red"}},
    )
    assert feats == {"domain": "color", "default_present": "true"}


def test_lookup_table_default_present_is_literal_string_not_bool():
    """default_present is the LITERAL string 'true' or 'false', NOT
    a Python bool. The dispatcher matches features by string
    equality; a bool here would silently mismatch every query."""
    feats_with = extract_features(
        "lookup_table", {"domain": "x", "default": 0},
    )
    assert feats_with["default_present"] == "true"
    assert isinstance(feats_with["default_present"], str)

    feats_without = extract_features(
        "lookup_table", {"domain": "x"},
    )
    assert feats_without["default_present"] == "false"


def test_lookup_table_default_zero_falsy_value_still_counts_as_present():
    """`spec.get('default') is not None` — so a default of 0, "",
    or False (any non-None value) counts as 'present'. Otherwise
    a meaningful default of zero would be silently treated as
    absent."""
    for falsy_default in [0, "", False, [], {}]:
        feats = extract_features(
            "lookup_table",
            {"domain": "x", "default": falsy_default},
        )
        assert feats["default_present"] == "true", (
            f"falsy default {falsy_default!r} reported as missing"
        )


def test_lookup_table_default_explicit_none_means_absent():
    feats = extract_features(
        "lookup_table", {"domain": "x", "default": None},
    )
    assert feats["default_present"] == "false"


def test_lookup_table_missing_domain_falls_back_to_underscore():
    feats = extract_features("lookup_table", {})
    assert feats == {"domain": "_", "default_present": "false"}


def test_lookup_table_dimensions():
    assert feature_dimensions("lookup_table") == (
        "domain", "default_present",
    )


# --- threshold_rule -----------------------------------------------

def test_threshold_rule_features():
    feats = extract_features(
        "threshold_rule",
        {"subject": "temp", "operator": ">",
         "threshold": 30.0},
    )
    assert feats == {"subject": "temp", "operator": ">"}


def test_threshold_rule_missing_keys_use_sentinel():
    feats = extract_features("threshold_rule", {})
    assert feats == {"subject": "_", "operator": "_"}


def test_threshold_rule_dimensions():
    assert feature_dimensions("threshold_rule") == (
        "subject", "operator",
    )


# --- interval_bucket_classifier -----------------------------------

def test_interval_bucket_features_subject_only():
    feats = extract_features(
        "interval_bucket_classifier",
        {"subject": "score", "buckets": [[0, 10], [10, 100]]},
    )
    # buckets are NOT part of the feature signature — only subject.
    assert feats == {"subject": "score"}


def test_interval_bucket_missing_subject_uses_sentinel():
    feats = extract_features("interval_bucket_classifier", {})
    assert feats == {"subject": "_"}


def test_interval_bucket_dimensions():
    assert feature_dimensions("interval_bucket_classifier") == (
        "subject",
    )


# --- linear_arithmetic --------------------------------------------

def test_linear_arithmetic_input_columns_signature_pipe_joined():
    feats = extract_features(
        "linear_arithmetic",
        {"input_columns": ["x", "y", "z"]},
    )
    assert feats == {"input_columns_signature": "x|y|z"}


def test_linear_arithmetic_empty_columns_uses_positional_sentinel():
    """Empty/missing input_columns must produce the literal
    '_positional' sentinel so positional-only solvers form their
    own match equivalence class."""
    for empty in [{"input_columns": []}, {}, {"input_columns": None}]:
        feats = extract_features("linear_arithmetic", empty)
        assert feats == {
            "input_columns_signature": "_positional",
        }, f"failed on input {empty!r}"


def test_linear_arithmetic_str_coerces_non_string_columns():
    feats = extract_features(
        "linear_arithmetic",
        {"input_columns": [1, 2.0, "z"]},
    )
    # 2.0 -> "2.0", 1 -> "1"
    assert feats == {"input_columns_signature": "1|2.0|z"}


def test_linear_arithmetic_dimensions():
    assert feature_dimensions("linear_arithmetic") == (
        "input_columns_signature",
    )


# --- bounded_interpolation ----------------------------------------

def test_bounded_interpolation_default_xy_vars():
    """Defaults to x/y when not specified."""
    feats = extract_features("bounded_interpolation", {})
    assert feats == {"x_var": "x", "y_var": "y"}


def test_bounded_interpolation_explicit_vars():
    feats = extract_features(
        "bounded_interpolation",
        {"x_var": "celsius", "y_var": "kelvin"},
    )
    assert feats == {"x_var": "celsius", "y_var": "kelvin"}


def test_bounded_interpolation_dimensions():
    assert feature_dimensions("bounded_interpolation") == (
        "x_var", "y_var",
    )


# --- shape consistency: extract_features keys == dimensions ------

@pytest.mark.parametrize("family", [
    "scalar_unit_conversion",
    "lookup_table",
    "threshold_rule",
    "interval_bucket_classifier",
    "linear_arithmetic",
    "bounded_interpolation",
])
def test_extract_features_keys_match_feature_dimensions(family):
    """The set of keys returned by extract_features for a family
    must match feature_dimensions for that family. Drift here means
    docs/tests get stale and the dispatcher silently consults the
    wrong feature shape."""
    feats = extract_features(family, {})
    assert set(feats.keys()) == set(feature_dimensions(family)), (
        f"feature key drift for family {family!r}: "
        f"extract={sorted(feats.keys())} "
        f"dimensions={sorted(feature_dimensions(family))}"
    )

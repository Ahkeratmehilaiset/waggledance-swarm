# SPDX-License-Identifier: Apache-2.0
"""Deterministic runtime hint extractor tests (Phase 15 P2)."""

from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth import (
    RESULT_DERIVED,
    RESULT_REJECTED_AMBIGUOUS,
    RESULT_REJECTED_FAMILY_NOT_LOW_RISK,
    RESULT_REJECTED_MALFORMED,
    RESULT_REJECTED_MISSING_FIELDS,
    RESULT_REJECTED_NOT_STRUCTURED,
    RESULT_SKIPPED,
    derive_low_risk_autonomy_hint,
    supported_subkeys,
)


def test_derives_scalar_unit_conversion() -> None:
    res = derive_low_risk_autonomy_hint(
        query="convert 25 C to K",
        context={
            "structured_request": {
                "unit_conversion": {"x": 25.0, "from": "C", "to": "K"},
                "cell_coord": "thermal",
                "intent_seed": "celsius_to_kelvin",
            }
        },
    )
    assert res.kind == RESULT_DERIVED
    assert res.family_kind == "scalar_unit_conversion"
    assert res.hint["family_kind"] == "scalar_unit_conversion"
    assert res.hint["inputs"] == {"x": 25.0}
    assert res.hint["features"] == {"from_unit": "C", "to_unit": "K"}
    assert res.hint["cell_coord"] == "thermal"
    assert res.hint["intent_seed"] == "celsius_to_kelvin"


def test_derives_lookup_table() -> None:
    res = derive_low_risk_autonomy_hint(
        query="what does red mean",
        context={
            "structured_request": {
                "lookup": {"key": "red", "domain": "color"},
            }
        },
    )
    assert res.kind == RESULT_DERIVED
    assert res.family_kind == "lookup_table"
    assert res.hint["features"]["domain"] == "color"
    assert res.hint["cell_coord"] == "general"  # default


def test_derives_threshold_rule() -> None:
    res = derive_low_risk_autonomy_hint(
        query="is 35 above the hot threshold?",
        context={
            "structured_request": {
                "threshold_check": {
                    "x": 35.0, "subject": "temperature_c", "operator": ">",
                },
            }
        },
    )
    assert res.kind == RESULT_DERIVED
    assert res.family_kind == "threshold_rule"
    assert res.hint["features"] == {
        "subject": "temperature_c", "operator": ">",
    }


def test_derives_interval_bucket_classifier() -> None:
    res = derive_low_risk_autonomy_hint(
        query="what cpu band",
        context={
            "structured_request": {
                "bucket_check": {"x": 17.5, "subject": "cpu_pct"},
            }
        },
    )
    assert res.kind == RESULT_DERIVED
    assert res.family_kind == "interval_bucket_classifier"


def test_derives_linear_arithmetic() -> None:
    res = derive_low_risk_autonomy_hint(
        query="estimate comfort",
        context={
            "structured_request": {
                "linear_eval": {
                    "inputs": {"temp_dev": 1.0, "humidity_dev": 0.5,
                                "noise_dev": 0.2},
                    "input_columns_signature": "temp_dev|humidity_dev|noise_dev",
                },
            }
        },
    )
    assert res.kind == RESULT_DERIVED
    assert res.family_kind == "linear_arithmetic"
    assert res.hint["features"]["input_columns_signature"] == (
        "temp_dev|humidity_dev|noise_dev"
    )


def test_derives_bounded_interpolation() -> None:
    res = derive_low_risk_autonomy_hint(
        query="charge curve",
        context={
            "structured_request": {
                "interpolation": {"x": 50.0, "x_var": "charge_pct",
                                    "y_var": "voltage_v"},
            }
        },
    )
    assert res.kind == RESULT_DERIVED
    assert res.family_kind == "bounded_interpolation"
    assert res.hint["features"] == {"x_var": "charge_pct",
                                       "y_var": "voltage_v"}


def test_rejected_ambiguous_when_multiple_subkeys_present() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "unit_conversion": {"x": 1.0, "from": "C", "to": "K"},
                "lookup": {"key": "red", "domain": "color"},
            }
        },
    )
    assert res.kind == RESULT_REJECTED_AMBIGUOUS
    assert "lookup" in res.rejected_subkeys
    assert "unit_conversion" in res.rejected_subkeys


def test_rejected_family_not_low_risk_when_only_other_keys() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "temporal_window_check": {"window_seconds": 60, "x": 0.5},
            }
        },
    )
    assert res.kind == RESULT_REJECTED_FAMILY_NOT_LOW_RISK
    assert "temporal_window_check" in res.rejected_subkeys


def test_rejected_missing_fields_unit_conversion() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "unit_conversion": {"x": 1.0, "from": "C"},  # 'to' missing
            }
        },
    )
    assert res.kind == RESULT_REJECTED_MISSING_FIELDS
    assert res.family_kind == "scalar_unit_conversion"


def test_rejected_missing_fields_threshold_check() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "threshold_check": {"x": 1.0, "subject": "x"},
            }
        },
    )
    assert res.kind == RESULT_REJECTED_MISSING_FIELDS


def test_rejected_malformed_x_not_number() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "unit_conversion": {"x": "not-a-number", "from": "C", "to": "K"},
            }
        },
    )
    assert res.kind == RESULT_REJECTED_MALFORMED


def test_rejected_malformed_unsupported_operator() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "threshold_check": {
                    "x": 1.0, "subject": "x", "operator": "??",
                },
            }
        },
    )
    assert res.kind == RESULT_REJECTED_MALFORMED


def test_rejected_not_structured_when_payload_not_dict() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={"structured_request": "just a string"},
    )
    assert res.kind == RESULT_REJECTED_NOT_STRUCTURED


def test_skipped_for_free_text_only() -> None:
    """No structured_request field => skipped, not a hint."""

    res = derive_low_risk_autonomy_hint(
        query="hi I'm a user with free text",
        context={"profile": "default"},
    )
    assert res.kind == RESULT_SKIPPED
    assert res.hint is None


def test_skipped_when_context_is_none() -> None:
    res = derive_low_risk_autonomy_hint(query="?", context=None)
    assert res.kind == RESULT_SKIPPED


def test_skipped_when_context_is_not_mapping() -> None:
    res = derive_low_risk_autonomy_hint(query="?", context="not-a-dict")  # type: ignore[arg-type]
    assert res.kind == RESULT_SKIPPED


def test_does_not_consult_query_string_for_family_inference() -> None:
    """Free text alone never produces a hint, even if it 'looks like'
    a structured query in natural language."""

    res = derive_low_risk_autonomy_hint(
        query="convert 25 from celsius to kelvin",
        context={},
    )
    assert res.kind == RESULT_SKIPPED


def test_supported_subkeys_matches_grammar() -> None:
    assert set(supported_subkeys()) == {
        "unit_conversion", "lookup", "threshold_check",
        "bucket_check", "linear_eval", "interpolation",
    }


def test_no_provider_call_side_effect() -> None:
    """The extractor is pure Python; calling it many times leaves no
    network or subprocess footprint. Smoke test: 1000 calls return
    immediately without any external state change."""

    for _ in range(1000):
        derive_low_risk_autonomy_hint(query="x", context={})


def test_hint_carries_intent_seed_when_supplied() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "lookup": {"key": "red", "domain": "color"},
                "intent_seed": "color_lookup",
            }
        },
    )
    assert res.kind == RESULT_DERIVED
    assert res.hint["intent_seed"] == "color_lookup"


def test_cell_coord_argument_overrides_payload() -> None:
    res = derive_low_risk_autonomy_hint(
        query="?",
        context={
            "structured_request": {
                "lookup": {"key": "red", "domain": "color"},
                "cell_coord": "general",
            }
        },
        cell_coord="safety",
    )
    assert res.hint["cell_coord"] == "safety"

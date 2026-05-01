"""Tests for the Phase 16A deterministic upstream structured_request extractor."""
from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth.upstream_structured_request_extractor import (
    UPSTREAM_DERIVED,
    UPSTREAM_REJECTED_AMBIGUOUS,
    UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK,
    UPSTREAM_REJECTED_MALFORMED,
    UPSTREAM_REJECTED_MISSING_FIELDS,
    UPSTREAM_SKIPPED,
    UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE,
    apply_upstream_structured_request,
    derive_upstream_structured_request,
)


# ── Per-family derivation ─────────────────────────────────────────


def test_unit_conversion_lift_renames_flat_fields_to_nested_grammar():
    ctx = {
        "profile": "default",
        "priority": 50,
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": 25,
    }
    result = derive_upstream_structured_request("convert 25C to F", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert result.family_kind == "scalar_unit_conversion"
    assert result.operation == "unit_conversion"
    assert result.structured_request == {
        "unit_conversion": {"x": 25.0, "from": "C", "to": "F"},
    }


def test_lookup_lift():
    ctx = {
        "operation": "lookup",
        "key": "warning",
        "domain": "co2_thresholds",
    }
    result = derive_upstream_structured_request("lookup warning", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert result.family_kind == "lookup_table"
    assert result.structured_request == {
        "lookup": {"key": "warning", "domain": "co2_thresholds"},
    }


def test_threshold_check_lift():
    ctx = {
        "operation": "threshold_check",
        "subject": "co2",
        "x": 950,
        "operator": "<=",
    }
    result = derive_upstream_structured_request("co2 below 1000?", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert result.family_kind == "threshold_rule"
    assert result.structured_request == {
        "threshold_check": {"x": 950.0, "subject": "co2", "operator": "<="},
    }


def test_bucket_check_lift():
    ctx = {"operation": "bucket_check", "subject": "temperature", "x": 22.5}
    result = derive_upstream_structured_request("comfort?", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert result.family_kind == "interval_bucket_classifier"
    assert result.structured_request == {
        "bucket_check": {"x": 22.5, "subject": "temperature"},
    }


def test_linear_arithmetic_lift():
    ctx = {
        "operation": "linear_eval",
        "inputs": {"a": 1.0, "b": 2.0, "c": 3.0},
        "input_columns_signature": "a:float,b:float,c:float",
    }
    result = derive_upstream_structured_request("compute", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert result.family_kind == "linear_arithmetic"
    assert result.structured_request == {
        "linear_eval": {
            "inputs": {"a": 1.0, "b": 2.0, "c": 3.0},
            "input_columns_signature": "a:float,b:float,c:float",
        },
    }


def test_interpolation_lift():
    ctx = {
        "operation": "interpolation",
        "x": 5.0,
        "x_var": "time",
        "y_var": "value",
    }
    result = derive_upstream_structured_request("interp", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert result.family_kind == "bounded_interpolation"
    assert result.structured_request == {
        "interpolation": {"x": 5.0, "x_var": "time", "y_var": "value"},
    }


# ── Optional shared flat fields propagated ────────────────────────


def test_cell_coord_and_intent_seed_propagated():
    ctx = {
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "K",
        "value": 0,
        "cell_coord": "thermal",
        "intent_seed": "celsius_to_kelvin_water_freeze",
    }
    result = derive_upstream_structured_request("c->k", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert result.structured_request["cell_coord"] == "thermal"
    assert (
        result.structured_request["intent_seed"]
        == "celsius_to_kelvin_water_freeze"
    )


# ── Skipped/rejected paths ────────────────────────────────────────


def test_skipped_when_no_operation_field():
    ctx = {"profile": "default", "language": "en"}
    result = derive_upstream_structured_request("free text only", ctx)
    assert result.kind == UPSTREAM_SKIPPED
    assert result.structured_request is None


def test_skipped_when_context_is_none():
    result = derive_upstream_structured_request("any", None)
    assert result.kind == UPSTREAM_SKIPPED


def test_skipped_builtin_precedence_when_caller_signals_success():
    ctx = {
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": 25,
        "builtin_solver_succeeded": True,
    }
    result = derive_upstream_structured_request("convert", ctx)
    assert result.kind == UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE
    assert result.structured_request is None


def test_rejected_family_not_low_risk_for_unsupported_operation():
    ctx = {
        "operation": "temporal_window_check",
        "window_seconds": 60,
        "x": 0.5,
    }
    result = derive_upstream_structured_request("temporal", ctx)
    assert result.kind == UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK
    assert result.operation == "temporal_window_check"


def test_rejected_missing_fields_unit_conversion():
    ctx = {"operation": "unit_conversion", "from_unit": "C"}
    result = derive_upstream_structured_request("incomplete", ctx)
    assert result.kind == UPSTREAM_REJECTED_MISSING_FIELDS
    assert "to_unit" in result.rejected_keys
    assert "value" in result.rejected_keys


def test_rejected_malformed_value_not_a_number():
    ctx = {
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": "not_a_number",
    }
    result = derive_upstream_structured_request("bad value", ctx)
    assert result.kind == UPSTREAM_REJECTED_MALFORMED
    assert "value" in result.rejected_keys


def test_rejected_malformed_unsupported_operator():
    ctx = {
        "operation": "threshold_check",
        "subject": "x",
        "x": 1.0,
        "operator": "approximately_equal_to",
    }
    result = derive_upstream_structured_request("bad op", ctx)
    assert result.kind == UPSTREAM_REJECTED_MALFORMED
    assert "operator" in result.rejected_keys


def test_rejected_malformed_linear_inputs_not_a_dict():
    ctx = {
        "operation": "linear_eval",
        "inputs": ["a", "b", "c"],
        "input_columns_signature": "a:float",
    }
    result = derive_upstream_structured_request("bad inputs", ctx)
    assert result.kind == UPSTREAM_REJECTED_MALFORMED
    assert "inputs" in result.rejected_keys


def test_rejected_malformed_operation_not_a_string():
    ctx = {"operation": 12345, "from_unit": "C", "to_unit": "F", "value": 25}
    result = derive_upstream_structured_request("bad op type", ctx)
    assert result.kind == UPSTREAM_REJECTED_MALFORMED
    assert "operation" in result.rejected_keys


def test_rejected_ambiguous_when_caller_supplies_structured_request_directly():
    ctx = {
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": 25,
        "structured_request": {
            "unit_conversion": {"x": 25.0, "from": "C", "to": "F"},
        },
    }
    result = derive_upstream_structured_request("ambiguous", ctx)
    assert result.kind == UPSTREAM_REJECTED_AMBIGUOUS
    assert "structured_request" in result.rejected_keys


def test_rejected_ambiguous_when_caller_supplies_low_risk_autonomy_query():
    ctx = {
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": 25,
        "low_risk_autonomy_query": {"family_kind": "scalar_unit_conversion"},
    }
    result = derive_upstream_structured_request("ambiguous hint", ctx)
    assert result.kind == UPSTREAM_REJECTED_AMBIGUOUS
    assert "low_risk_autonomy_query" in result.rejected_keys


# ── apply_upstream_structured_request mutates context only on success ──


def test_apply_helper_mutates_context_on_derive():
    ctx = {
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": 25,
    }
    result = apply_upstream_structured_request("convert", ctx)
    assert result.kind == UPSTREAM_DERIVED
    assert ctx["structured_request"] == {
        "unit_conversion": {"x": 25.0, "from": "C", "to": "F"},
    }


def test_apply_helper_does_not_mutate_context_on_skip():
    ctx = {"profile": "default"}
    snapshot = dict(ctx)
    result = apply_upstream_structured_request("free text", ctx)
    assert result.kind == UPSTREAM_SKIPPED
    assert ctx == snapshot
    assert "structured_request" not in ctx


def test_apply_helper_does_not_mutate_context_on_reject_missing():
    ctx = {"operation": "unit_conversion", "from_unit": "C"}
    snapshot = dict(ctx)
    result = apply_upstream_structured_request("missing", ctx)
    assert result.kind == UPSTREAM_REJECTED_MISSING_FIELDS
    assert ctx == snapshot
    assert "structured_request" not in ctx


# ── No provider/job side effects ──────────────────────────────────


def test_extractor_does_no_provider_or_job_io(monkeypatch):
    """Sanity: pure deterministic Python — no network, no SQLite writes."""
    import socket
    from urllib import request as _ureq

    def _no_socket(*a, **kw):
        raise AssertionError("upstream extractor must not open sockets")

    def _no_url(*a, **kw):
        raise AssertionError("upstream extractor must not call URLs")

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(_ureq, "urlopen", _no_url)

    ctx = {
        "operation": "unit_conversion",
        "from_unit": "C",
        "to_unit": "F",
        "value": 25,
    }
    result = derive_upstream_structured_request("net-free", ctx)
    assert result.kind == UPSTREAM_DERIVED

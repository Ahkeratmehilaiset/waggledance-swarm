"""Tests for hybrid_router question-frame integration."""
from __future__ import annotations

from waggledance.core.reasoning.hybrid_router import (
    filter_by_question_frame, route_with_question_frame,
    _solver_supports_question_type, _comparator_unit_compatible,
)
from waggledance.core.reasoning.question_frame import (
    parse, QuestionFrame, Comparator, Negation,
)


# Sample solver specs (matching what's in axiom YAMLs)
HEATING_COST = {
    "primary_value": {"name": "daily_cost", "type": "number", "unit": "EUR"},
    "comparable_fields": [
        {"name": "daily_cost", "unit": "EUR"},
        {"name": "monthly_cost", "unit": "EUR"},
        {"name": "daily_kwh", "unit": "kWh"},
    ],
    "output_mode": "numeric",
}

HONEY_YIELD = {
    "primary_value": {"name": "season_honey_kg", "type": "number", "unit": "kg"},
    "comparable_fields": [
        {"name": "season_honey_kg", "unit": "kg"},
        {"name": "daily_honey_kg", "unit": "kg"},
    ],
    "output_mode": "numeric",
}

MTBF = {
    "primary_value": {"name": "recommended_pm_interval", "type": "number", "unit": "hours"},
    "comparable_fields": [{"name": "recommended_pm_interval", "unit": "hours"}],
    "output_mode": "numeric",
}

HYPOTHETICAL_DIAGNOSIS_SOLVER = {
    "primary_value": {"name": "diagnosis", "type": "string"},
    "comparable_fields": [],
    "output_mode": "diagnosis",
}

SOLVER_SPECS = {
    "heating_cost": HEATING_COST,
    "honey_yield": HONEY_YIELD,
    "mtbf_prediction": MTBF,
    "diagnose_pump": HYPOTHETICAL_DIAGNOSIS_SOLVER,
}


# ── _solver_supports_question_type ─────────────────────────────────


def test_numeric_output_supports_numeric_question():
    assert _solver_supports_question_type(HEATING_COST, "numeric") is True


def test_numeric_output_supports_boolean_question():
    assert _solver_supports_question_type(HEATING_COST, "boolean_comparison") is True


def test_numeric_output_does_not_support_diagnosis():
    assert _solver_supports_question_type(HEATING_COST, "diagnosis") is False


def test_diagnosis_solver_supports_diagnosis():
    assert _solver_supports_question_type(HYPOTHETICAL_DIAGNOSIS_SOLVER, "diagnosis") is True


def test_diagnosis_solver_does_not_support_numeric():
    assert _solver_supports_question_type(HYPOTHETICAL_DIAGNOSIS_SOLVER, "numeric") is False


# ── _comparator_unit_compatible ────────────────────────────────────


def test_no_comparator_means_compatible():
    assert _comparator_unit_compatible(HEATING_COST, None) is True


def test_eur_comparator_matches_heating_cost():
    comp = Comparator(op=">", threshold=50.0, unit="EUR")
    assert _comparator_unit_compatible(HEATING_COST, comp) is True


def test_kg_comparator_matches_honey_yield():
    comp = Comparator(op=">", threshold=15.0, unit="kg")
    assert _comparator_unit_compatible(HONEY_YIELD, comp) is True


def test_eur_comparator_does_not_match_honey_yield():
    comp = Comparator(op=">", threshold=15.0, unit="EUR")
    # honey_yield has kg only, not EUR
    assert _comparator_unit_compatible(HONEY_YIELD, comp) is False


def test_kwh_unit_matches_heating_cost_via_secondary_field():
    comp = Comparator(op=">", threshold=120, unit="kWh")
    assert _comparator_unit_compatible(HEATING_COST, comp) is True


def test_solver_with_no_comparable_fields_fails_unit_check():
    comp = Comparator(op=">", threshold=50, unit="EUR")
    assert _comparator_unit_compatible(HYPOTHETICAL_DIAGNOSIS_SOLVER, comp) is False


def test_comparator_without_unit_skips_check():
    comp = Comparator(op=">", threshold=100, unit=None)
    assert _comparator_unit_compatible(HEATING_COST, comp) is True


# ── filter_by_question_frame ───────────────────────────────────────


def test_filter_keeps_compatible():
    candidates = [
        {"canonical_solver_id": "heating_cost", "score": 0.9},
        {"canonical_solver_id": "honey_yield", "score": 0.8},
    ]
    frame = parse("paljonko lämmitys maksaa kuussa")
    # frame.desired_output = numeric
    out = filter_by_question_frame(candidates, SOLVER_SPECS, frame)
    assert len(out) == 2  # both numeric solvers compatible


def test_filter_rejects_diagnosis_query_to_numeric_solver():
    candidates = [
        {"canonical_solver_id": "heating_cost", "score": 0.85},
    ]
    frame = parse("miksi lämpöpumppu pitää kovaa ääntä")
    # frame.desired_output = diagnosis, but heating_cost is numeric
    out = filter_by_question_frame(candidates, SOLVER_SPECS, frame)
    assert out == []  # no compatible solver


def test_filter_keeps_diagnosis_solver_for_diagnosis_query():
    candidates = [
        {"canonical_solver_id": "diagnose_pump", "score": 0.7},
        {"canonical_solver_id": "heating_cost", "score": 0.6},
    ]
    frame = parse("miksi pumppu vuotaa")
    out = filter_by_question_frame(candidates, SOLVER_SPECS, frame)
    assert len(out) == 1
    assert out[0]["canonical_solver_id"] == "diagnose_pump"


def test_filter_rejects_eur_threshold_for_kg_solver():
    candidates = [
        {"canonical_solver_id": "honey_yield", "score": 0.85},
    ]
    frame = parse("onko hunajan tuotto yli 100 EUR")
    # boolean_comparison + EUR unit, but honey_yield only has kg
    out = filter_by_question_frame(candidates, SOLVER_SPECS, frame)
    assert out == []


def test_filter_keeps_eur_threshold_for_heating_cost():
    candidates = [
        {"canonical_solver_id": "heating_cost", "score": 0.9},
    ]
    frame = parse("onko lämmityskustannus yli 50 EUR")
    out = filter_by_question_frame(candidates, SOLVER_SPECS, frame)
    assert len(out) == 1


# ── route_with_question_frame ──────────────────────────────────────


def test_route_returns_off_domain_when_filter_empty():
    """The CRITICAL Phase D test — diagnosis query should be rejected."""

    def fake_retrieve(query):
        return [{"canonical_solver_id": "heating_cost", "score": 0.85}]

    result = route_with_question_frame(
        "miksi pumppu meluaa",
        fake_retrieve,
        SOLVER_SPECS,
    )
    assert result["chosen_solver"] is None
    assert result["rejected_off_domain"] is True
    assert result["candidates_before_filter"] == 1
    assert result["candidates_after_filter"] == 0


def test_route_returns_top_compatible_candidate():
    def fake_retrieve(query):
        return [
            {"canonical_solver_id": "diagnose_pump", "score": 0.6},
            {"canonical_solver_id": "heating_cost", "score": 0.9},
        ]

    # Numeric query → diagnose_pump filtered out, heating_cost kept
    result = route_with_question_frame(
        "paljonko lämmitys maksaa",
        fake_retrieve,
        SOLVER_SPECS,
    )
    assert result["chosen_solver"] == "diagnose_pump" or result["chosen_solver"] == "heating_cost"
    # Actually, our filter preserves order, and diagnose_pump.output_mode is "diagnosis"
    # which doesn't support "numeric" query → should be filtered out
    # → only heating_cost remains
    assert result["chosen_solver"] == "heating_cost"
    assert result["chosen_score"] == 0.9
    assert result["rejected_off_domain"] is False


def test_route_preserves_frame_in_output():
    def fake_retrieve(query):
        return [{"canonical_solver_id": "heating_cost", "score": 0.9}]

    result = route_with_question_frame(
        "onko lämmitys yli 50 EUR",
        fake_retrieve,
        SOLVER_SPECS,
    )
    assert result["frame"]["desired_output"] == "boolean_comparison"
    assert result["frame"]["comparator"]["op"] == ">"
    assert result["frame"]["comparator"]["threshold"] == 50.0
    assert result["frame"]["comparator"]["unit"] == "EUR"

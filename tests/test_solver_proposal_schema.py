"""Tests for schemas/solver_proposal.schema.json.

Validates the schema itself (meta-validation) and exercises a
handful of accepting / rejecting example proposals to prove the
shape matches x.txt Phase 4 requirements.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "solver_proposal.schema.json"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema) -> jsonschema.Draft7Validator:
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _good_proposal() -> dict:
    return {
        "proposal_id": "cot-2026-04-24-a",
        "cell_id": "thermal",
        "solver_name": "heatpump_cop_at_outdoor_temp",
        "purpose": "Estimate steady-state COP of an air-source heat pump given outdoor temperature and nominal COP curve.",
        "inputs": [
            {"name": "outdoor_c", "unit": "degC",
             "description": "Outdoor ambient temperature",
             "range": [-30, 45], "type": "number"},
            {"name": "nominal_cop", "unit": "ratio",
             "description": "Rated COP at 7°C outdoor",
             "range": [1.0, 8.0], "type": "ratio"},
        ],
        "outputs": [
            {"name": "cop_effective", "unit": "ratio",
             "description": "Effective COP at this outdoor temperature",
             "type": "ratio", "primary": True}
        ],
        "formula_or_algorithm": {
            "kind": "formula_chain",
            "steps": [
                {"name": "delta_t", "formula": "outdoor_c - 7",
                 "output_unit": "degC", "description": "temp offset from rating"},
                {"name": "cop_effective",
                 "formula": "max(1.0, nominal_cop + 0.03 * delta_t)",
                 "output_unit": "ratio",
                 "description": "linearized degradation, floored at 1.0"},
            ],
        },
        "assumptions": [
            "Air-source heat pump with linear COP degradation below rating",
            "Defrost cycles averaged in — do not model transient defrost",
        ],
        "invariants": [
            "cop_effective >= 1.0",
            "cop_effective <= nominal_cop + 2",
        ],
        "units": {"system": "SI", "notes": "Celsius for temperatures."},
        "tests": [
            {"name": "at_rating",
             "inputs": {"outdoor_c": 7, "nominal_cop": 4.0},
             "expected": 4.0, "tolerance": 1e-6},
            {"name": "cold_day",
             "inputs": {"outdoor_c": -20, "nominal_cop": 4.0},
             "expected": 3.19, "tolerance": 0.05},
        ],
        "expected_failure_modes": [
            {"condition": "outdoor_c below -25°C",
             "behavior": "returns 1.0 (floor), may underestimate real device damage"},
        ],
        "examples": [
            {"description": "nominal COP 4, 7°C outdoor",
             "inputs": {"outdoor_c": 7, "nominal_cop": 4.0},
             "expected": 4.0},
        ],
        "provenance_note": "teacher: claude-opus-4.7; manifest hash: sha256:abc…; rating curve from typical Daikin Altherma catalog.",
        "estimated_latency_ms": 0.1,
        "expected_coverage_lift": {
            "value": 0.05, "uncertainty": "medium",
            "rationale": "cell manifest showed 12 unresolved thermal queries mentioning cold-weather heat pump performance"
        },
        "risk_level": "low",
        "uncertainty_declaration": "Linear degradation is a first-order approximation; vendor-specific curves diverge below -15°C.",
        "tags": ["thermal", "heat-pump"],
    }


def test_schema_is_valid_draft7(schema):
    jsonschema.Draft7Validator.check_schema(schema)


def test_good_proposal_validates(validator):
    errors = list(validator.iter_errors(_good_proposal()))
    assert errors == [], errors


@pytest.mark.parametrize("missing", [
    "proposal_id", "cell_id", "solver_name", "purpose",
    "inputs", "outputs", "formula_or_algorithm", "assumptions",
    "invariants", "units", "tests", "expected_failure_modes",
    "examples", "provenance_note", "estimated_latency_ms",
    "expected_coverage_lift", "risk_level",
    "uncertainty_declaration",
])
def test_missing_required_field_rejected(validator, missing):
    bad = _good_proposal()
    bad.pop(missing)
    errors = list(validator.iter_errors(bad))
    assert errors, f"expected rejection when '{missing}' is missing"


def test_unknown_cell_rejected(validator):
    bad = _good_proposal()
    bad["cell_id"] = "fusion"  # not a real cell
    errors = list(validator.iter_errors(bad))
    assert errors


def test_bad_solver_name_rejected(validator):
    bad = _good_proposal()
    bad["solver_name"] = "BadName"  # must be lowercase snake
    assert list(validator.iter_errors(bad))


def test_risk_level_enum_enforced(validator):
    bad = _good_proposal()
    bad["risk_level"] = "critical"  # not in enum
    assert list(validator.iter_errors(bad))


def test_coverage_lift_value_bounds(validator):
    bad = _good_proposal()
    bad["expected_coverage_lift"]["value"] = 1.5  # > 1.0
    assert list(validator.iter_errors(bad))


def test_additional_top_level_fields_rejected(validator):
    bad = _good_proposal()
    bad["secret_extra"] = "snuck in"
    assert list(validator.iter_errors(bad))


def test_empty_inputs_rejected(validator):
    bad = _good_proposal()
    bad["inputs"] = []
    assert list(validator.iter_errors(bad))


def test_empty_tests_rejected(validator):
    bad = _good_proposal()
    bad["tests"] = []
    assert list(validator.iter_errors(bad))


def test_algorithm_kind_accepted(validator):
    good = _good_proposal()
    good["formula_or_algorithm"] = {
        "kind": "algorithm",
        "description": "Given a list of solvers, walk the adjacency graph and pick the first that matches the input unit.",
        "pseudo_code": "for s in candidates: if match(s, inputs): return s",
    }
    errors = list(validator.iter_errors(good))
    assert errors == [], errors


def test_table_lookup_kind_accepted(validator):
    good = _good_proposal()
    good["formula_or_algorithm"] = {
        "kind": "table_lookup",
        "lookup_key": "outdoor_c",
        "source": "ISO 15927-1 Table A.1",
        "description": "Standard weighted bin temperatures for Finland.",
    }
    errors = list(validator.iter_errors(good))
    assert errors == [], errors


def test_formula_or_algorithm_unknown_kind_rejected(validator):
    bad = _good_proposal()
    bad["formula_or_algorithm"] = {"kind": "vibe_check", "description": "feels right"}
    assert list(validator.iter_errors(bad))


def test_llm_dependency_optional_but_validated(validator):
    good = _good_proposal()
    good["llm_dependency"] = {"required": False}
    assert list(validator.iter_errors(good)) == []
    good["llm_dependency"] = {"required": True, "reason": "needs text summarization",
                              "fallback_only": True}
    assert list(validator.iter_errors(good)) == []


def test_proposal_is_json_and_yaml_roundtrip_safe():
    """The schema must accept documents that survive JSON and YAML
    serialization, so a teacher can return either format."""
    import yaml
    p = _good_proposal()
    reparsed = yaml.safe_load(yaml.safe_dump(p))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(reparsed)


def test_schema_has_no_secrets_no_absolute_paths():
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "WAGGLE_API_KEY" not in raw
    assert "gnt_" not in raw
    assert "C:\\" not in raw
    assert "U:\\" not in raw


def test_teacher_prompt_present_and_non_trivial():
    prompt = ROOT / "docs" / "prompts" / "cell_teacher_prompt.md"
    assert prompt.exists(), "Teacher prompt markdown must accompany the schema"
    text = prompt.read_text("utf-8")
    # Key terms that must be in the prompt
    for term in [
        "manifest.json",
        "solver_proposal.schema.json",
        "ACCEPT_SHADOW_ONLY",
        "REJECT_DUPLICATE",
        "uncertainty_declaration",
    ]:
        assert term in text, f"prompt missing expected anchor: {term}"

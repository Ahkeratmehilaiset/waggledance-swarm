# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.llm_solver_generator.

The U3 free-form synthesis surface — when U1 declarative matching
fails, the generator builds a provider-request payload and hands
it to a ProviderPlane. The actual LLM call is downstream.

Codex scout round 4, Candidate 2 (HIGH risk if missing): a regression
in this module can hand a malformed payload to the plane (no schema
validation), silently swallow a parse failure (RULE 14: never
silent), or misclassify a real LLM response as dry_run.

Direct test coverage on this file was zero before this PR. Existing
indirect tests (`test_solver_bootstrap.py`,
`test_low_risk_grower.py`, `test_outer_inner_loop_truthful.py`) reach
the module via dry-run stubs but do not pin the parse-failure
surface or the schema-validation hook.

Pinned invariants:

- `build_provider_request_payload`:
  - schema_version = 1
  - task_class = "code_or_repair"
  - provider_priority_list = the four-step default chain
  - no_runtime_mutation = True (constitutional)
  - intent string carries the gap_id and cell_id
  - `validate_request` is invoked (round-trip schema validation)
- `_short_id`: deterministic 12-char sha-prefix; differs on
  changed seed.
- `_try_parse_spec_payload`:
  - direct `spec` key in raw_payload short-circuits.
  - `stdout` JSON object path returns the parsed dict.
  - empty / missing stdout → (None, error).
  - non-JSON stdout → (None, error message containing 'JSON').
  - JSON array stdout (not an object) → (None, error message).
- `generate`:
  - dispatch.provider_type_used == "dry_run_stub" → parse_status =
    "dry_run", spec = None.
  - real provider with parseable spec → parse_status = "ok".
  - real provider with unparseable response → parse_status =
    "failed" + parse_error populated (RULE 14).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from waggledance.core.providers.provider_contracts import ProviderResponse
from waggledance.core.providers.provider_plane import ProviderDispatchResult
from waggledance.core.solver_synthesis.llm_solver_generator import (
    GenerationRequest,
    GenerationResult,
    _short_id,
    _try_parse_spec_payload,
    build_provider_request_payload,
    generate,
)


# --- helpers --------------------------------------------------------

def _request() -> GenerationRequest:
    return GenerationRequest(
        gap_id="gap_x",
        cell_id="general",
        intent="Map celsius to kelvin",
        examples=[{"input": "0 C", "expected": "273.15 K"}],
        family_hints=["scalar_unit_conversion"],
    )


def _response(raw_payload: dict, request_id: str = "req-1") -> ProviderResponse:
    return ProviderResponse(
        schema_version=1,
        response_id="resp-1",
        request_id=request_id,
        provider_used="claude_code_builder",
        raw_payload=raw_payload,
        ts_iso="2026-05-09T00:00:00Z",
        trust_layer_state="trusted",
        no_direct_mutation=True,
    )


def _dispatch(provider_type: str, raw_payload: dict) -> ProviderDispatchResult:
    """Mock dispatch result with the requested provider_type and payload."""
    request_mock = MagicMock()
    request_mock.request_id = "req-1"
    return ProviderDispatchResult(
        request=request_mock,
        response=_response(raw_payload),
        provider_id_used=f"{provider_type}_default",
        provider_type_used=provider_type,
        rationale="test dispatch",
    )


# --- _short_id ------------------------------------------------------

def test_short_id_is_twelve_chars_and_deterministic():
    a = _short_id("gap_x")
    b = _short_id("gap_x")
    assert a == b
    assert len(a) == 12


def test_short_id_differs_on_changed_seed():
    assert _short_id("gap_x") != _short_id("gap_y")


# --- build_provider_request_payload --------------------------------

def test_build_payload_satisfies_schema_via_validate_request():
    """The function must round-trip the payload through
    `validate_request` so a malformed payload cannot leak past."""
    payload = build_provider_request_payload(
        _request(),
        branch_name="test/branch",
        base_commit_hash="deadbeef",
    )
    assert payload["schema_version"] == 1
    assert payload["task_class"] == "code_or_repair"
    assert payload["no_runtime_mutation"] is True
    # Four-step default priority chain.
    assert payload["provider_priority_list"] == [
        "claude_code_builder_lane",
        "anthropic_api",
        "gpt_api",
        "local_model_service",
    ]


def test_build_payload_intent_carries_gap_id_and_cell_id():
    payload = build_provider_request_payload(
        _request(),
        branch_name="test", base_commit_hash="deadbeef",
    )
    intent = payload["intent"]
    assert "gap_x" in intent
    assert "general" in intent
    assert "Map celsius to kelvin" in intent


def test_build_payload_input_payload_carries_examples_and_family_hints():
    payload = build_provider_request_payload(
        _request(),
        branch_name="test", base_commit_hash="deadbeef",
    )
    assert payload["input_payload"]["gap_id"] == "gap_x"
    assert payload["input_payload"]["cell_id"] == "general"
    assert payload["input_payload"]["family_hints"] == ["scalar_unit_conversion"]
    assert len(payload["input_payload"]["examples"]) == 1


def test_build_payload_provenance_includes_branch_and_commit():
    payload = build_provider_request_payload(
        _request(),
        branch_name="phase9/autonomy-fabric",
        base_commit_hash="abc123",
        pinned_input_manifest_sha256="sha256:test",
    )
    prov = payload["provenance"]
    assert prov["branch_name"] == "phase9/autonomy-fabric"
    assert prov["base_commit_hash"] == "abc123"
    assert prov["pinned_input_manifest_sha256"] == "sha256:test"


def test_build_payload_section_field_optional_added_when_present():
    payload_with = build_provider_request_payload(
        _request(),
        branch_name="t", base_commit_hash="d",
        section="solver_synthesis",
    )
    assert payload_with.get("section") == "solver_synthesis"
    payload_without = build_provider_request_payload(
        _request(), branch_name="t", base_commit_hash="d",
    )
    assert "section" not in payload_without


def test_build_payload_invokes_validate_request():
    """The validation hook is the path that catches malformed
    payloads — patch `validate_request` to raise and verify it
    propagates."""
    with patch(
        "waggledance.core.solver_synthesis.llm_solver_generator.validate_request",
        side_effect=ValueError("schema rejected"),
    ):
        with pytest.raises(ValueError, match="schema rejected"):
            build_provider_request_payload(
                _request(),
                branch_name="t", base_commit_hash="d",
            )


# --- _try_parse_spec_payload ---------------------------------------

def test_parse_spec_short_circuits_on_direct_spec_key():
    raw = {"spec": {"family_kind": "scalar_unit_conversion", "factor": 1.0}}
    parsed, err = _try_parse_spec_payload(raw)
    assert err is None
    assert parsed == {"family_kind": "scalar_unit_conversion", "factor": 1.0}


def test_parse_spec_reads_stdout_json_object():
    raw = {"stdout": '{"family_kind": "lookup_table", "table": {"a": 1}}'}
    parsed, err = _try_parse_spec_payload(raw)
    assert err is None
    assert parsed == {"family_kind": "lookup_table", "table": {"a": 1}}


def test_parse_spec_returns_error_when_stdout_missing_or_empty():
    parsed, err = _try_parse_spec_payload({})
    assert parsed is None
    assert "no spec" in err

    parsed, err = _try_parse_spec_payload({"stdout": ""})
    assert parsed is None
    assert "no spec" in err

    parsed, err = _try_parse_spec_payload({"stdout": "   "})
    assert parsed is None  # whitespace-only also treated as empty


def test_parse_spec_returns_error_on_non_json_stdout():
    raw = {"stdout": "this is not json"}
    parsed, err = _try_parse_spec_payload(raw)
    assert parsed is None
    assert "not JSON" in err


def test_parse_spec_returns_error_when_stdout_json_is_array_not_object():
    """RULE 14 — never silent. Array stdout must surface as failure
    with explicit error text, not silently fall through."""
    raw = {"stdout": "[1, 2, 3]"}
    parsed, err = _try_parse_spec_payload(raw)
    assert parsed is None
    assert "not an object" in err


def test_parse_spec_spec_key_takes_precedence_over_stdout():
    """If both `spec` and `stdout` are present, `spec` wins
    (synthetic/stub path short-circuits before the JSON parse)."""
    raw = {
        "spec": {"family_kind": "from_spec_key"},
        "stdout": '{"family_kind": "from_stdout"}',
    }
    parsed, err = _try_parse_spec_payload(raw)
    assert err is None
    assert parsed["family_kind"] == "from_spec_key"


def test_parse_spec_non_mapping_spec_falls_through_to_stdout():
    """If `spec` key exists but is not a Mapping, the function
    SHOULD fall through to stdout parsing rather than crash."""
    raw = {"spec": "not a dict", "stdout": '{"family_kind": "ok"}'}
    parsed, err = _try_parse_spec_payload(raw)
    assert err is None
    assert parsed == {"family_kind": "ok"}


# --- generate ------------------------------------------------------

def test_generate_dry_run_stub_returns_dry_run_status():
    plane = MagicMock()
    plane.dispatch.return_value = _dispatch(
        provider_type="dry_run_stub", raw_payload={"stdout": "{}"},
    )
    result = generate(
        plane, _request(),
        branch_name="t", base_commit_hash="d",
    )
    assert isinstance(result, GenerationResult)
    assert result.parse_status == "dry_run"
    assert result.parsed_spec_payload is None
    assert "dry-run" in result.parse_error


def test_generate_real_provider_with_parseable_response_returns_ok():
    plane = MagicMock()
    plane.dispatch.return_value = _dispatch(
        provider_type="claude_code_builder",
        raw_payload={"stdout": '{"family_kind": "scalar_unit_conversion"}'},
    )
    result = generate(
        plane, _request(),
        branch_name="t", base_commit_hash="d",
    )
    assert result.parse_status == "ok"
    assert result.parsed_spec_payload == {"family_kind": "scalar_unit_conversion"}
    assert result.parse_error is None


def test_generate_real_provider_with_unparseable_response_returns_failed():
    """RULE 14: parse failure must NOT be silent. The generator
    MUST surface parse_status='failed' AND a parse_error string."""
    plane = MagicMock()
    plane.dispatch.return_value = _dispatch(
        provider_type="claude_code_builder",
        raw_payload={"stdout": "not valid json"},
    )
    result = generate(
        plane, _request(),
        branch_name="t", base_commit_hash="d",
    )
    assert result.parse_status == "failed"
    assert result.parsed_spec_payload is None
    assert result.parse_error is not None
    assert "not JSON" in result.parse_error


def test_generate_dry_run_payload_marker_also_triggers_dry_run_status():
    """A real provider type but with `dry_run=True` in raw_payload
    MUST also classify as dry_run — never silently treat synthetic
    output as a real LLM response."""
    plane = MagicMock()
    plane.dispatch.return_value = _dispatch(
        provider_type="claude_code_builder",
        raw_payload={"dry_run": True, "stdout": "{}"},
    )
    result = generate(
        plane, _request(),
        branch_name="t", base_commit_hash="d",
    )
    assert result.parse_status == "dry_run"


def test_generate_passes_request_through_to_result():
    request = _request()
    plane = MagicMock()
    plane.dispatch.return_value = _dispatch(
        provider_type="claude_code_builder",
        raw_payload={"spec": {"family_kind": "scalar_unit_conversion"}},
    )
    result = generate(
        plane, request,
        branch_name="t", base_commit_hash="d",
    )
    assert result.request is request
    assert result.dispatch is plane.dispatch.return_value


def test_generate_nested_spec_in_raw_payload_returns_ok():
    """Codex scout round 4 explicitly called out the nested-spec
    branch as undercovered: the synthetic / stub path uses
    `raw_payload["spec"]` directly, bypassing JSON parsing."""
    plane = MagicMock()
    plane.dispatch.return_value = _dispatch(
        provider_type="claude_code_builder",
        raw_payload={"spec": {"family_kind": "lookup_table",
                                "table": {"k": "v"}}},
    )
    result = generate(
        plane, _request(),
        branch_name="t", base_commit_hash="d",
    )
    assert result.parse_status == "ok"
    assert result.parsed_spec_payload == {"family_kind": "lookup_table",
                                            "table": {"k": "v"}}

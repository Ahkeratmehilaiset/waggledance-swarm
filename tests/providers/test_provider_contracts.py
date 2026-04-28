"""Targeted tests for the Phase 10 P3 provider contracts."""

from __future__ import annotations

from copy import deepcopy

import pytest

from waggledance.core.providers import (
    PROVIDER_TYPES,
    ProviderContractError,
    TASK_CLASSES,
    TRUST_LAYERS,
    validate_request,
    validate_response,
)


VALID_REQUEST: dict = {
    "schema_version": 1,
    "request_id": "abcdef012345",
    "task_class": "code_or_repair",
    "provider_priority_list": [
        "claude_code_builder_lane",
        "anthropic_api",
        "gpt_api",
        "local_model_service",
    ],
    "intent": "Add a path resolver smoke test.",
    "input_payload": {"hint": "isolated worktree only"},
    "budget": {"max_calls": 1, "max_tokens": 8000, "max_latency_ms": 600000},
    "agent_id_hint": None,
    "capsule_context": "neutral_v1",
    "no_runtime_mutation": True,
    "provenance": {
        "branch_name": "phase10/foundation-truth-builder-lane",
        "base_commit_hash": "8bf1869",
        "pinned_input_manifest_sha256": "sha256:unknown",
    },
}

VALID_RESPONSE: dict = {
    "schema_version": 1,
    "response_id": "resp-001",
    "request_id": "abcdef012345",
    "provider_used": "claude_code_builder_lane_default",
    "raw_payload": {"stdout": "(empty)", "returncode": 0},
    "ts_iso": "2026-04-28T01:00:00+00:00",
    "latency_ms": 12.5,
    "trust_layer_state": "raw_quarantine",
    "no_direct_mutation": True,
}


def test_request_round_trip_validates() -> None:
    req = validate_request(VALID_REQUEST)
    assert req.task_class == "code_or_repair"
    assert req.provider_priority_list[0] == "claude_code_builder_lane"
    assert req.no_runtime_mutation is True
    # to_dict round-trips
    assert req.to_dict()["request_id"] == "abcdef012345"


def test_response_round_trip_validates() -> None:
    resp = validate_response(VALID_RESPONSE)
    assert resp.trust_layer_state == "raw_quarantine"
    assert resp.no_direct_mutation is True


def test_request_missing_required_field_raises() -> None:
    payload = deepcopy(VALID_REQUEST)
    del payload["intent"]
    with pytest.raises(ProviderContractError):
        validate_request(payload)


def test_request_unknown_task_class_raises() -> None:
    payload = deepcopy(VALID_REQUEST)
    payload["task_class"] = "do_my_taxes"
    with pytest.raises(ProviderContractError):
        validate_request(payload)


def test_request_no_runtime_mutation_must_be_true() -> None:
    payload = deepcopy(VALID_REQUEST)
    payload["no_runtime_mutation"] = False
    with pytest.raises(ProviderContractError):
        validate_request(payload)


def test_response_no_direct_mutation_must_be_true() -> None:
    payload = deepcopy(VALID_RESPONSE)
    payload["no_direct_mutation"] = False
    with pytest.raises(ProviderContractError):
        validate_response(payload)


def test_response_unknown_trust_layer_raises() -> None:
    payload = deepcopy(VALID_RESPONSE)
    payload["trust_layer_state"] = "totally_trusted"
    with pytest.raises(ProviderContractError):
        validate_response(payload)


def test_provider_priority_list_must_use_known_types() -> None:
    payload = deepcopy(VALID_REQUEST)
    payload["provider_priority_list"] = ["unknown_provider"]
    with pytest.raises(ProviderContractError):
        validate_request(payload)


def test_constants_consistent_with_schema() -> None:
    # The 5-provider constant set is a Phase 10 superset (adds dry_run_stub).
    assert "dry_run_stub" in PROVIDER_TYPES
    assert "claude_code_builder_lane" in PROVIDER_TYPES
    assert TASK_CLASSES == ("code_or_repair", "spec_or_critique", "bulk_classification")
    assert "raw_quarantine" in TRUST_LAYERS
    assert "human_gated" in TRUST_LAYERS

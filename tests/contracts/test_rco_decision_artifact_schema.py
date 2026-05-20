from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.rco_decision_artifact import (
    build_rco_decision_artifact,
    validate_rco_decision_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas" / "v3_13_0" / "rco_decision_artifact.v0.json"


def _validator() -> jsonschema.Draft7Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )


def _good_artifact() -> dict:
    return {
        "rco_decision_version": "magma.rco_decision_artifact.v0",
        "decision_id": "rco:decision:test:001",
        "ts_utc": "2026-05-20T12:00:00Z",
        "intent_digest": sha256_digest({"intent": "test"}),
        "write_payload_digest": sha256_digest({"payload": "test"}),
        "risk_class": "local_artifact",
        "gate_decision": "review",
        "approved": False,
        "operator_required": False,
        "approval_id": None,
        "policy_version": "policy:write_rco_gate:v1",
        "charter_version": "charter:v1",
        "scope_policy_decision": "requires_operator",
        "peer_rco_verdict": "pass",
        "verifier_path": ["write_rco_gate_v1", "rco_decision_artifact_v0"],
        "reason_codes": ["rco:local_artifact_requires_review"],
        "audit_event_ids": ["write.intent_classified:test:001"],
        "stop_condition": None,
    }


def test_rco_decision_artifact_schema_is_valid_draft7() -> None:
    _validator()


def test_good_rco_decision_artifact_validates() -> None:
    _validator().validate(_good_artifact())
    validate_rco_decision_artifact(_good_artifact())


def test_rco_decision_artifact_rejects_raw_payload_and_bad_digest() -> None:
    validator = _validator()
    raw_payload = _good_artifact()
    raw_payload["raw_payload"] = {"secret": "not allowed"}
    bad_digest = _good_artifact()
    bad_digest["intent_digest"] = "sha256:not-a-digest"

    assert list(validator.iter_errors(raw_payload))
    assert list(validator.iter_errors(bad_digest))


def test_external_effect_requires_operator_required() -> None:
    artifact = _good_artifact()
    artifact["risk_class"] = "external_effect"
    artifact["gate_decision"] = "require_approval"
    artifact["operator_required"] = False

    assert list(_validator().iter_errors(artifact))


def test_refuse_decision_cannot_be_approved() -> None:
    artifact = _good_artifact()
    artifact["gate_decision"] = "refuse"
    artifact["approved"] = True

    assert list(_validator().iter_errors(artifact))


def test_helper_binds_intent_and_payload_digests() -> None:
    intent = {"intent_id": "intent:test:001", "payload_digest": "sha256:test"}
    payload = {"write": "value"}

    artifact = build_rco_decision_artifact(
        decision_id="rco:decision:test:002",
        ts_utc="2026-05-20T12:00:00Z",
        intent=intent,
        write_payload=payload,
        risk_class="internal_memory",
        gate_decision="allow",
        approved=True,
        policy_version="policy:write_rco_gate:v1",
        charter_version="charter:v1",
        reason_codes=["rco:internal_memory_allow"],
        verifier_path=["write_rco_gate_v1"],
    )

    assert artifact["intent_digest"] == sha256_digest(intent)
    assert artifact["write_payload_digest"] == sha256_digest(payload)
    assert artifact["operator_required"] is False


def test_helper_refuses_inconsistent_refuse_decision() -> None:
    with pytest.raises(ValueError, match="refused"):
        build_rco_decision_artifact(
            decision_id="rco:decision:test:003",
            ts_utc="2026-05-20T12:00:00Z",
            intent={"intent": "x"},
            write_payload={"payload": "x"},
            risk_class="local_artifact",
            gate_decision="refuse",
            approved=True,
            policy_version="policy:write_rco_gate:v1",
            charter_version="charter:v1",
            reason_codes=["rco:refused"],
            verifier_path=["write_rco_gate_v1"],
        )


def test_schema_rejects_unknown_status_values() -> None:
    artifact = copy.deepcopy(_good_artifact())
    artifact["peer_rco_verdict"] = "looks_good_to_me"

    assert list(_validator().iter_errors(artifact))

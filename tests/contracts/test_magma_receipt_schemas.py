from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validator(name: str) -> jsonschema.Draft7Validator:
    schema = _schema(name)
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def good_evaluation_result() -> dict:
    return {
        "evaluation_version": "magma.evaluation_result.v0",
        "case_id": "case:external-effect-counterfactual-001",
        "subject_type": "counterfactual",
        "target_digest": digest("9"),
        "risk_class": "external_effect",
        "expected_gate": "require_approval",
        "actual_gate": "require_approval",
        "verifier_path": ["schema", "rco_gate", "operator_gate"],
        "solver_selection": ["pdam_close_solver_core"],
        "policy_version": "policy:v1",
        "charter_version": "charter:v1",
        "domain_threshold_version": "threshold:pdam:v1",
        "verdict": "pass",
        "reason_codes": ["external_effect_requires_operator_gate"],
        "operator_required": True,
        "confidence_score": 0.91,
        "uncertainty_sources": [
            {
                "kind": "limited_evidence",
                "detail": "Live system state is not replayed in this fixture.",
            }
        ],
    }


def good_magma_receipt() -> dict:
    return {
        "receipt_version": "magma.receipt.v1",
        "event_id": "magma:receipt:20260517:001",
        "ts_utc": "2026-05-17T05:10:00Z",
        "risk_class": "external_effect",
        "payload_visibility": "digest_only",
        "canonical_payload_digest": digest("a"),
        "prev_receipt_hash": digest("b"),
        "policy_digest": digest("c"),
        "charter_digest": digest("d"),
        "rco_decision_digest": digest("e"),
        "world_snapshot_digest": digest("f"),
        "solver_contract_digest": digest("1"),
        "evaluation_result_digest": digest("2"),
        "approval_id": "bridge:approval:pr449",
        "operator_gate_required": True,
        "signature_algorithm": None,
        "signature": None,
        "key_id": None,
        "anchored_at": None,
    }


def test_magma_receipt_and_evaluation_schemas_are_valid_draft7() -> None:
    _validator("magma_receipt.v1.json")
    _validator("evaluation_result.v0.json")


def test_good_magma_receipt_and_evaluation_result_validate() -> None:
    _validator("evaluation_result.v0.json").validate(good_evaluation_result())
    _validator("magma_receipt.v1.json").validate(good_magma_receipt())


def test_receipt_requires_charter_and_evaluation_digests() -> None:
    validator = _validator("magma_receipt.v1.json")
    no_charter = good_magma_receipt()
    del no_charter["charter_digest"]
    no_eval = good_magma_receipt()
    del no_eval["evaluation_result_digest"]

    assert list(validator.iter_errors(no_charter))
    assert list(validator.iter_errors(no_eval))


def test_receipt_requires_explicit_prev_hash_but_allows_genesis_null() -> None:
    validator = _validator("magma_receipt.v1.json")
    genesis = good_magma_receipt()
    genesis["prev_receipt_hash"] = None
    validator.validate(genesis)

    missing = good_magma_receipt()
    del missing["prev_receipt_hash"]
    assert list(validator.iter_errors(missing))


def test_receipt_rejects_non_canonical_digest_shape() -> None:
    receipt = good_magma_receipt()
    receipt["canonical_payload_digest"] = "sha256:not-rfc8785-bytes"

    assert list(_validator("magma_receipt.v1.json").iter_errors(receipt))


def test_receipt_rejects_raw_payload_and_unknown_risk_class() -> None:
    validator = _validator("magma_receipt.v1.json")
    raw_payload = good_magma_receipt()
    raw_payload["raw_payload"] = {"secret": "not allowed"}
    unknown_risk = good_magma_receipt()
    unknown_risk["risk_class"] = "operator_free_external_write"

    assert list(validator.iter_errors(raw_payload))
    assert list(validator.iter_errors(unknown_risk))


def test_external_effect_receipt_requires_operator_gate() -> None:
    receipt = good_magma_receipt()
    receipt["operator_gate_required"] = False

    assert list(_validator("magma_receipt.v1.json").iter_errors(receipt))


def test_signature_envelope_is_null_or_complete() -> None:
    validator = _validator("magma_receipt.v1.json")
    signed = good_magma_receipt()
    signed.update({
        "signature_algorithm": "Ed25519",
        "signature": "base64url:abcdefghijklmnopqrstuvwxyz0123456789_-",
        "key_id": "did:wd:local-dev-key",
    })
    validator.validate(signed)

    partial = good_magma_receipt()
    partial["signature"] = "base64url:abcdefghijklmnopqrstuvwxyz0123456789_-"
    assert list(validator.iter_errors(partial))


def test_evaluation_result_requires_confidence_and_uncertainty_sources() -> None:
    validator = _validator("evaluation_result.v0.json")
    no_confidence = good_evaluation_result()
    del no_confidence["confidence_score"]
    no_uncertainty = good_evaluation_result()
    del no_uncertainty["uncertainty_sources"]

    assert list(validator.iter_errors(no_confidence))
    assert list(validator.iter_errors(no_uncertainty))


def test_evaluation_result_requires_target_digest_and_verdict() -> None:
    validator = _validator("evaluation_result.v0.json")
    no_target = good_evaluation_result()
    del no_target["target_digest"]
    no_verdict = good_evaluation_result()
    del no_verdict["verdict"]
    bad_verdict = good_evaluation_result()
    bad_verdict["verdict"] = "probably_ok"

    assert list(validator.iter_errors(no_target))
    assert list(validator.iter_errors(no_verdict))
    assert list(validator.iter_errors(bad_verdict))


def test_external_effect_evaluation_requires_operator_required() -> None:
    result = good_evaluation_result()
    result["operator_required"] = False

    assert list(_validator("evaluation_result.v0.json").iter_errors(result))


def test_evaluation_result_rejects_out_of_range_confidence_score() -> None:
    result = copy.deepcopy(good_evaluation_result())
    result["confidence_score"] = 1.2

    assert list(_validator("evaluation_result.v0.json").iter_errors(result))


def test_evaluation_result_uncertainty_sources_are_typed_and_may_be_empty() -> None:
    validator = _validator("evaluation_result.v0.json")
    no_concerns = good_evaluation_result()
    no_concerns["uncertainty_sources"] = []
    validator.validate(no_concerns)

    untyped = good_evaluation_result()
    untyped["uncertainty_sources"] = ["free-form uncertainty"]
    assert list(validator.iter_errors(untyped))

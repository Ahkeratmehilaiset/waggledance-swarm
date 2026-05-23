from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import (
    build_evaluation_result,
    build_evaluation_result_v1,
)
from waggledance.core.magma.receipt import build_magma_receipt


def digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def evaluation_for(payload: dict, case_id: str = "case:magma:receipt-emitter:001") -> dict:
    return build_evaluation_result(
        case_id=case_id,
        subject_type="counterfactual",
        target_payload=payload,
        risk_class="internal_memory",
        expected_gate="review",
        actual_gate="review",
        verifier_path=["unit_test"],
        solver_selection=["fixture_solver"],
        policy_version="policy:fixture:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:fixture:v1",
        verdict="pass",
        reason_codes=["fixture:receipt_emitter"],
        confidence_score=1.0,
    )


def evaluation_v1_for(
    payload: dict,
    case_id: str = "case:magma:receipt-emitter-v1:001",
) -> dict:
    return build_evaluation_result_v1(
        case_id=case_id,
        subject_type="counterfactual",
        target_payload=payload,
        risk_class="internal_memory",
        expected_gate="review",
        actual_gate="review",
        verifier_path=["unit_test"],
        solver_selection=["fixture_solver"],
        policy_version="policy:fixture:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:fixture:v1",
        verdict="pass",
        reason_codes=["fixture:receipt_emitter"],
        confidence_score=1.0,
        confidence_basis={
            "method": "point_estimate",
            "sample_count": 1,
        },
    )


def receipt_for(
    payload: dict,
    evaluation: dict,
    *,
    event_id: str = "magma:receipt:test:001",
    previous_receipt: dict | None = None,
    risk_class: str = "internal_memory",
    approval_id: str | None = None,
    signature_envelope: dict[str, str] | None = None,
    allow_external_effect: bool = False,
    charter_digest: str | None = None,
) -> dict:
    return build_magma_receipt(
        event_id=event_id,
        ts_utc="2026-05-17T07:05:00Z",
        risk_class=risk_class,
        payload=payload,
        evaluation_result=evaluation,
        previous_receipt=previous_receipt,
        policy_digest=digest("1"),
        charter_digest=charter_digest or digest("2"),
        rco_decision_digest=digest("3"),
        world_snapshot_digest=digest("4"),
        solver_contract_digest=digest("5"),
        approval_id=approval_id,
        signature_envelope=signature_envelope,
        allow_external_effect=allow_external_effect,
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_builds_schema_valid_receipt_and_binds_payload_and_evaluation_digests() -> None:
    payload = {"action": "evaluate_only", "case": 1}
    evaluation = evaluation_for(payload)

    receipt = receipt_for(payload, evaluation)

    assert receipt["receipt_version"] == "magma.receipt.v1"
    assert receipt["canonical_payload_digest"] == sha256_digest(payload)
    assert receipt["evaluation_result_digest"] == sha256_digest(evaluation)
    assert receipt["prev_receipt_hash"] is None
    assert "raw_payload" not in receipt


def test_child_receipt_binds_previous_receipt_hash() -> None:
    first_payload = {"action": "first"}
    first_eval = evaluation_for(first_payload, "case:magma:receipt-emitter:001")
    first = receipt_for(first_payload, first_eval, event_id="magma:receipt:test:001")
    second_payload = {"action": "second"}
    second_eval = evaluation_for(second_payload, "case:magma:receipt-emitter:002")

    second = receipt_for(
        second_payload,
        second_eval,
        event_id="magma:receipt:test:002",
        previous_receipt=first,
    )

    assert second["prev_receipt_hash"] == sha256_digest(first)


def test_rejects_evaluation_result_for_different_payload() -> None:
    payload = {"action": "receipt_payload"}
    evaluation = evaluation_for({"action": "other_payload"})

    with pytest.raises(ValueError, match="target_digest"):
        receipt_for(payload, evaluation)


def test_rejects_evaluation_result_for_different_risk_class() -> None:
    payload = {"action": "would_write_logbook"}
    evaluation = build_evaluation_result(
        case_id="case:magma:receipt-emitter:risk-mismatch",
        subject_type="counterfactual",
        target_payload=payload,
        risk_class="external_effect",
        expected_gate="require_approval",
        actual_gate="require_approval",
        verifier_path=["unit_test"],
        solver_selection=["fixture_solver"],
        policy_version="policy:fixture:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:fixture:v1",
        verdict="pass",
        reason_codes=["fixture:risk_mismatch"],
        confidence_score=1.0,
        allow_external_effect=True,
    )

    with pytest.raises(ValueError, match="risk_class"):
        receipt_for(payload, evaluation, risk_class="internal_memory")


def test_external_effect_forces_operator_gate_and_rejects_missing_approval() -> None:
    payload = {"action": "would_write_logbook"}
    evaluation = build_evaluation_result(
        case_id="case:magma:receipt-emitter:external",
        subject_type="counterfactual",
        target_payload=payload,
        risk_class="external_effect",
        expected_gate="require_approval",
        actual_gate="require_approval",
        verifier_path=["unit_test"],
        solver_selection=["fixture_solver"],
        policy_version="policy:fixture:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:fixture:v1",
        verdict="pass",
        reason_codes=["fixture:external_effect"],
        confidence_score=1.0,
        allow_external_effect=True,
    )

    with pytest.raises(ValueError, match="refuses external_effect"):
        receipt_for(payload, evaluation, risk_class="external_effect")

    with pytest.raises(ValueError, match="approval_id"):
        receipt_for(
            payload,
            evaluation,
            risk_class="external_effect",
            allow_external_effect=True,
        )

    receipt = receipt_for(
        payload,
        evaluation,
        risk_class="external_effect",
        approval_id="bridge:approval:test",
        allow_external_effect=True,
    )
    assert receipt["operator_gate_required"] is True


def test_signature_envelope_is_null_or_complete() -> None:
    payload = {"action": "signed"}
    evaluation = evaluation_for(payload)
    unsigned = receipt_for(payload, evaluation)
    assert unsigned["signature_algorithm"] is None
    assert unsigned["signature"] is None
    assert unsigned["key_id"] is None

    with pytest.raises(ValueError, match="signature_envelope"):
        receipt_for(
            payload,
            evaluation,
            signature_envelope={
                "signature": "base64url:abcdefghijklmnopqrstuvwxyz",
                "signature_algorithm": "Ed25519",
            },
        )

    signed = receipt_for(
        payload,
        evaluation,
        signature_envelope={
            "signature_algorithm": "Ed25519",
            "signature": "base64url:abcdefghijklmnopqrstuvwxyz0123456789_-",
            "key_id": "did:wd:test-key",
        },
    )
    assert signed["signature_algorithm"] == "Ed25519"


def test_receipt_does_not_copy_payload_content() -> None:
    payload = {
        "action": "digest_only",
        "secret": "operator_secret_marker_DO_NOT_LEAK",
    }
    evaluation = evaluation_for(payload)

    receipt = receipt_for(payload, evaluation)

    assert "operator_secret_marker_DO_NOT_LEAK" not in json.dumps(receipt, sort_keys=True)


def test_rejects_invalid_risk_class_and_external_digest_shape() -> None:
    payload = {"action": "schema_guard"}
    evaluation = evaluation_for(payload)

    with pytest.raises(ValueError, match="risk_class"):
        receipt_for(payload, evaluation, risk_class="made_up_tier")

    with pytest.raises(ValueError, match="invalid MAGMA receipt v1"):
        receipt_for(payload, evaluation, charter_digest="not-a-sha256")


def test_emitted_chain_verifies_with_offline_verifier(tmp_path: Path) -> None:
    payload1 = {"action": "first"}
    eval1 = evaluation_for(payload1, "case:magma:receipt-emitter:chain001")
    receipt1 = receipt_for(payload1, eval1, event_id="magma:receipt:chain:001")
    payload2 = {"action": "second"}
    eval2 = evaluation_for(payload2, "case:magma:receipt-emitter:chain002")
    receipt2 = receipt_for(
        payload2,
        eval2,
        event_id="magma:receipt:chain:002",
        previous_receipt=receipt1,
    )
    chain_dir = tmp_path / "chain"
    chain_dir.mkdir()
    write_json(chain_dir / "payload-001.json", payload1)
    write_json(chain_dir / "evaluation-001.json", eval1)
    write_json(chain_dir / "receipt-001.json", receipt1)
    write_json(chain_dir / "payload-002.json", payload2)
    write_json(chain_dir / "evaluation-002.json", eval2)
    write_json(chain_dir / "receipt-002.json", receipt2)
    write_json(
        chain_dir / "manifest.json",
        {
            "chain_id": "magma:receipt:emitter:test",
            "entries": [
                {
                    "payload": "payload-001.json",
                    "evaluation_result": "evaluation-001.json",
                    "receipt": "receipt-001.json",
                },
                {
                    "payload": "payload-002.json",
                    "evaluation_result": "evaluation-002.json",
                    "receipt": "receipt-002.json",
                },
            ],
        },
    )

    report = verify_manifest(chain_dir / "manifest.json")

    assert report["ok"] is True
    assert report["receipt_count"] == 2


def test_emitted_v1_evaluation_chain_verifies_with_offline_verifier(tmp_path: Path) -> None:
    payload = {"action": "v1_evaluation"}
    evaluation = evaluation_v1_for(payload)
    receipt = receipt_for(
        payload,
        evaluation,
        event_id="magma:receipt:v1-chain:001",
    )
    chain_dir = tmp_path / "v1_chain"
    chain_dir.mkdir()
    write_json(chain_dir / "payload.json", payload)
    write_json(chain_dir / "evaluation.json", evaluation)
    write_json(chain_dir / "receipt.json", receipt)
    write_json(
        chain_dir / "manifest.json",
        {
            "chain_id": "magma:receipt:emitter:v1-test",
            "entries": [
                {
                    "payload": "payload.json",
                    "evaluation_result": "evaluation.json",
                    "receipt": "receipt.json",
                }
            ],
        },
    )

    report = verify_manifest(chain_dir / "manifest.json")

    assert report["ok"] is True
    assert report["receipt_count"] == 1


def test_offline_verifier_rejects_unknown_evaluation_version(tmp_path: Path) -> None:
    payload = {"action": "unknown_evaluation_version"}
    evaluation = evaluation_v1_for(payload)
    evaluation["evaluation_version"] = "magma.evaluation_result.v99"
    receipt = receipt_for(
        payload,
        evaluation,
        event_id="magma:receipt:unknown-eval-version:001",
    )
    chain_dir = tmp_path / "unknown_version_chain"
    chain_dir.mkdir()
    write_json(chain_dir / "payload.json", payload)
    write_json(chain_dir / "evaluation.json", evaluation)
    write_json(chain_dir / "receipt.json", receipt)
    write_json(
        chain_dir / "manifest.json",
        {
            "chain_id": "magma:receipt:emitter:unknown-version-test",
            "entries": [
                {
                    "payload": "payload.json",
                    "evaluation_result": "evaluation.json",
                    "receipt": "receipt.json",
                }
            ],
        },
    )

    report = verify_manifest(chain_dir / "manifest.json")

    assert report["ok"] is False
    assert any("unknown evaluation_version" in error for error in report["errors"])

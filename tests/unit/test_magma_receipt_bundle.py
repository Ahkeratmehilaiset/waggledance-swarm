from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Any
import json

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)


class _BooleanInt(IntEnum):
    FALSE = 0
    TRUE = 1


class _ExplosiveBool:
    def __bool__(self) -> bool:
        raise AssertionError("verifier status must not be coerced")


def _digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def _evaluation(payload: dict[str, Any], case_id: str) -> dict[str, Any]:
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
        reason_codes=["fixture:receipt_bundle"],
        confidence_score=1.0,
    )


def _receipt(
    payload: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    event_id: str,
    previous_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_magma_receipt(
        event_id=event_id,
        ts_utc="2026-05-17T12:00:00Z",
        risk_class=evaluation["risk_class"],
        payload=payload,
        evaluation_result=evaluation,
        previous_receipt=previous_receipt,
        policy_digest=_digest("1"),
        charter_digest=_digest("2"),
        rco_decision_digest=_digest("3"),
        world_snapshot_digest=_digest("4"),
        solver_contract_digest=_digest("5"),
    )


def _entry(
    label: str,
    payload: dict[str, Any],
    *,
    event_id: str,
    previous_receipt: dict[str, Any] | None = None,
) -> ReceiptBundleEntry:
    evaluation = _evaluation(payload, f"case:magma:bundle:{label}")
    receipt = _receipt(
        payload,
        evaluation,
        event_id=event_id,
        previous_receipt=previous_receipt,
    )
    return ReceiptBundleEntry(
        label=label,
        payload=payload,
        evaluation_result=evaluation,
        receipt=receipt,
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_write_receipt_bundle_writes_single_entry_and_verifies(tmp_path: Path) -> None:
    out_dir = tmp_path / "bundle"
    entry = _entry(
        "alpha",
        {"action": "evaluate", "case": 1},
        event_id="magma:bundle:test:001",
    )

    report = write_receipt_bundle(
        out_dir=out_dir,
        chain_id="magma:bundle:test:v0",
        entries=[entry],
        verify_manifest=verify_manifest,
    )

    assert report["receipt_count"] == 1
    assert report["verifier_report"] == {
        "ok": True,
        "receipt_count": 1,
        "errors": [],
    }
    payload = _read_json(out_dir / "payload-001-alpha.json")
    evaluation = _read_json(out_dir / "evaluation-001-alpha.json")
    receipt = _read_json(out_dir / "receipt-001-alpha.json")
    manifest = _read_json(out_dir / "manifest.json")
    assert manifest["chain_id"] == "magma:bundle:test:v0"
    assert evaluation["target_digest"] == sha256_digest(payload)
    assert receipt["canonical_payload_digest"] == sha256_digest(payload)
    assert receipt["evaluation_result_digest"] == sha256_digest(evaluation)


def test_write_receipt_bundle_preserves_two_entry_chain(tmp_path: Path) -> None:
    first = _entry(
        "first",
        {"action": "first"},
        event_id="magma:bundle:test:001",
    )
    second = _entry(
        "second",
        {"action": "second"},
        event_id="magma:bundle:test:002",
        previous_receipt=first.receipt,
    )

    report = write_receipt_bundle(
        out_dir=tmp_path / "bundle",
        chain_id="magma:bundle:test:v0",
        entries=[first, second],
        verify_manifest=verify_manifest,
    )

    assert report["verifier_report"]["ok"] is True
    assert report["verifier_report"]["receipt_count"] == 2
    assert second.receipt["prev_receipt_hash"] == sha256_digest(first.receipt)


def test_write_receipt_bundle_refuses_existing_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "existing"
    out_dir.mkdir()
    entry = _entry(
        "alpha",
        {"action": "evaluate"},
        event_id="magma:bundle:test:001",
    )

    with pytest.raises(ValueError, match="out_dir must not exist"):
        write_receipt_bundle(
            out_dir=out_dir,
            chain_id="magma:bundle:test:v0",
            entries=[entry],
            verify_manifest=verify_manifest,
        )


def test_write_receipt_bundle_verifier_failure_is_fail_closed(tmp_path: Path) -> None:
    entry = _entry(
        "alpha",
        {"action": "evaluate"},
        event_id="magma:bundle:test:001",
    )

    def fail_manifest(path: Path) -> dict[str, Any]:
        return {
            "ok": False,
            "receipt_count": 1,
            "errors": [f"fixture failure at {path.name}"],
        }

    with pytest.raises(ValueError, match="receipt bundle verification failed"):
        write_receipt_bundle(
            out_dir=tmp_path / "bundle",
            chain_id="magma:bundle:test:v0",
            entries=[entry],
            verify_manifest=fail_manifest,
        )


@pytest.mark.parametrize(
    "verifier_ok",
    [
        "false",
        "true",
        0,
        1,
        None,
        _BooleanInt.FALSE,
        _BooleanInt.TRUE,
        _ExplosiveBool(),
    ],
)
def test_write_receipt_bundle_rejects_non_literal_verifier_status(
    tmp_path: Path,
    verifier_ok: object,
) -> None:
    entry = _entry(
        "alpha",
        {"action": "evaluate"},
        event_id="magma:bundle:test:001",
    )

    def malformed_manifest(_path: Path) -> dict[str, Any]:
        return {
            "ok": verifier_ok,
            "receipt_count": 1,
            "errors": [],
        }

    with pytest.raises(
        ValueError,
        match="verifier ok must be a literal bool",
    ):
        write_receipt_bundle(
            out_dir=tmp_path / "bundle",
            chain_id="magma:bundle:test:v0",
            entries=[entry],
            verify_manifest=malformed_manifest,
        )


def test_write_receipt_bundle_rejects_empty_entries_before_directory_create(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "empty"

    with pytest.raises(ValueError, match="at least one entry"):
        write_receipt_bundle(
            out_dir=out_dir,
            chain_id="magma:bundle:test:v0",
            entries=[],
            verify_manifest=verify_manifest,
        )

    assert not out_dir.exists()


def test_write_receipt_bundle_rejects_unsafe_label_before_directory_create(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "unsafe"
    safe_entry = _entry(
        "safe",
        {"action": "evaluate"},
        event_id="magma:bundle:test:001",
    )
    entry = ReceiptBundleEntry(
        label="../escape",
        payload=safe_entry.payload,
        evaluation_result=safe_entry.evaluation_result,
        receipt=safe_entry.receipt,
    )

    with pytest.raises(ValueError, match="filename-safe"):
        write_receipt_bundle(
            out_dir=out_dir,
            chain_id="magma:bundle:test:v0",
            entries=[entry],
            verify_manifest=verify_manifest,
        )

    assert not out_dir.exists()

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify_magma_receipt.py"
POLICY_SURFACE_FIXTURE = ROOT / "tests" / "fixtures" / "policy_surface_v0.json"


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _evaluation(*, case_id: str, payload_digest: str) -> dict:
    return {
        "evaluation_version": "magma.evaluation_result.v0",
        "case_id": case_id,
        "subject_type": "counterfactual",
        "target_digest": payload_digest,
        "risk_class": "external_effect",
        "expected_gate": "require_approval",
        "actual_gate": "require_approval",
        "verifier_path": ["schema", "rco_gate", "operator_gate"],
        "solver_selection": ["fixture_solver"],
        "policy_version": "policy:v1",
        "charter_version": "charter:v1",
        "domain_threshold_version": "threshold:fixture:v1",
        "verdict": "pass",
        "reason_codes": ["external_effect_requires_operator_gate"],
        "operator_required": True,
        "confidence_score": 0.9,
        "uncertainty_sources": [],
    }


def _receipt(
    *,
    event_id: str,
    payload_digest: str,
    evaluation_digest: str,
    prev_hash: str | None,
    policy_digest: str | None = None,
    charter_digest: str | None = None,
) -> dict:
    return {
        "receipt_version": "magma.receipt.v1",
        "event_id": event_id,
        "ts_utc": "2026-05-17T05:30:00Z",
        "risk_class": "external_effect",
        "payload_visibility": "digest_only",
        "canonical_payload_digest": payload_digest,
        "prev_receipt_hash": prev_hash,
        "policy_digest": policy_digest or "sha256:" + "1" * 64,
        "charter_digest": charter_digest or "sha256:" + "2" * 64,
        "rco_decision_digest": "sha256:" + "3" * 64,
        "world_snapshot_digest": "sha256:" + "4" * 64,
        "solver_contract_digest": "sha256:" + "5" * 64,
        "evaluation_result_digest": evaluation_digest,
        "approval_id": "bridge:approval:fixture",
        "operator_gate_required": True,
        "signature_algorithm": None,
        "signature": None,
        "key_id": None,
        "anchored_at": None,
    }


def _write_chain(root: Path) -> Path:
    root.mkdir()
    entries = []
    prev_hash: str | None = None
    for index in (1, 2):
        payload = {
            "action": "fixture_external_effect",
            "index": index,
            "requires_operator": True,
        }
        payload_digest = sha256_digest(payload)
        evaluation = _evaluation(
            case_id=f"case:magma:fixture:{index:03d}",
            payload_digest=payload_digest,
        )
        receipt = _receipt(
            event_id=f"magma:receipt:fixture:{index:03d}",
            payload_digest=payload_digest,
            evaluation_digest=sha256_digest(evaluation),
            prev_hash=prev_hash,
        )
        _write_json(root / f"payload-{index:03d}.json", payload)
        _write_json(root / f"evaluation-{index:03d}.json", evaluation)
        _write_json(root / f"receipt-{index:03d}.json", receipt)
        entries.append(
            {
                "receipt": f"receipt-{index:03d}.json",
                "payload": f"payload-{index:03d}.json",
                "evaluation_result": f"evaluation-{index:03d}.json",
            }
        )
        prev_hash = sha256_digest(receipt)
    manifest = {"chain_id": "magma:fixture:valid_chain", "entries": entries}
    _write_json(root / "manifest.json", manifest)
    return root / "manifest.json"


def _write_policy_surface(root: Path) -> Path:
    policy = json.loads(POLICY_SURFACE_FIXTURE.read_text(encoding="utf-8"))
    _write_json(root / "policy_surface_v0.json", policy)
    return root / "policy_surface_v0.json"


def _bind_receipts_to_policy_surface(manifest: Path, policy_path: Path) -> tuple[str, str]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy_digest = sha256_digest(policy)
    charter_digest = sha256_digest(policy["charter_sections"])
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    prev_hash: str | None = None
    for entry in manifest_json["entries"]:
        receipt_path = manifest.parent / entry["receipt"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["policy_digest"] = policy_digest
        receipt["charter_digest"] = charter_digest
        receipt["prev_receipt_hash"] = prev_hash
        _write_json(receipt_path, receipt)
        prev_hash = sha256_digest(receipt)
    return policy_digest, charter_digest


def _run_verify(manifest: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest), *extra],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_canonicalization_contract_is_pinned() -> None:
    assert canonical_json_bytes({"b": 1, "a": "å"}) == b'{"a":"\\u00e5","b":1}'
    with pytest.raises(ValueError):
        canonical_json_bytes({"not_a_number": float("nan")})


def test_cli_verifies_valid_chain(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")

    result = _run_verify(manifest)

    assert result.returncode == 0, result.stderr
    assert "magma receipt verification OK: 2 receipts" in result.stdout


def test_cli_writes_json_report_for_valid_chain(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")

    result = _run_verify(manifest, "--json")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["receipt_count"] == 2
    assert report["errors"] == []


def test_cli_verifies_manifest_entries_out_of_chain_order(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_json["entries"] = list(reversed(manifest_json["entries"]))
    _write_json(manifest, manifest_json)

    result = _run_verify(manifest)

    assert result.returncode == 0, result.stderr


def test_cli_can_check_expected_charter_and_policy_digests(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")

    matching = _run_verify(
        manifest,
        "--expected-charter-digest",
        "sha256:" + "2" * 64,
        "--expected-policy-digest",
        "sha256:" + "1" * 64,
    )
    mismatched = _run_verify(
        manifest,
        "--expected-charter-digest",
        "sha256:" + "9" * 64,
    )

    assert matching.returncode == 0, matching.stderr
    assert mismatched.returncode == 1
    assert "charter_digest mismatch" in mismatched.stderr


def test_cli_cross_validates_policy_surface_digests(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    policy_surface = _write_policy_surface(manifest.parent)
    policy_digest, charter_digest = _bind_receipts_to_policy_surface(
        manifest,
        policy_surface,
    )

    result = _run_verify(manifest, "--policy-surface", str(policy_surface), "--json")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["manifest"] == "<redacted>"
    assert report["chain_id"] == "<redacted>"
    assert report["policy_surface"]["provided"] is True
    assert report["policy_surface"]["policy_id"] == "<redacted>"
    assert report["policy_surface"]["canonicalization"] == "magma-jcs-subset-v1"
    assert report["policy_surface"]["policy_digest"] == policy_digest
    assert report["policy_surface"]["charter_digest"] == charter_digest
    assert str(tmp_path) not in result.stdout
    assert "policy_surface_v0.json" not in result.stdout


def test_cli_rejects_policy_surface_digest_mismatch(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    policy_surface = _write_policy_surface(manifest.parent)
    _bind_receipts_to_policy_surface(manifest, policy_surface)
    receipt_path = manifest.parent / "receipt-002.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["policy_digest"] = "sha256:" + "9" * 64
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--policy-surface", str(policy_surface))

    assert result.returncode == 1
    assert "policy_digest mismatch" in result.stderr


def test_cli_rejects_invalid_policy_surface_before_digest_binding(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    policy_surface = _write_policy_surface(manifest.parent)
    policy = json.loads(policy_surface.read_text(encoding="utf-8"))
    policy["digest_bindings"]["canonicalization"] = "unknown-json-canonical"
    _write_json(policy_surface, policy)

    result = _run_verify(manifest, "--policy-surface", str(policy_surface))

    assert result.returncode == 1
    assert "policy_surface" in result.stderr
    assert "canonicalization" in result.stderr


def test_cli_redacts_invalid_policy_surface_schema_values(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    policy_surface = _write_policy_surface(manifest.parent)
    policy = json.loads(policy_surface.read_text(encoding="utf-8"))
    policy["title"] = "Leak _DO_NOT_LEAK https://example.invalid sk-test"
    _write_json(policy_surface, policy)

    result = _run_verify(manifest, "--policy-surface", str(policy_surface), "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "policy_surface" in combined
    assert "_DO_NOT_LEAK" not in combined
    assert "https://example.invalid" not in combined
    assert "sk-test" not in combined


def test_cli_redacts_missing_paths_and_schema_instance_values(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_json["chain_id"] = "chain_DO_NOT_LEAK"
    manifest_json["entries"][0]["receipt"] = "missing_receipt_DO_NOT_LEAK.json"
    _write_json(manifest, manifest_json)

    missing = _run_verify(manifest, "--json")
    assert missing.returncode == 1
    assert "_DO_NOT_LEAK" not in missing.stdout + missing.stderr
    assert str(tmp_path) not in missing.stdout + missing.stderr

    manifest = _write_chain(tmp_path / "schema-chain")
    receipt_path = manifest.parent / "receipt-001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["event_id"] = "bad value sk-DO_NOT_LEAK"
    _write_json(receipt_path, receipt)

    schema_error = _run_verify(manifest, "--json")
    assert schema_error.returncode == 1
    assert "sk-DO_NOT_LEAK" not in schema_error.stdout + schema_error.stderr


def test_cli_redacts_raw_digest_field_values_in_mismatch_errors(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    receipt_path = manifest.parent / "receipt-001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["canonical_payload_digest"] = (
        "sha256:_DO_NOT_LEAK_sk-test_https://example.invalid/canary"
    )
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "canonical_payload_digest mismatch" in combined
    assert "_DO_NOT_LEAK" not in combined
    assert "https://example.invalid" not in combined
    assert "sk-test" not in combined


def test_cli_rejects_policy_surface_argument_digest_conflict(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    policy_surface = _write_policy_surface(manifest.parent)
    _bind_receipts_to_policy_surface(manifest, policy_surface)

    result = _run_verify(
        manifest,
        "--policy-surface",
        str(policy_surface),
        "--expected-policy-digest",
        "sha256:" + "8" * 64,
    )

    assert result.returncode == 1
    assert "expected_policy_digest argument mismatch" in result.stderr


def test_cli_rejects_tampered_payload(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    payload_path = manifest.parent / "payload-001.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["requires_operator"] = False
    payload["secret"] = "synthetic_secret_value_DO_NOT_LEAK"
    _write_json(payload_path, payload)

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "canonical_payload_digest mismatch" in result.stderr
    assert "synthetic_secret_value_DO_NOT_LEAK" not in result.stdout + result.stderr


def test_cli_rejects_changed_evaluation_result(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    evaluation_path = manifest.parent / "evaluation-002.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["reason_codes"] = ["changed_after_receipt"]
    _write_json(evaluation_path, evaluation)

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "evaluation_result_digest mismatch" in result.stderr


def test_cli_rejects_intermediate_receipt_tamper(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    receipt_path = manifest.parent / "receipt-001.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["policy_digest"] = "sha256:" + "6" * 64
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "missing_parent" in result.stderr
    assert "orphan_receipt" in result.stderr


def test_cli_rejects_multiple_genesis_receipts(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    receipt_path = manifest.parent / "receipt-002.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["prev_receipt_hash"] = None
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "multiple_genesis" in result.stderr


def test_cli_rejects_broken_prev_receipt_hash(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    receipt_path = manifest.parent / "receipt-002.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["prev_receipt_hash"] = "sha256:" + "0" * 64
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "missing_parent" in result.stderr
    assert "orphan_receipt" in result.stderr

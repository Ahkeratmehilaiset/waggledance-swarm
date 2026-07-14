from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.chat_served_receipt import (
    build_chat_served_summary,
    write_chat_served_receipt_bundle,
)
from tools.verify_magma_receipt import verify_manifest


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


def _write_chat_chain(root: Path) -> Path:
    summary = build_chat_served_summary(
        query="private query",
        response="private response",
        route_type="solver",
        source="solver",
        confidence=0.95,
        latency_ms=12.3,
        cached=False,
        round_table=False,
        agent_id=None,
        language="en",
        profile="COTTAGE",
        world_snapshot_ref="snap-1",
        route_stage_trace=[
            {
                "stage": "route_selection",
                "route_type": "solver",
                "solver_intent": "math",
                "memory_score": 0.0,
            }
        ],
    )
    write_chat_served_receipt_bundle(
        out_dir=root,
        summary_payload=summary,
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        verify_manifest=verify_manifest,
        ordinal=1,
    )
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


@pytest.mark.parametrize("target", ["manifest", "entry"])
def test_cli_rejects_extra_manifest_or_entry_keys(
    tmp_path: Path, target: str
) -> None:
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    if target == "manifest":
        manifest_json["extra"] = "ignored-before-c0"
    else:
        manifest_json["entries"][0]["extra"] = "ignored-before-c0"
    _write_json(manifest, manifest_json)

    result = _run_verify(manifest, "--json")

    assert result.returncode == 1
    assert "exactly" in result.stdout + result.stderr


def test_cli_keeps_generic_manifest_extra_key_compatibility(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_json["generic_extension"] = {"version": 1}
    manifest_json["entries"][0]["generic_extension"] = True
    _write_json(manifest, manifest_json)

    result = _run_verify(manifest, "--json")

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_rejects_wrong_chain_id_for_chat_payload(tmp_path: Path) -> None:
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_json["chain_id"] = "magma:fixture:wrong_chat_chain"
    _write_json(manifest, manifest_json)

    result = _run_verify(manifest, "--json")

    assert result.returncode == 1
    assert "chat_served chain_id mismatch" in result.stdout + result.stderr


def test_cli_rejects_rebound_chat_payload_with_raw_extra(tmp_path: Path) -> None:
    marker = "SECRET_REBOUND_RAW_PAYLOAD_DO_NOT_LEAK"
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    entry = manifest_json["entries"][0]
    payload_path = manifest.parent / entry["payload"]
    evaluation_path = manifest.parent / entry["evaluation_result"]
    receipt_path = manifest.parent / entry["receipt"]

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["raw_private_extra"] = marker
    payload_digest = sha256_digest(payload)
    evaluation["target_digest"] = payload_digest
    receipt["canonical_payload_digest"] = payload_digest
    receipt["evaluation_result_digest"] = sha256_digest(evaluation)
    _write_json(payload_path, payload)
    _write_json(evaluation_path, evaluation)
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "invalid or unsupported chat_served payload" in combined
    assert marker not in combined


@pytest.mark.parametrize(
    ("downgraded_version", "change_chain_id"),
    [
        ("magma.chat_served_receipt_payload.v0", False),
        ("attacker.unknown.v9", False),
        ("magma.chat_served_receipt_payload.v0", True),
    ],
)
def test_cli_rejects_chat_payload_version_downgrade(
    tmp_path: Path, downgraded_version: str, change_chain_id: bool
) -> None:
    marker = "SECRET_DOWNGRADE_RAW_PAYLOAD_DO_NOT_LEAK"
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    if change_chain_id:
        manifest_json["chain_id"] = "magma:fixture:attacker_reclassified"
        _write_json(manifest, manifest_json)
    entry = manifest_json["entries"][0]
    payload_path = manifest.parent / entry["payload"]
    evaluation_path = manifest.parent / entry["evaluation_result"]
    receipt_path = manifest.parent / entry["receipt"]

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["payload_version"] = downgraded_version
    payload["raw_private_extra"] = marker
    payload_digest = sha256_digest(payload)
    evaluation["target_digest"] = payload_digest
    receipt["canonical_payload_digest"] = payload_digest
    receipt["evaluation_result_digest"] = sha256_digest(evaluation)
    _write_json(payload_path, payload)
    _write_json(evaluation_path, evaluation)
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "invalid or unsupported chat_served payload" in combined
    assert marker not in combined


def test_cli_rejects_unknown_chat_shape_when_all_markers_are_relabelled(
    tmp_path: Path,
) -> None:
    marker = "SECRET_FULL_RELABEL_RAW_PAYLOAD"
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_json["chain_id"] = "magma:fixture:attacker_reclassified"
    entry = manifest_json["entries"][0]
    payload_path = manifest.parent / entry["payload"]
    evaluation_path = manifest.parent / entry["evaluation_result"]
    receipt_path = manifest.parent / entry["receipt"]
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["payload_version"] = "attacker.unknown.v9"
    payload["served_path"] = "Attacker.handle"
    payload["raw_private_extra"] = marker
    payload_digest = sha256_digest(payload)
    evaluation["case_id"] = "case:attacker:fixture"
    evaluation["target_digest"] = payload_digest
    evaluation["verifier_path"] = ["attacker_verifier"]
    evaluation["reason_codes"] = ["attacker_reclassified"]
    receipt["event_id"] = "magma:receipt:attacker:001"
    receipt["canonical_payload_digest"] = payload_digest
    receipt["evaluation_result_digest"] = sha256_digest(evaluation)
    receipt["rco_decision_digest"] = sha256_digest({"attacker": "rco"})
    receipt["solver_contract_digest"] = sha256_digest({"attacker": "solver"})
    _write_json(manifest, manifest_json)
    _write_json(payload_path, payload)
    _write_json(evaluation_path, evaluation)
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "invalid or unsupported chat_served payload" in combined
    assert marker not in combined


@pytest.mark.parametrize(
    "removed_fingerprint",
    [
        "query_digest",
        "response_digest",
        "route_stage_trace",
        "route_stage_trace_digest",
        "digest_semantics",
    ],
)
def test_cli_rejects_partially_stripped_unknown_chat_shape(
    tmp_path: Path, removed_fingerprint: str
) -> None:
    marker = "SECRET_PARTIALLY_STRIPPED_RAW_PAYLOAD"
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_json["chain_id"] = "magma:fixture:attacker_reclassified"
    entry = manifest_json["entries"][0]
    payload_path = manifest.parent / entry["payload"]
    evaluation_path = manifest.parent / entry["evaluation_result"]
    receipt_path = manifest.parent / entry["receipt"]
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["payload_version"] = "attacker.unknown.v9"
    payload["served_path"] = "Attacker.handle"
    payload["raw_private_extra"] = marker
    payload.pop(removed_fingerprint)
    payload_digest = sha256_digest(payload)
    evaluation["case_id"] = "case:attacker:fixture"
    evaluation["target_digest"] = payload_digest
    evaluation["verifier_path"] = ["attacker_verifier"]
    evaluation["reason_codes"] = ["attacker_reclassified"]
    receipt["event_id"] = "magma:receipt:attacker:001"
    receipt["canonical_payload_digest"] = payload_digest
    receipt["evaluation_result_digest"] = sha256_digest(evaluation)
    receipt["rco_decision_digest"] = sha256_digest({"attacker": "rco"})
    receipt["solver_contract_digest"] = sha256_digest({"attacker": "solver"})
    _write_json(manifest, manifest_json)
    _write_json(payload_path, payload)
    _write_json(evaluation_path, evaluation)
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "invalid or unsupported chat_served payload" in combined
    assert marker not in combined


def test_cli_rejects_huge_chat_number_without_crashing(tmp_path: Path) -> None:
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    entry = manifest_json["entries"][0]
    payload_path = manifest.parent / entry["payload"]
    evaluation_path = manifest.parent / entry["evaluation_result"]
    receipt_path = manifest.parent / entry["receipt"]
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["latency_ms"] = 10**400
    payload_digest = sha256_digest(payload)
    evaluation["target_digest"] = payload_digest
    receipt["canonical_payload_digest"] = payload_digest
    receipt["evaluation_result_digest"] = sha256_digest(evaluation)
    _write_json(payload_path, payload)
    _write_json(evaluation_path, evaluation)
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert any("invalid or unsupported chat_served payload" in e for e in report["errors"])


@pytest.mark.parametrize(
    "nonfinite",
    [float("nan"), float("inf"), float("-inf")],
)
def test_cli_rejects_noncanonical_chat_number_without_crashing(
    tmp_path: Path, nonfinite: float
) -> None:
    manifest = _write_chat_chain(tmp_path / "chat-chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    entry = manifest_json["entries"][0]
    payload_path = manifest.parent / entry["payload"]
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["latency_ms"] = nonfinite
    _write_json(payload_path, payload)

    result = _run_verify(manifest, "--json")

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert any("invalid JSON non-finite" in e for e in report["errors"])


def test_cli_rejects_evaluation_target_rebinding(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    entry = manifest_json["entries"][-1]
    evaluation_path = manifest.parent / entry["evaluation_result"]
    receipt_path = manifest.parent / entry["receipt"]
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evaluation["target_digest"] = sha256_digest({"unbound": True})
    receipt["evaluation_result_digest"] = sha256_digest(evaluation)
    _write_json(evaluation_path, evaluation)
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")

    assert result.returncode == 1
    assert "evaluation target_digest mismatch" in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("artifact_key", "filename_prefix"),
    [
        ("receipt", "receipt"),
        ("payload", "payload"),
        ("evaluation_result", "evaluation"),
    ],
)
def test_cli_rejects_non_object_bundle_artifacts(
    tmp_path: Path, artifact_key: str, filename_prefix: str
) -> None:
    manifest = _write_chain(tmp_path / "chain")
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    artifact_path = manifest.parent / manifest_json["entries"][0][artifact_key]
    _write_json(artifact_path, [f"{filename_prefix}_must_not_be_an_array"])

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert f"{artifact_key} must be a JSON object" in combined


def test_cli_rejects_manifest_entry_path_escape(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
    first = manifest_json["entries"][0]
    for field in ("receipt", "payload", "evaluation_result"):
        shutil.copy2(manifest.parent / first[field], outside / first[field])
        first[field] = "../outside/" + first[field]
    _write_json(manifest, manifest_json)

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "unsafe relative path" in combined
    assert str(outside) not in combined


def test_cli_rejects_manifest_entry_raw_unsafe_segments(tmp_path: Path) -> None:
    unsafe_templates = (
        "./{name}",
        "{name}/",
        "{name}/.",
        "nested//{name}",
    )
    for index, template in enumerate(unsafe_templates):
        manifest = _write_chain(tmp_path / f"chain-{index}")
        manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
        first = manifest_json["entries"][0]
        first["receipt"] = template.format(name=first["receipt"])
        _write_json(manifest, manifest_json)

        result = _run_verify(manifest, "--json")
        combined = result.stdout + result.stderr

        assert result.returncode == 1, combined
        assert "receipt unsafe relative path" in combined


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


def test_cli_redacts_raw_prev_receipt_hash_in_topology_errors(tmp_path: Path) -> None:
    manifest = _write_chain(tmp_path / "chain")
    receipt_path = manifest.parent / "receipt-002.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["prev_receipt_hash"] = (
        "sha256:_DO_NOT_LEAK_sk-test_https://example.invalid/canary"
    )
    _write_json(receipt_path, receipt)

    result = _run_verify(manifest, "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "missing_parent" in combined
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


def test_cli_rejects_non_external_receipt_with_stale_approval_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "chain"
    root.mkdir()
    payload = {"action": "local_artifact", "index": 1}
    payload_digest = sha256_digest(payload)
    evaluation = _evaluation(
        case_id="case:magma:fixture:local-stale-approval",
        payload_digest=payload_digest,
    )
    evaluation["risk_class"] = "local_artifact"
    evaluation["expected_gate"] = "review"
    evaluation["actual_gate"] = "review"
    evaluation["verifier_path"] = ["schema", "local_artifact"]
    evaluation["reason_codes"] = ["local_artifact_no_operator_approval"]
    evaluation["operator_required"] = False
    receipt = _receipt(
        event_id="magma:receipt:fixture:local-stale-approval",
        payload_digest=payload_digest,
        evaluation_digest=sha256_digest(evaluation),
        prev_hash=None,
    )
    receipt["risk_class"] = "local_artifact"
    receipt["operator_gate_required"] = False

    _write_json(root / "payload.json", payload)
    _write_json(root / "evaluation.json", evaluation)
    _write_json(root / "receipt.json", receipt)
    _write_json(
        root / "manifest.json",
        {
            "chain_id": "magma:fixture:stale_approval",
            "entries": [
                {
                    "payload": "payload.json",
                    "evaluation_result": "evaluation.json",
                    "receipt": "receipt.json",
                }
            ],
        },
    )

    result = _run_verify(root / "manifest.json", "--json")
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "approval_id" in combined


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

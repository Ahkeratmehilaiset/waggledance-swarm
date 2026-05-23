# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_rco_receipt_binding_demo import verify_rco_receipt_binding
from tools.run_write_rco_route_receipt_emission_proof import (
    AXIS_ID,
    CHAIN_ID,
    CLAIM_LABEL,
    REPORT_VERSION,
    build_write_rco_route_receipt_emission_proof,
)
from tools.verify_magma_receipt import verify_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_write_rco_route_receipt_emission_proof.py"
FIXED_NOW = datetime(2026, 5, 23, 11, 45, tzinfo=timezone.utc)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _entry_path(manifest_path: Path, index: int, field: str) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest_path.parent / manifest["entries"][index][field]


def _all_json_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.json"))
    )


def test_proof_emits_verified_route_receipt_chain(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-route-proof"

    report = build_write_rco_route_receipt_emission_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-05-23T11:45:00Z"
    assert report["axis_id"] == AXIS_ID
    assert report["claim_label"] == CLAIM_LABEL
    assert report["chain_id"] == CHAIN_ID
    assert report["runtime_path"] == "WriteRCOGate.route"
    assert report["route_count"] == 2
    assert [route["risk_class"] for route in report["route_summaries"]] == [
        "local_artifact",
        "external_effect",
    ]
    assert all(route["approved"] is True for route in report["route_summaries"])
    assert report["receipt_count"] == 2
    assert report["verifier_ok"] is True
    assert report["rco_binding_ok"] is True
    assert report["raw_payload_leak_check"] is True
    assert report["sink_none_preserved"] is True
    assert report["external_writes_applied"] is False
    assert report["external_effect_authority_change"] is False
    assert report["receipt_emission_mode"] == "opt_in_route_sink"
    assert report["default_sink_required"] is False
    assert report["external_effect_approval_id"] == "approval:write-rco-route-proof:001"
    assert report["no_overclaim_guardrails"] == {
        "not_a_competitor_benchmark": True,
        "no_consensus_grade_promotion": True,
        "no_release_boundary_change": True,
        "claim_label_remains_partial": True,
        "route_only_no_execute": True,
    }

    manifest_path = Path(report["receipt_manifest"])
    assert verify_manifest(manifest_path)["ok"] is True
    assert verify_rco_receipt_binding(manifest_path)["ok"] is True


def test_proof_chain_links_second_route_to_first(tmp_path: Path) -> None:
    report = build_write_rco_route_receipt_emission_proof(
        out_dir=tmp_path / "write-rco-route-proof",
        now_utc=FIXED_NOW,
    )
    receipt_dir = Path(report["receipt_out_dir"])
    local_receipt = json.loads(
        (receipt_dir / "receipt-001-local.json").read_text(encoding="utf-8")
    )
    external_receipt = json.loads(
        (receipt_dir / "receipt-002-external.json").read_text(encoding="utf-8")
    )

    from waggledance.core.magma.canonical import sha256_digest

    assert local_receipt["prev_receipt_hash"] is None
    assert external_receipt["prev_receipt_hash"] == sha256_digest(local_receipt)
    assert external_receipt["operator_gate_required"] is True
    assert external_receipt["approval_id"] == "approval:write-rco-route-proof:001"


def test_proof_detects_tampered_payload(tmp_path: Path) -> None:
    report = build_write_rco_route_receipt_emission_proof(
        out_dir=tmp_path / "tampered-payload",
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_manifest"])
    payload_path = _entry_path(manifest_path, 0, "payload")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["action"] = "tampered"
    payload_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_manifest(manifest_path)

    assert verification["ok"] is False
    assert "entry 1: canonical_payload_digest mismatch" in verification["errors"]


def test_proof_detects_tampered_rco_artifact(tmp_path: Path) -> None:
    report = build_write_rco_route_receipt_emission_proof(
        out_dir=tmp_path / "tampered-rco",
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_manifest"])
    rco_path = _entry_path(manifest_path, 1, "rco_decision_artifact")
    rco = json.loads(rco_path.read_text(encoding="utf-8"))
    rco["gate_decision"] = "review"
    rco_path.write_text(
        json.dumps(rco, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    binding = verify_rco_receipt_binding(manifest_path)

    assert binding["ok"] is False
    assert any("rco_decision_digest mismatch" in error for error in binding["errors"])


def test_proof_does_not_leak_private_markers(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-route-proof"

    build_write_rco_route_receipt_emission_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    emitted_text = _all_json_text(out_dir)
    assert "write_rco_route_receipt_private" not in emitted_text
    assert "DO_NOT_LEAK" not in emitted_text


def test_proof_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "existing"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="out_dir must not exist"):
        build_write_rco_route_receipt_emission_proof(
            out_dir=out_dir,
            now_utc=FIXED_NOW,
        )


def test_cli_json_reports_write_rco_route_proof(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-route-cli-proof"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-23T11:45:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["axis_id"] == AXIS_ID
    assert payload["claim_label"] == CLAIM_LABEL
    assert payload["receipt_count"] == 2
    assert payload["rco_binding_ok"] is True
    assert "DO_NOT_LEAK" not in result.stdout


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-route-cli-existing"
    out_dir.mkdir()

    result = _run("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr

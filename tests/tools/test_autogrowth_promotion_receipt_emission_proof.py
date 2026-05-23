# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_autogrowth_promotion_receipt_emission_proof import (
    AXIS_ID,
    CHAIN_ID,
    CLAIM_LABEL,
    REPORT_VERSION,
    build_autogrowth_promotion_receipt_emission_proof,
)
from tools.verify_magma_receipt import verify_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_autogrowth_promotion_receipt_emission_proof.py"
FIXED_NOW = datetime(2026, 5, 23, 13, 30, tzinfo=timezone.utc)


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


def test_proof_emits_scheduler_to_engine_receipt(tmp_path: Path) -> None:
    out_dir = tmp_path / "autogrowth-proof"

    report = build_autogrowth_promotion_receipt_emission_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-05-23T13:30:00Z"
    assert report["axis_id"] == AXIS_ID
    assert report["claim_label"] == CLAIM_LABEL
    assert report["chain_id"] == CHAIN_ID
    assert report["runtime_path"] == (
        "AutogrowthScheduler.tick -> LowRiskGrower.grow_from_gap -> "
        "AutoPromotionEngine.evaluate_candidate"
    )
    assert report["risk_class"] == "local_artifact"
    assert report["family_kind"] == "scalar_unit_conversion"
    assert report["solver_name"] == "autogrowth_receipt_solver_i0001"
    assert report["transitions"] == ["auto_promoted"]
    assert report["receipt_count"] == 1
    assert report["verifier_ok"] is True
    assert report["raw_payload_leak_check"] is True
    assert report["sink_none_preserved"] is True
    assert report["external_effect_authority_change"] is False
    assert report["operator_gate_required"] is False
    assert report["external_writes_applied"] is False
    assert report["local_artifacts_written"] is True
    assert report["receipt_emission_mode"] == "opt_in_disk_bundle_sink"
    assert report["default_sink_required"] is False
    assert report["scheduler_outcome"]["outcome"] == "auto_promoted"
    assert report["no_sink_scheduler_outcome"]["outcome"] == "auto_promoted"
    assert report["no_overclaim_guardrails"] == {
        "not_a_competitor_benchmark": True,
        "no_consensus_grade_promotion": True,
        "no_release_boundary_change": True,
        "claim_label_remains_partial": True,
    }

    manifest_path = Path(report["receipt_manifest"])
    assert manifest_path.exists()
    verifier = verify_manifest(manifest_path)
    assert verifier["ok"] is True
    assert verifier["receipt_count"] == 1


def test_proof_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "autogrowth-proof"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="out_dir must not exist"):
        build_autogrowth_promotion_receipt_emission_proof(
            out_dir=out_dir,
            now_utc=FIXED_NOW,
        )


def test_proof_detects_tampered_payload(tmp_path: Path) -> None:
    report = build_autogrowth_promotion_receipt_emission_proof(
        out_dir=tmp_path / "tampered-payload",
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_manifest"])
    payload_path = _entry_path(manifest_path, 0, "payload")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["decision"] = "tampered"
    payload_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_manifest(manifest_path)

    assert verification["ok"] is False
    assert "entry 1: canonical_payload_digest mismatch" in verification["errors"]


def test_proof_does_not_leak_private_markers(tmp_path: Path) -> None:
    out_dir = tmp_path / "autogrowth-proof"

    build_autogrowth_promotion_receipt_emission_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    emitted_text = _all_json_text(out_dir)
    assert "private autogrowth" not in emitted_text
    assert "DO_NOT_LEAK" not in emitted_text


def test_cli_json_reports_autogrowth_proof(tmp_path: Path) -> None:
    out_dir = tmp_path / "autogrowth-cli-proof"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-23T13:30:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["axis_id"] == AXIS_ID
    assert payload["claim_label"] == CLAIM_LABEL
    assert payload["transitions"] == ["auto_promoted"]
    assert payload["receipt_count"] == 1
    assert "DO_NOT_LEAK" not in result.stdout


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "autogrowth-cli-existing"
    out_dir.mkdir()

    result = _run("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr

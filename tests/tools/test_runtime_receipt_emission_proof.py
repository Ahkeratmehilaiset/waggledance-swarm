# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_runtime_receipt_emission_proof import (
    REPORT_VERSION,
    build_runtime_receipt_emission_proof,
)
from tools.verify_magma_receipt import verify_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_runtime_receipt_emission_proof.py"
FIXED_NOW = datetime(2026, 5, 23, 7, 0, tzinfo=timezone.utc)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _all_json_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.json"))
    )


def _entry_path(manifest_path: Path, field: str) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest_path.parent / manifest["entries"][0][field]


def test_runtime_receipt_emission_proof_writes_verified_sanitized_bundle(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "proof"

    report = build_runtime_receipt_emission_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-05-23T07:00:00Z"
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["runtime_path"] == "AutonomyRuntime.handle_query"
    assert report["result_has_runtime_receipt"] is True
    assert report["result_executed"] is True
    assert report["actual_gate"] == "allow"
    assert report["verdict"] == "pass"
    assert report["receipt_count"] == 1
    assert report["verifier_ok"] is True
    assert report["raw_payload_leak_check"] is True
    assert report["external_effect_authority_change"] is False
    assert report["operator_gate_required"] is False
    assert report["external_writes_applied"] is False
    assert report["local_artifacts_written"] is True
    assert report["receipt_emission_mode"] == "opt_in_disk_bundle_sink"
    assert report["default_sink_required"] is False
    assert report["sink_none_preserved"] is True
    assert "runtime_receipt" in report["result_keys"]
    assert Path(report["receipt_manifest"]).exists()
    assert (out_dir / "runtime_receipt_emission_proof.json").exists()

    emitted_text = _all_json_text(out_dir)
    assert "private runtime query" not in emitted_text
    assert "context secret" not in emitted_text
    assert "DO_NOT_LEAK" not in emitted_text


def test_runtime_receipt_emission_proof_refuses_existing_output_dir(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "existing"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="out_dir must not exist"):
        build_runtime_receipt_emission_proof(out_dir=out_dir, now_utc=FIXED_NOW)


def test_runtime_receipt_emission_proof_detects_tampered_payload(
    tmp_path: Path,
) -> None:
    report = build_runtime_receipt_emission_proof(
        out_dir=tmp_path / "tampered-payload",
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_manifest"])
    payload_path = _entry_path(manifest_path, "payload")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["verdict"] = "review"
    payload_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_manifest(manifest_path)

    assert verification["ok"] is False
    assert "entry 1: canonical_payload_digest mismatch" in verification["errors"]


def test_runtime_receipt_emission_proof_detects_tampered_evaluation_result(
    tmp_path: Path,
) -> None:
    report = build_runtime_receipt_emission_proof(
        out_dir=tmp_path / "tampered-evaluation",
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_manifest"])
    evaluation_path = _entry_path(manifest_path, "evaluation_result")
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["verdict"] = "review"
    evaluation_path.write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_manifest(manifest_path)

    assert verification["ok"] is False
    assert "entry 1: evaluation_result_digest mismatch" in verification["errors"]


def test_runtime_receipt_emission_proof_cli_json(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli-proof"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-23T07:00:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["verifier_ok"] is True
    assert payload["raw_payload_leak_check"] is True
    assert "DO_NOT_LEAK" not in result.stdout


def test_runtime_receipt_emission_proof_cli_rejects_non_utc_now(
    tmp_path: Path,
) -> None:
    result = _run(
        "--json",
        "--out-dir",
        str(tmp_path / "bad-now"),
        "--now",
        "2026-05-23T10:00:00+03:00",
    )

    assert result.returncode == 1
    assert "UTC timestamp ending in Z" in result.stderr

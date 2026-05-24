from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys

from tools.run_rco_receipt_binding_demo import (
    build_rco_receipt_binding_demo,
    verify_rco_receipt_binding,
)
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_rco_receipt_binding_demo.py"


def _run_demo(out_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(out_dir), "--json", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_builds_rco_artifact_bound_to_verified_magma_receipt(tmp_path: Path) -> None:
    out_dir = tmp_path / "rco-demo"

    report = build_rco_receipt_binding_demo(
        out_dir=out_dir,
        now_utc=datetime.fromisoformat("2026-05-20T12:00:00+00:00"),
    )

    assert report["demo_version"] == "magma.rco_receipt_binding_demo.v0"
    assert report["writes_applied"] is False
    assert report["binding_report"]["ok"] is True
    assert report["binding_report"]["receipt_count"] == 1
    assert report["binding_report"]["rco_artifact_count"] == 1
    assert verify_manifest(out_dir / "manifest.json")["ok"] is True

    rco_artifact = _read_json(out_dir / "rco-decision-001.json")
    receipt = _read_json(out_dir / "receipt-001.json")
    evaluation = _read_json(out_dir / "evaluation-001.json")
    intent = _read_json(out_dir / "intent-001.json")
    assert receipt["rco_decision_digest"] == sha256_digest(rco_artifact)
    assert receipt["canonical_payload_digest"] == sha256_digest(intent)
    assert evaluation["target_digest"] == sha256_digest(intent)
    assert evaluation["actual_gate"] == rco_artifact["gate_decision"]
    assert rco_artifact["write_payload_digest"].startswith("sha256:")


def test_tampered_rco_artifact_breaks_binding_report(tmp_path: Path) -> None:
    out_dir = tmp_path / "rco-demo"
    build_rco_receipt_binding_demo(
        out_dir=out_dir,
        now_utc=datetime.fromisoformat("2026-05-20T12:00:00+00:00"),
    )
    rco_path = out_dir / "rco-decision-001.json"
    rco_artifact = _read_json(rco_path)
    rco_artifact["gate_decision"] = "allow"
    rco_path.write_text(json.dumps(rco_artifact, indent=2, sort_keys=True), encoding="utf-8")

    report = verify_rco_receipt_binding(out_dir / "manifest.json")

    assert report["ok"] is False
    assert any("rco_decision_digest mismatch" in error for error in report["errors"])
    assert any("actual_gate does not match" in error for error in report["errors"])


def test_rejects_rco_artifact_path_escape(tmp_path: Path) -> None:
    out_dir = tmp_path / "rco-demo"
    build_rco_receipt_binding_demo(
        out_dir=out_dir,
        now_utc=datetime.fromisoformat("2026-05-20T12:00:00+00:00"),
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy2(
        out_dir / "rco-decision-001.json",
        outside / "rco-decision-001.json",
    )
    manifest_path = out_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["entries"][0]["rco_decision_artifact"] = (
        "../outside/rco-decision-001.json"
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = verify_rco_receipt_binding(manifest_path)

    assert report["ok"] is False
    assert any(
        "rco_decision_artifact unsafe relative path" in error
        for error in report["errors"]
    )
    assert str(outside) not in "\n".join(report["errors"])


def test_cli_emits_json_and_does_not_leak_private_marker(tmp_path: Path) -> None:
    out_dir = tmp_path / "rco-demo"

    result = _run_demo(out_dir)

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["binding_report"]["ok"] is True
    combined = result.stdout + result.stderr
    for path in out_dir.glob("*.json"):
        combined += path.read_text(encoding="utf-8")
    assert "operator_rco_secret_marker_DO_NOT_LEAK" not in combined


def test_cli_rejects_non_empty_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "rco-demo"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("keep\n", encoding="utf-8")

    result = _run_demo(out_dir)

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr

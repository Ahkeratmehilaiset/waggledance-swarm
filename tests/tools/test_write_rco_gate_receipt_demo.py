from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from tools.run_rco_receipt_binding_demo import verify_rco_receipt_binding
from tools.run_write_rco_gate_receipt_demo import build_write_rco_gate_receipt_demo
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_write_rco_gate_receipt_demo.py"


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


def test_real_write_rco_route_is_bound_to_receipt_bundle(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-demo"

    report = build_write_rco_gate_receipt_demo(
        out_dir=out_dir,
        now_utc=datetime.fromisoformat("2026-05-20T12:30:00+00:00"),
    )

    assert report["demo_version"] == "magma.write_rco_gate_receipt_demo.v0"
    assert report["writes_applied"] is False
    assert report["gate_outcome"]["risk_class"] == "local_artifact"
    assert report["gate_outcome"]["approved"] is True
    assert report["audit_event_count"] == 2
    assert report["binding_report"]["ok"] is True
    assert verify_manifest(out_dir / "manifest.json")["ok"] is True
    assert verify_rco_receipt_binding(out_dir / "manifest.json")["ok"] is True

    intent = _read_json(out_dir / "intent-001.json")
    rco_artifact = _read_json(out_dir / "rco-decision-001.json")
    evaluation = _read_json(out_dir / "evaluation-001.json")
    receipt = _read_json(out_dir / "receipt-001.json")
    assert rco_artifact["intent_digest"] == sha256_digest(intent)
    assert rco_artifact["gate_decision"] == "allow"
    assert receipt["rco_decision_digest"] == sha256_digest(rco_artifact)
    assert receipt["canonical_payload_digest"] == sha256_digest(intent)
    assert evaluation["actual_gate"] == rco_artifact["gate_decision"]


def test_cli_emits_json_and_no_private_marker(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-demo"

    result = _run_demo(out_dir)

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["binding_report"]["ok"] is True
    combined = result.stdout + result.stderr
    for path in out_dir.glob("*.json"):
        combined += path.read_text(encoding="utf-8")
    assert "write_rco_route_secret_DO_NOT_LEAK" not in combined


def test_tampered_rco_artifact_breaks_route_demo_binding(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-demo"
    build_write_rco_gate_receipt_demo(
        out_dir=out_dir,
        now_utc=datetime.fromisoformat("2026-05-20T12:30:00+00:00"),
    )
    rco_path = out_dir / "rco-decision-001.json"
    rco_artifact = _read_json(rco_path)
    rco_artifact["gate_decision"] = "review"
    rco_path.write_text(json.dumps(rco_artifact, indent=2, sort_keys=True), encoding="utf-8")

    report = verify_rco_receipt_binding(out_dir / "manifest.json")

    assert report["ok"] is False
    assert any("rco_decision_digest mismatch" in error for error in report["errors"])


def test_cli_refuses_existing_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "write-rco-demo"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("keep\n", encoding="utf-8")

    result = _run_demo(out_dir)

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr

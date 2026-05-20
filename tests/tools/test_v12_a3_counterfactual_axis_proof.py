from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_a3_counterfactual_axis_proof import (
    build_a3_counterfactual_axis_proof,
    render_markdown,
)
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_a3_counterfactual_axis_proof.py"
FIXED_NOW = datetime(2026, 5, 20, 19, 50, tzinfo=timezone.utc)


def _run_a3(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_a3_axis_proof_reports_counterfactual_delta_without_writes() -> None:
    report = build_a3_counterfactual_axis_proof(now_utc=FIXED_NOW)

    assert report["report_version"] == "wd.v12.a3_counterfactual_axis_proof.v0"
    assert report["ok"] is True
    assert report["axis_id"] == "A3"
    assert report["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert report["writes_applied"] is False
    assert report["counterfactual_delta_proven"] is True
    assert report["delta"] == {
        "actual_gate": ["review", "allow"],
        "kind": ["KEEP_WIP", "CLOSE_OK"],
        "verdict": ["pass", "review"],
    }
    assert set(report["delta_fields"]) == {"actual_gate", "kind", "verdict"}
    assert report["receipt_chain_verified"] is False


def test_a3_axis_proof_writes_verified_receipt_chain(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    report = build_a3_counterfactual_axis_proof(
        receipt_out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["receipt_chain_verified"] is True
    assert report["receipt_bundle"]["available"] is True
    assert report["receipt_bundle"]["receipt_count"] == 2
    assert (out_dir / "manifest.json").exists()
    first_receipt = json.loads((out_dir / "receipt-001-factual.json").read_text(encoding="utf-8"))
    second_receipt = json.loads((out_dir / "receipt-002-counterfactual.json").read_text(encoding="utf-8"))
    assert second_receipt["prev_receipt_hash"] == sha256_digest(first_receipt)


def test_a3_markdown_preserves_no_rival_benchmark_guardrail(tmp_path: Path) -> None:
    report = build_a3_counterfactual_axis_proof(
        receipt_out_dir=tmp_path / "a3-receipts",
        now_utc=FIXED_NOW,
    )
    markdown = render_markdown(report)

    assert "V12 A3 Counterfactual Axis Proof" in markdown
    assert "counterfactual_delta_proven: `true`" in markdown
    assert "receipt_chain_verified: `true`" in markdown
    assert "not a rival benchmark" in markdown


def test_a3_cli_json_with_receipts_is_deterministic(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    result = _run_a3(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-20T19:50:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["generated_at_utc"] == "2026-05-20T19:50:00Z"
    assert payload["counterfactual_delta_proven"] is True
    assert payload["receipt_chain_verified"] is True
    assert payload["receipt_bundle"]["receipt_count"] == 2


def test_a3_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = _run_a3(
        "--json",
        "--out-dir",
        str(tmp_path / "a3-receipts"),
        "--now",
        "2026-05-20T22:50:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr


def test_a3_output_does_not_leak_private_marker(tmp_path: Path) -> None:
    out_dir = tmp_path / "a3-receipts"

    result = _run_a3("--json", "--out-dir", str(out_dir))

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    for path in out_dir.rglob("*.json"):
        combined += path.read_text(encoding="utf-8")
    assert "operator_secret_goal_marker_DO_NOT_LEAK" not in combined

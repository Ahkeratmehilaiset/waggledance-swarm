from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_supervisor_demo_pack import build_demo_pack


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_supervisor_demo_pack.py"


def _run_demo(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_demo_pack_writes_verified_evidence(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo-pack"

    result = build_demo_pack(out_dir=out_dir, now_utc=_fixed_now())

    assert result["adversarial_case_count"] == 15
    assert result["adversarial_pass_count"] == 15
    assert result["writes_applied"] is False
    assert result["receipt_verifier_ok"] is True
    assert result["receipt_count"] == 1
    assert result["high_criticality_gap_count"] == 0
    assert result["status_counts"]["receipt_bound"] >= 6
    assert result["a3_counterfactual_delta_proven"] is True
    assert result["a3_receipt_chain_verified"] is True
    assert result["rival_local_check_pass_count"] == 0
    assert result["rival_local_check_required_count"] == 4
    assert result["competitor_consensus_grade"] is False
    assert (out_dir / "summary.md").exists()
    assert (out_dir / "adversarial_eval_report.json").exists()
    assert (out_dir / "adversarial_receipts" / "manifest.json").exists()
    assert (out_dir / "receipt_verifier_report.json").exists()
    assert (out_dir / "receipt_adoption_report.md").exists()
    assert (out_dir / "a3_counterfactual_axis_proof.json").exists()
    assert (out_dir / "a3_counterfactual_axis_proof.md").exists()
    assert (out_dir / "a3_counterfactual_receipts" / "manifest.json").exists()
    assert (out_dir / "rival_local_check_matrix.json").exists()
    assert (out_dir / "rival_local_check_matrix.md").exists()

    report = json.loads((out_dir / "adversarial_eval_report.json").read_text(encoding="utf-8"))
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
    assert report["writes_applied"] is False
    assert report["full_match_count"] == 15
    rival_matrix = json.loads((out_dir / "rival_local_check_matrix.json").read_text(encoding="utf-8"))
    assert rival_matrix["consensus_grade"] is False
    assert rival_matrix["rival_local_checks_status"] == "0/4 rival local checks passed"
    a3_proof = json.loads((out_dir / "a3_counterfactual_axis_proof.json").read_text(encoding="utf-8"))
    assert a3_proof["counterfactual_delta_proven"] is True
    assert a3_proof["receipt_chain_verified"] is True

    summary = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "WD V12 Supervisor Demo Pack" in summary
    assert "15/15" in summary
    assert "writes_applied: `false`" in summary
    assert "A3 counterfactual delta proven: `true`" in summary
    assert "A3 receipt chain verified: `true`" in summary
    assert "rival local checks passed: `0/4`" in summary
    assert "competitor consensus grade: `false`" in summary


def test_demo_pack_does_not_leak_hidden_expectations_or_canaries(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "demo-pack"

    build_demo_pack(out_dir=out_dir, now_utc=_fixed_now())

    combined = ""
    for path in out_dir.rglob("*.json"):
        combined += path.read_text(encoding="utf-8")
    for path in out_dir.rglob("*.md"):
        combined += path.read_text(encoding="utf-8")

    assert "_DO_NOT_LEAK" not in combined
    assert "peer_review_trap_marker" not in combined
    assert "should_claude_catch" not in combined
    assert "should_codex_catch" not in combined
    assert "expected_verdict" not in combined


def test_cli_json_reports_demo_pack(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo-pack"

    result = _run_demo(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-20T18:50:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["demo_version"] == "wd.v12.supervisor_demo_pack.v0"
    assert payload["adversarial_case_count"] == 15
    assert payload["receipt_verifier_ok"] is True
    assert payload["a3_counterfactual_delta_proven"] is True
    assert payload["a3_receipt_chain_verified"] is True
    assert payload["rival_local_check_pass_count"] == 0
    assert payload["competitor_consensus_grade"] is False
    assert (out_dir / "summary.md").exists()


def test_cli_refuses_existing_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo-pack"
    out_dir.mkdir()

    result = _run_demo("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_cli_rejects_non_utc_timestamp(tmp_path: Path) -> None:
    result = _run_demo(
        "--out-dir",
        str(tmp_path / "demo-pack"),
        "--now",
        "2026-05-20T21:50:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr


def _fixed_now() -> datetime:
    return datetime(2026, 5, 20, 18, 50, tzinfo=timezone.utc)

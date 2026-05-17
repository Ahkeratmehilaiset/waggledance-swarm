from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.run_magma_composition_demo import build_composition_demo
from tools.verify_magma_receipt import verify_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_magma_composition_demo.py"


def _run_demo(out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(out_dir), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_demo_text(out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(out_dir)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_builds_receipt_chain_that_offline_verifier_accepts(tmp_path: Path) -> None:
    out_dir = tmp_path / "composition"

    report = build_composition_demo(out_dir=out_dir)

    assert report["demo_version"] == "magma.composition_demo.v0"
    assert report["writes_applied"] is False
    assert report["case_count"] == 3
    assert report["chain_length"] == 3
    assert report["case_ids"] == [
        "case:adv:subtle_drift:001",
        "case:adv:payload_leak:001",
        "case:adv:correlated_review_trap:001",
    ]
    assert report["skipped_external_effect_count"] >= 1
    assert report["verifier_report"]["ok"] is True
    assert report["verifier_report"]["receipt_count"] == 3
    assert verify_manifest(out_dir / "manifest.json")["ok"] is True


def test_default_cli_path_reports_success_without_json(tmp_path: Path) -> None:
    out_dir = tmp_path / "composition"

    result = _run_demo_text(out_dir)

    assert result.returncode == 0, result.stderr
    assert "magma composition demo OK: 3 receipts" in result.stdout
    assert (out_dir / "manifest.json").exists()


def test_receipts_bind_payload_evaluation_and_previous_receipt(tmp_path: Path) -> None:
    out_dir = tmp_path / "composition"

    build_composition_demo(out_dir=out_dir)

    first = _read_json(out_dir / "receipt-001.json")
    second = _read_json(out_dir / "receipt-002.json")
    third = _read_json(out_dir / "receipt-003.json")
    for index in range(1, 4):
        payload = _read_json(out_dir / f"payload-{index:03d}.json")
        evaluation = _read_json(out_dir / f"evaluation-{index:03d}.json")
        receipt = _read_json(out_dir / f"receipt-{index:03d}.json")
        assert evaluation["target_digest"] == receipt["canonical_payload_digest"]
        assert evaluation["risk_class"] == receipt["risk_class"]
        assert payload["payload_version"] == "magma.composition_payload.v0"

    assert first["prev_receipt_hash"] is None
    assert second["prev_receipt_hash"] is not None
    assert third["prev_receipt_hash"] is not None


def test_external_effect_cases_are_skipped_in_pure_demo(tmp_path: Path) -> None:
    out_dir = tmp_path / "composition"

    report = build_composition_demo(out_dir=out_dir)

    assert report["skipped_external_effect_count"] >= 1
    for index in range(1, 4):
        receipt = _read_json(out_dir / f"receipt-{index:03d}.json")
        assert receipt["risk_class"] != "external_effect"
        assert receipt["operator_gate_required"] is False
        assert receipt["approval_id"] is None


def test_demo_output_does_not_leak_privacy_or_hidden_expectation_fields(tmp_path: Path) -> None:
    out_dir = tmp_path / "composition"

    result = _run_demo(out_dir)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    for path in out_dir.glob("*.json"):
        combined += path.read_text(encoding="utf-8")
    assert "_DO_NOT_LEAK" not in combined
    assert "should_claude_catch" not in combined
    assert "should_codex_catch" not in combined
    assert "peer_review_trap_marker" not in combined
    assert "approval_wording_trap" not in combined
    assert "v0_expectations" not in combined


def test_cli_refuses_non_empty_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "composition"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("do not overwrite\n", encoding="utf-8")

    result = _run_demo(out_dir)

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_output_directory_parent_is_created(tmp_path: Path) -> None:
    out_dir = tmp_path / "nested" / "composition"

    report = build_composition_demo(out_dir=out_dir)

    assert out_dir.exists()
    assert (out_dir / "manifest.json").exists()
    assert report["verifier_report"]["ok"] is True

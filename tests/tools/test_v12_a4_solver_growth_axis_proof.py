# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_a4_solver_growth_axis_proof import (
    build_a4_solver_growth_axis_proof,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_a4_solver_growth_axis_proof.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_a4_solver_growth_axis_proof_writes_receipt_bundle(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "a4-proof"

    report = build_a4_solver_growth_axis_proof(
        out_dir=out_dir,
        now_utc=_fixed_now(),
    )

    assert report["ok"] is True
    assert report["axis_id"] == "A4"
    assert report["claim_label"] == "MEASURED_LOCAL_SYNTHETIC"
    assert report["solver_growth_proven"] is True
    assert report["release_gate_pass"] is True
    assert report["fixture"]["signals_total"] == 30
    assert report["fixture"]["candidates_total"] == 14
    assert report["fixture"]["allowlisted_candidate_count"] == 6
    assert report["registration"]["registered_solver_count"] == 6
    assert report["registration"]["rejected_registration_count"] == 8
    assert report["dispatch"]["dispatch_success_count"] == 30
    assert report["dispatch"]["dispatch_case_count"] == 30
    assert report["dispatch"]["dispatch_failure_count"] == 0
    assert report["dispatch"]["families_covered"] == 6
    assert report["receipt_chain_verified"] is True
    assert report["no_overclaim_guardrails"]["does_not_claim_learned_authority"] is True
    assert report["no_overclaim_guardrails"]["does_not_touch_production_control_plane"] is True
    assert (out_dir / "a4_solver_growth_axis_proof.json").exists()
    assert (out_dir / "a4_solver_growth_axis_proof.md").exists()
    assert (out_dir / "phase18c" / "mined_solver_runtime_dispatch_proof.json").exists()
    assert (out_dir / "a4_solver_growth_receipts" / "manifest.json").exists()


def test_render_markdown_carries_scope_caveat(tmp_path: Path) -> None:
    report = build_a4_solver_growth_axis_proof(
        out_dir=tmp_path / "a4-proof",
        now_utc=_fixed_now(),
    )

    markdown = render_markdown(report)

    assert "V12 A4 Solver-Growth Axis Proof" in markdown
    assert "solver_growth_proven: `true`" in markdown
    assert "dispatch successes | 30/30" in markdown
    assert "not a rival benchmark" in markdown
    assert "does not change production runtime authority" in markdown
    assert "does not claim learned policy authority" in markdown


def test_cli_json_reports_a4_axis(tmp_path: Path) -> None:
    result = _run(
        "--json",
        "--out-dir",
        str(tmp_path / "a4-proof"),
        "--now",
        "2026-05-20T18:50:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report_version"] == "wd.v12.a4_solver_growth_axis_proof.v0"
    assert payload["ok"] is True
    assert payload["solver_growth_proven"] is True
    assert payload["receipt_chain_verified"] is True
    assert payload["dispatch"]["dispatch_success_count"] == 30
    assert payload["registration"]["registered_solver_count"] == 6


def test_cli_can_pin_recorded_base_main_sha(tmp_path: Path) -> None:
    recorded_sha = "f5498d4f69fcbb764529f006325292178926ab58"
    out_dir = tmp_path / "a4-proof"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-21T18:55:00Z",
        "--recorded-base-main-sha",
        recorded_sha,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["base_main_sha"] == recorded_sha
    assert payload["base_main_sha_source"] == "recorded_override"

    raw_phase18c = json.loads(
        (out_dir / "phase18c" / "mined_solver_runtime_dispatch_proof.json")
        .read_text(encoding="utf-8")
    )
    assert raw_phase18c["base_main_sha"] == recorded_sha
    assert raw_phase18c["base_main_sha_source"] == "recorded_override"


def test_cli_rejects_invalid_recorded_base_main_sha() -> None:
    result = _run("--json", "--recorded-base-main-sha", "not-a-sha")

    assert result.returncode == 1
    assert (
        "--recorded-base-main-sha must be a 40-character hexadecimal git SHA"
        in result.stderr
    )


def test_cli_rejects_existing_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "a4-proof"
    out_dir.mkdir()

    result = _run("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def _fixed_now() -> datetime:
    return datetime(2026, 5, 20, 18, 50, tzinfo=timezone.utc)

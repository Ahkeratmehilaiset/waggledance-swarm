# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_hex_subdivision_runtime_rehearsal_proof import (
    REPORT_VERSION,
    build_hex_subdivision_runtime_rehearsal_proof,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_hex_subdivision_runtime_rehearsal_proof.py"
FIXED_NOW = datetime(2026, 6, 27, 0, 0, tzinfo=timezone.utc)


def test_proof_builds_runtime_rehearsal_without_runtime_commit(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "hex-subdivision-runtime-rehearsal"

    report = build_hex_subdivision_runtime_rehearsal_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-06-27T00:00:00Z"
    assert report["proof_checks"] == {
        "authority_tamper_blocks_rehearsal": True,
        "missing_parent_blocks_rehearsal": True,
        "ready_rehearsal_does_not_commit_runtime": True,
        "ready_rehearsal_matches_shadow_topology": True,
    }

    rehearsal = report["subdivision_runtime_rehearsal"]
    assert rehearsal["ok"] is True
    assert rehearsal["ready_for_operator_commit_gate"] is True
    assert rehearsal["runtime_authority_granted"] is False
    assert rehearsal["runtime_topology_mutation_applied"] is False
    assert rehearsal["routing_influence_applied"] is False
    assert rehearsal["transport_performed"] is False
    assert rehearsal["claim_safe_upgrade"] is False
    assert rehearsal["runtime_commit_performed"] is False
    assert "candidate_topology_buildable" in report[
        "missing_parent_blockers"
    ]
    assert "preflight_runtime_authority_false" in report[
        "authority_tamper_blockers"
    ]

    proof_path = Path(report["proof_path"])
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved["ok"] is True


def test_cli_json_reports_runtime_rehearsal(tmp_path: Path) -> None:
    out_dir = tmp_path / "hex-subdivision-runtime-rehearsal"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--now",
            "2026-06-27T00:00:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    rehearsal = payload["subdivision_runtime_rehearsal"]
    assert rehearsal["required_next_gate"] == (
        "operator_signed_runtime_subdivision_commit"
    )
    assert rehearsal["runtime_commit_performed"] is False


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "hex-subdivision-runtime-rehearsal"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(tmp_path / "hex-subdivision-runtime-rehearsal"),
            "--now",
            "2026-06-27T03:00:00+03:00",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr

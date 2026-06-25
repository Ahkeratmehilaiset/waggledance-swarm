from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_hex_subdivision_activation_preflight_proof import (
    REPORT_VERSION,
    build_hex_subdivision_activation_preflight_proof,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_hex_subdivision_activation_preflight_proof.py"
FIXED_NOW = datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc)


def test_proof_builds_shadow_subdivision_preflight(tmp_path: Path) -> None:
    out_dir = tmp_path / "hex-subdivision-preflight"

    report = build_hex_subdivision_activation_preflight_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-06-26T00:00:00Z"

    preflight = report["subdivision_activation_preflight"]
    assert preflight["ok"] is True
    assert preflight["runtime_authority_granted"] is False
    assert preflight["runtime_topology_mutation_applied"] is False
    assert preflight["routing_influence_applied"] is False
    assert preflight["transport_performed"] is False
    assert preflight["claim_safe_upgrade"] is False
    assert preflight["canary_sample_count"] == 2
    assert preflight["shadow_activation_packet"]["ok"] is True
    assert preflight["shadow_activation_packet"]["delivery_summary"][
        "blocked_count"
    ] == 0

    proof_path = Path(report["proof_path"])
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved["ok"] is True


def test_cli_json_reports_preflight(tmp_path: Path) -> None:
    out_dir = tmp_path / "hex-subdivision-preflight"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--now",
            "2026-06-26T00:00:00Z",
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
    preflight = payload["subdivision_activation_preflight"]
    assert preflight["runtime_authority_granted"] is False
    assert preflight["required_next_gate"] == (
        "operator_signed_runtime_subdivision_commit"
    )


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "hex-subdivision-preflight"
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
            str(tmp_path / "hex-subdivision-preflight"),
            "--now",
            "2026-06-26T03:00:00+03:00",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_hex_subdivision_runtime_commit_envelope_proof import (
    REPORT_VERSION,
    build_hex_subdivision_runtime_commit_envelope_proof,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "tools" / "run_hex_subdivision_runtime_commit_envelope_proof.py"
)
FIXED_NOW = datetime(2026, 6, 27, 0, 0, tzinfo=timezone.utc)


def test_proof_builds_runtime_commit_envelope_without_runtime_commit(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "hex-subdivision-commit-envelope"

    report = build_hex_subdivision_runtime_commit_envelope_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-06-27T00:00:00Z"
    assert report["proof_checks"] == {
        "forged_signature_blocks": True,
        "signed_fixture_does_not_commit_runtime": True,
        "signed_fixture_envelope_ready": True,
        "unsigned_envelope_blocks": True,
    }
    assert report["operator_signature_fixture"] == (
        "synthetic_test_only_not_real_operator_approval"
    )

    envelope = report["subdivision_runtime_commit_envelope"]
    assert envelope["ok"] is True
    assert envelope["ready_for_runtime_commit_executor"] is True
    assert envelope["runtime_authority_granted"] is False
    assert envelope["runtime_topology_mutation_applied"] is False
    assert envelope["routing_influence_applied"] is False
    assert envelope["transport_performed"] is False
    assert envelope["claim_safe_upgrade"] is False
    assert envelope["runtime_commit_performed"] is False
    assert envelope["operator_signature"]["signed_by"] == (
        "synthetic-operator-fixture"
    )
    assert "fixture_only" not in envelope["operator_signature"]
    assert "operator_signature_present" in report[
        "unsigned_envelope_blockers"
    ]
    assert "operator_signature_preflight_digest_matches" in report[
        "forged_signature_blockers"
    ]

    proof_path = Path(report["proof_path"])
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved["ok"] is True


def test_cli_json_reports_runtime_commit_envelope(tmp_path: Path) -> None:
    out_dir = tmp_path / "hex-subdivision-commit-envelope"

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
    envelope = payload["subdivision_runtime_commit_envelope"]
    assert envelope["required_operator_action"] == (
        "operator_signed_runtime_subdivision_commit"
    )
    assert envelope["runtime_commit_performed"] is False


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "hex-subdivision-commit-envelope"
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
            str(tmp_path / "hex-subdivision-commit-envelope"),
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

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_subdivision_runtime_commit_applicator_proof import (
    build_hex_subdivision_runtime_commit_application_proof,
)


def test_runtime_commit_application_proof_writes_fail_closed_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_subdivision_runtime_commit_application_proof(
        out_dir=out_dir,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "application_prepares_candidate": True,
        "application_matches_rehearsal_candidate": True,
        "application_does_not_commit_live_runtime": True,
        "unsigned_envelope_blocks_application": True,
        "source_drift_blocks_application": True,
        "rehearsal_digest_drift_blocks_application": True,
    }
    application = report["subdivision_runtime_commit_application"]
    assert application["ok"] is True
    assert application["runtime_commit_performed"] is False
    assert application["runtime_topology_mutation_applied"] is False
    assert application["transport_performed"] is False
    assert application["live_runtime_commit_authorized"] is False
    assert "commit_envelope_ok" in report["unsigned_application_blockers"]
    assert (
        "source_topology_matches_rehearsal_input"
        in report["source_drift_blockers"]
    )
    assert (
        "runtime_rehearsal_digest_rederives"
        in report["tampered_rehearsal_blockers"]
    )

    proof_path = out_dir / (
        "hex_subdivision_runtime_commit_application_proof.json"
    )
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_runtime_commit_application_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_commit_applicator_proof.py",
        "--out-dir",
        str(out_dir),
        "--json",
    ]

    proc = subprocess.run(
        cmd,
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "out_dir must not exist" in proc.stderr


def test_runtime_commit_application_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_commit_applicator_proof.py",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-27T00:00:00Z",
        "--json",
    ]

    proc = subprocess.run(
        cmd,
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["generated_at_utc"] == "2026-06-27T00:00:00Z"
    assert report["operator_signature_fixture"] == (
        "synthetic_test_only_not_real_operator_approval"
    )

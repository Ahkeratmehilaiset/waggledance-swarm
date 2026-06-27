# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_subdivision_runtime_executor_admission_proof import (
    build_hex_subdivision_runtime_executor_admission_proof,
)


def test_runtime_executor_admission_proof_writes_fail_closed_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_subdivision_runtime_executor_admission_proof(
        out_dir=out_dir,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "executor_admission_proof_valid": True,
        "executor_admission_remains_blocked": True,
        "executor_admission_does_not_run_live_runtime": True,
        "request_digest_drift_blocks_admission": True,
        "runtime_claim_blocks_admission": True,
        "cutover_authorization_is_not_accepted_by_dry_run": True,
    }
    admission = report["subdivision_runtime_executor_admission"]
    assert admission["ok"] is True
    assert admission["ready_for_runtime_executor_admission"] is False
    assert admission["runtime_commit_performed"] is False
    assert admission["runtime_topology_mutation_applied"] is False
    assert admission["transport_performed"] is False
    assert admission["live_runtime_execution_authorized"] is False
    assert admission["runtime_executor_invoked"] is False
    assert (
        "execution_request_digest_rederives"
        in report["drifted_request_blockers"]
    )
    assert (
        "execution_request_runtime_flags_false"
        in report["runtime_claim_request_blockers"]
    )
    assert (
        "cutover_authorization_absent"
        in report["cutover_supplied_blockers"]
    )

    proof_path = out_dir / (
        "hex_subdivision_runtime_executor_admission_proof.json"
    )
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_runtime_executor_admission_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_executor_admission_proof.py",
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


def test_runtime_executor_admission_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_executor_admission_proof.py",
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
    assert report["cutover_authorization_fixture"] == (
        "synthetic_cutover_input_rejected_by_dry_run"
    )

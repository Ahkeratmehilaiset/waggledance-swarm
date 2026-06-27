# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_subdivision_runtime_execution_request_proof import (
    build_hex_subdivision_runtime_execution_request_proof,
)


def test_runtime_execution_request_proof_writes_fail_closed_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_subdivision_runtime_execution_request_proof(
        out_dir=out_dir,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "execution_request_ready": True,
        "execution_request_does_not_authorize_live_runtime": True,
        "execution_request_preserves_application_digest": True,
        "tampered_application_blocks_request": True,
        "mismatched_metadata_blocks_request": True,
        "runtime_claim_metadata_blocks_request": True,
    }
    request = report["subdivision_runtime_execution_request"]
    assert request["ok"] is True
    assert request["runtime_commit_performed"] is False
    assert request["runtime_topology_mutation_applied"] is False
    assert request["transport_performed"] is False
    assert request["live_runtime_execution_authorized"] is False
    assert request["runtime_executor_invoked"] is False
    assert (
        "runtime_application_digest_rederives"
        in report["tampered_application_blockers"]
    )
    assert (
        "request_application_digest_matches"
        in report["mismatched_metadata_blockers"]
    )
    assert (
        "request_contains_no_runtime_claim"
        in report["runtime_claim_metadata_blockers"]
    )

    proof_path = out_dir / (
        "hex_subdivision_runtime_execution_request_proof.json"
    )
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_runtime_execution_request_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_execution_request_proof.py",
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


def test_runtime_execution_request_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_execution_request_proof.py",
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
    assert report["request_metadata_fixture"] == (
        "synthetic_request_not_operator_approval"
    )

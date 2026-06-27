# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

from tools.run_hex_subdivision_runtime_pipeline_e2e_proof import (
    build_hex_subdivision_runtime_pipeline_e2e_proof,
)


def test_pipeline_e2e_proof_writes_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = build_hex_subdivision_runtime_pipeline_e2e_proof(out_dir=out_dir)

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["proof_checks"] == {
        "pipeline_chain_composes": True,
        "preflight_digest_flows_end_to_end": True,
        "envelope_digest_flows_end_to_end": True,
        "rehearsal_digest_flows_end_to_end": True,
        "application_digest_flows_to_request": True,
        "plan_identity_consistent_end_to_end": True,
        "no_stage_authorizes_live_runtime": True,
        "broken_handoff_fails_closed": True,
    }
    assert report["pipeline_stage_order"] == [
        "preflight",
        "commit_envelope",
        "rehearsal",
        "commit_application",
        "execution_request",
    ]
    # every hand-off digest is a non-empty string that actually flowed
    digests = report["handoff_digests"]
    assert set(digests) == {
        "preflight_digest",
        "envelope_digest",
        "rehearsal_digest",
        "application_digest",
        "execution_request_digest",
    }
    assert all(isinstance(v, str) and v for v in digests.values())

    # the final request composed clean and authorizes nothing live
    assert report["execution_request_ok"] is True
    assert report["execution_request_live_runtime_authorized"] is False

    # the corrupted hand-off was caught fail-closed
    assert (
        "application_envelope_digest_matches_embedded"
        in report["broken_handoff_blockers"]
    )

    proof_path = out_dir / "hex_subdivision_runtime_pipeline_e2e_proof.json"
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved == report


def test_pipeline_e2e_proof_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_pipeline_e2e_proof.py",
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


def test_pipeline_e2e_proof_cli_json(tmp_path):
    out_dir = tmp_path / "proof"
    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_pipeline_e2e_proof.py",
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
    assert report["report_version"] == (
        "wd.hex_subdivision_runtime_pipeline_e2e_proof.v0"
    )

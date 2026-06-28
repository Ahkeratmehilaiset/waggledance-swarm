# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys

import tools.run_hex_subdivision_runtime_readiness_dry_run as dry_run
from tools.run_hex_subdivision_runtime_readiness_dry_run import (
    AUTHORITY_BOUNDARY,
    READINESS_STATUS,
    REPORT_VERSION,
    build_hex_subdivision_runtime_readiness_dry_run,
)
from waggledance.core.hex_topology.subdivision_runtime_executor_admission import (
    SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER,
)


def test_runtime_readiness_dry_run_writes_blocked_report(tmp_path):
    out_dir = tmp_path / "readiness"

    report = build_hex_subdivision_runtime_readiness_dry_run(out_dir=out_dir)

    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["readiness_status"] == READINESS_STATUS
    assert report["runtime_ready_evidence_available"] is True
    assert report["production_activation_ready"] is False
    assert report["runtime_mutation_authority"] is False
    assert report["authority_boundary"] == AUTHORITY_BOUNDARY
    assert set(report["authority_boundary"].values()) == {False}
    assert report["activation_blockers"] == [
        SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER
    ]
    assert report["forbidden_true_flag_paths"] == []
    assert report["proof_checks"] == {
        "pipeline_e2e_proof_ok": True,
        "pipeline_e2e_checks_all_true": True,
        "executor_admission_proof_ok": True,
        "executor_admission_checks_all_true": True,
        "executor_admission_remains_blocked": True,
        "dry_run_rejects_cutover_authorization": True,
        "runtime_authority_false_everywhere": True,
        "production_activation_not_ready": True,
        "operator_cutover_gate_required": True,
    }

    pipeline = report["source_reports"]["pipeline_e2e"]
    assert pipeline["ok"] is True
    assert pipeline["execution_request_live_runtime_authorized"] is False
    admission = report["source_reports"]["executor_admission"]
    assert admission["ok"] is True
    assert admission["ready_for_runtime_executor_admission"] is False
    assert admission["runtime_executor_invoked"] is False
    assert admission["runtime_commit_performed"] is False
    assert admission["runtime_topology_mutation_applied"] is False
    assert admission["transport_performed"] is False

    proof_paths = [
        out_dir / "pipeline_e2e" / "hex_subdivision_runtime_pipeline_e2e_proof.json",
        out_dir
        / "executor_admission"
        / "hex_subdivision_runtime_executor_admission_proof.json",
        out_dir / "hex_subdivision_runtime_readiness_dry_run.json",
    ]
    assert all(path.exists() for path in proof_paths)
    saved = json.loads(proof_paths[-1].read_text(encoding="utf-8"))
    assert saved == report


def test_runtime_readiness_dry_run_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "readiness"
    out_dir.mkdir()

    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_readiness_dry_run.py",
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


def test_runtime_readiness_dry_run_fails_closed_when_top_level_dormancy_flips(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        dry_run,
        "TOP_LEVEL_DORMANCY_FLAGS",
        {
            "production_activation_ready": True,
            "runtime_mutation_authority": True,
        },
    )

    report = build_hex_subdivision_runtime_readiness_dry_run(
        out_dir=tmp_path / "readiness"
    )

    assert report["ok"] is False
    assert report["runtime_ready_evidence_available"] is False
    assert report["production_activation_ready"] is True
    assert report["runtime_mutation_authority"] is True
    assert "production_activation_not_ready" in report["blockers"]
    assert "runtime_authority_false_everywhere" in report["blockers"]
    assert set(report["forbidden_true_flag_paths"]) == {
        "top_level_dormancy.production_activation_ready",
        "top_level_dormancy.runtime_mutation_authority",
    }


def test_runtime_readiness_dry_run_fails_closed_when_authority_boundary_flips(
    tmp_path, monkeypatch
):
    authority_boundary = {
        **AUTHORITY_BOUNDARY,
        "runtime_executor_invocation": True,
        "runtime_topology_mutation": True,
        "routing_influence": True,
        "transport": True,
    }
    monkeypatch.setattr(dry_run, "AUTHORITY_BOUNDARY", authority_boundary)

    report = build_hex_subdivision_runtime_readiness_dry_run(
        out_dir=tmp_path / "readiness"
    )

    assert report["ok"] is False
    assert report["runtime_ready_evidence_available"] is False
    assert "runtime_authority_false_everywhere" in report["blockers"]
    assert set(report["forbidden_true_flag_paths"]) == {
        "authority_boundary.runtime_executor_invocation",
        "authority_boundary.runtime_topology_mutation",
        "authority_boundary.routing_influence",
        "authority_boundary.transport",
    }


def test_runtime_readiness_dry_run_cli_json(tmp_path):
    out_dir = tmp_path / "readiness"
    cmd = [
        sys.executable,
        "tools/run_hex_subdivision_runtime_readiness_dry_run.py",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-28T00:00:00Z",
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
    assert report["generated_at_utc"] == "2026-06-28T00:00:00Z"
    assert report["report_version"] == REPORT_VERSION
    assert report["production_activation_ready"] is False
    assert report["runtime_mutation_authority"] is False

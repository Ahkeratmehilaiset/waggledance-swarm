# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.run_hex_runtime_readiness_observability_rollup import (
    REPORT_VERSION,
    build_hex_runtime_readiness_observability_rollup,
)
from tools.run_hex_subdivision_runtime_readiness_dry_run import (
    READINESS_STATUS,
    REPORT_VERSION as DRY_RUN_REPORT_VERSION,
)
from waggledance.core.hex_topology.subdivision_runtime_executor_admission import (
    SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER,
)


SCRIPT = Path("tools/run_hex_runtime_readiness_observability_rollup.py")


def _valid_readiness_report() -> dict:
    digest = "sha256:" + ("1" * 64)
    return {
        "report_version": DRY_RUN_REPORT_VERSION,
        "generated_at_utc": "2026-06-29T00:00:00Z",
        "ok": True,
        "blockers": [],
        "readiness_status": READINESS_STATUS,
        "runtime_ready_evidence_available": True,
        "production_activation_ready": False,
        "runtime_mutation_authority": False,
        "readiness_digest": "sha256:" + ("2" * 64),
        "authority_boundary": {
            "runtime_mutation_authority": False,
            "runtime_authority_granted": False,
            "production_activation": False,
            "operator_cutover": False,
            "runtime_executor_invocation": False,
            "runtime_topology_mutation": False,
            "routing_influence": False,
            "transport": False,
            "claim_safe_upgrade": False,
            "bridge_append_allowed": False,
            "scheduler_enqueue_allowed": False,
            "merge_allowed": False,
        },
        "activation_blockers": [
            SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER
        ],
        "required_next_gate": (
            "operator_verified_runtime_subdivision_executor_cutover"
        ),
        "proof_checks": {
            "runtime_authority_false_everywhere": True,
        },
        "source_reports": {
            "pipeline_e2e": {
                "ok": True,
                "execution_request_digest": digest,
                "execution_request_live_runtime_authorized": False,
            },
            "executor_admission": {
                "ok": True,
                "runtime_execution_request_digest": digest,
                "ready_for_runtime_executor_admission": False,
                "runtime_executor_invoked": False,
                "runtime_commit_performed": False,
                "runtime_topology_mutation_applied": False,
                "transport_performed": False,
            },
        },
    }


def _write_report(path: Path, report: dict) -> None:
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_rollup_surfaces_runtime_ready_evidence_without_authority(
    tmp_path: Path,
) -> None:
    readiness_path = tmp_path / "readiness.json"
    _write_report(readiness_path, _valid_readiness_report())

    report = build_hex_runtime_readiness_observability_rollup(
        readiness_report_path=readiness_path,
        out_dir=tmp_path / "rollup",
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["runtime_ready_evidence_available"] is True
    assert report["production_activation_ready"] is False
    assert report["runtime_mutation_authority"] is False
    assert report["gate_bypass_allowed"] is False
    assert report["source"]["readiness_report_version"] == DRY_RUN_REPORT_VERSION
    assert report["source"]["readiness_status"] == READINESS_STATUS
    assert report["source"]["path_free"] is True
    assert report["evidence"]["activation_blockers"] == [
        SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER
    ]
    assert report["evidence"]["pipeline_execution_request_digest"] == (
        report["evidence"]["executor_admission_execution_request_digest"]
    )
    assert set(report["authority_boundary"].values()) == {False, True}
    assert report["authority_boundary"]["read_only_report"] is True
    for key, value in report["authority_boundary"].items():
        if key != "read_only_report":
            assert value is False
    assert report["forbidden_true_flag_paths"] == []
    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\Python" not in encoded
    assert str(tmp_path) not in encoded
    assert json.loads(
        (tmp_path / "rollup" / "hex_runtime_readiness_observability_rollup.json")
        .read_text(encoding="utf-8")
    ) == report


def test_rollup_fails_closed_when_evidence_claims_activation(
    tmp_path: Path,
) -> None:
    readiness = _valid_readiness_report()
    readiness["production_activation_ready"] = True
    readiness["authority_boundary"]["scheduler_enqueue_allowed"] = True
    readiness["source_reports"]["executor_admission"][
        "runtime_executor_invoked"
    ] = True
    readiness_path = tmp_path / "readiness.json"
    _write_report(readiness_path, readiness)

    report = build_hex_runtime_readiness_observability_rollup(
        readiness_report_path=readiness_path,
    )

    assert report["ok"] is False
    assert report["runtime_ready_evidence_available"] is False
    assert report["production_activation_ready"] is False
    assert report["runtime_mutation_authority"] is False
    assert report["gate_bypass_allowed"] is False
    assert "production_activation_ready_not_false" in report["blockers"]
    assert (
        "authority_boundary.scheduler_enqueue_allowed_not_false"
        in report["blockers"]
    )
    assert (
        "executor_admission.runtime_executor_invoked_not_false"
        in report["blockers"]
    )
    assert set(report["forbidden_true_flag_paths"]) == {
        "production_activation_ready",
        "authority_boundary.scheduler_enqueue_allowed",
        "source_reports.executor_admission.runtime_executor_invoked",
    }


def test_rollup_fails_closed_when_digest_or_cutover_gate_is_unbound(
    tmp_path: Path,
) -> None:
    readiness = _valid_readiness_report()
    readiness["activation_blockers"] = []
    readiness["source_reports"]["executor_admission"][
        "runtime_execution_request_digest"
    ] = "sha256:" + ("0" * 64)
    readiness_path = tmp_path / "readiness.json"
    _write_report(readiness_path, readiness)

    report = build_hex_runtime_readiness_observability_rollup(
        readiness_report_path=readiness_path,
    )

    assert report["ok"] is False
    assert report["runtime_ready_evidence_available"] is False
    assert "operator_cutover_activation_blocker_missing" in report["blockers"]
    assert "pipeline_executor_digest_mismatch" in report["blockers"]


def test_rollup_refuses_existing_out_dir(tmp_path: Path) -> None:
    readiness_path = tmp_path / "readiness.json"
    _write_report(readiness_path, _valid_readiness_report())
    out_dir = tmp_path / "rollup"
    out_dir.mkdir()

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--readiness-report",
            str(readiness_path),
            "--out-dir",
            str(out_dir),
            "--json",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "out_dir must not exist" in proc.stderr


def test_rollup_cli_json_smoke(tmp_path: Path) -> None:
    readiness_path = tmp_path / "readiness.json"
    _write_report(readiness_path, _valid_readiness_report())
    out_dir = tmp_path / "rollup"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--readiness-report",
            str(readiness_path),
            "--out-dir",
            str(out_dir),
            "--now",
            "2026-06-29T00:00:00Z",
            "--json",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["generated_at_utc"] == "2026-06-29T00:00:00Z"
    assert report["runtime_ready_evidence_available"] is True
    assert report["authority_boundary"]["scheduler_enqueue_allowed"] is False
    assert report["authority_boundary"]["merge_allowed"] is False

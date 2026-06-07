# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_v12_per_query_receipt_coverage_proof import (
    CLAIM_LABEL,
    QueryCase,
    REPORT_VERSION,
    build_v12_per_query_receipt_coverage_proof,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_per_query_receipt_coverage_proof.py"
FIXED_NOW = datetime(2026, 6, 7, 17, 0, tzinfo=timezone.utc)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _all_json_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.json"))
    )


def test_build_reports_full_local_per_query_receipt_coverage(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "coverage-proof"

    report = build_v12_per_query_receipt_coverage_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-06-07T17:00:00Z"
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["claim_label"] == CLAIM_LABEL
    assert report["runtime_path"] == "AutonomyRuntime.handle_query"
    assert report["query_count"] == 3
    assert report["receipt_count_total"] == 3
    assert report["queries_with_verified_receipt"] == 3
    assert report["queries_with_solver_trace_receipt_bound"] == 3
    assert report["receipt_coverage_ratio"] == 1.0
    assert report["solver_trace_receipt_bound_ratio"] == 1.0
    assert report["all_queries_receipt_bound"] is True
    assert report["all_solver_traces_receipt_bound"] is True
    assert report["raw_payload_leak_check"] is True
    assert Path(report["report_path"]).exists()

    for query_report in report["query_reports"]:
        assert query_report["ok"] is True
        assert query_report["result_executed"] is True
        assert query_report["result_has_runtime_receipt"] is True
        assert query_report["verified_receipt"] is True
        assert query_report["receipt_count"] == 1
        assert query_report["verifier_ok"] is True
        assert query_report["actual_gate"] == "allow"
        assert query_report["verdict"] == "pass"
        assert query_report["evaluation_version"] == "magma.evaluation_result.v1"
        assert query_report["solver_call_trace_count"] == 1
        assert query_report["solver_call_trace_digest"].startswith("sha256:")
        assert query_report["solver_call_trace_digest_bound"] is True
        assert query_report["solver_call_trace_receipt_bound"] is True
        assert query_report["solver_selection"] == ["solve.v12_fixture"]
        assert query_report["raw_payload_leak_check"] is True
        assert Path(query_report["receipt_manifest"]).exists()

    authority = report["authority_boundary"]
    assert authority["local_artifacts_written"] is True
    assert authority["receipt_emission_mode"] == "opt_in_disk_bundle_sink"
    assert authority["default_sink_required"] is False
    assert authority["sink_none_preserved"] is True
    assert authority["default_runtime_receipt_emission_changed"] is False
    assert authority["runtime_authority_changed"] is False
    assert authority["external_effect_authority_change"] is False
    assert authority["operator_gate_required"] is False
    assert authority["external_writes_applied"] is False
    assert authority["bridge_append"] is False
    assert authority["solver_call_authority_granted"] is False
    assert authority["scheduler_enqueue"] is False
    assert authority["promotion"] is False
    assert authority["gate_skip"] is False
    assert authority["network"] is False
    assert authority["production_memory_migration"] is False

    no_sink = report["no_sink_control"]
    assert no_sink["result_executed"] is True
    assert no_sink["result_has_runtime_receipt"] is False
    assert no_sink["sink_none_preserved"] is True
    assert "runtime_receipt" not in no_sink["result_keys"]


def test_build_output_does_not_leak_raw_query_or_context_markers(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "coverage-proof"

    build_v12_per_query_receipt_coverage_proof(out_dir=out_dir, now_utc=FIXED_NOW)
    emitted_text = _all_json_text(out_dir)

    assert "DO_NOT_LEAK" not in emitted_text
    assert "private oncology query" not in emitted_text
    assert "private thermodynamics query" not in emitted_text
    assert "private control query" not in emitted_text
    assert "context secret" not in emitted_text


def test_build_can_emit_v0_evaluation_results(tmp_path: Path) -> None:
    report = build_v12_per_query_receipt_coverage_proof(
        out_dir=tmp_path / "v0-proof",
        now_utc=FIXED_NOW,
        evaluation_version="magma.evaluation_result.v0",
    )

    assert report["ok"] is True
    assert report["evaluation_version"] == "magma.evaluation_result.v0"
    assert {
        query_report["evaluation_version"] for query_report in report["query_reports"]
    } == {"magma.evaluation_result.v0"}


def test_build_refuses_existing_output_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "existing"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="out_dir must not exist"):
        build_v12_per_query_receipt_coverage_proof(
            out_dir=out_dir,
            now_utc=FIXED_NOW,
        )


def test_build_rejects_duplicate_query_ids_before_writing(tmp_path: Path) -> None:
    out_dir = tmp_path / "duplicate-proof"
    duplicate_cases = (
        QueryCase(
            query_id="query.local.duplicate.fixture",
            query="private duplicate query DO_NOT_LEAK",
            context={"operator_note": "context secret"},
        ),
        QueryCase(
            query_id="query.local.duplicate.fixture",
            query="private duplicate query two DO_NOT_LEAK",
            context={"operator_note": "context secret"},
        ),
    )

    with pytest.raises(ValueError, match="unique query_id"):
        build_v12_per_query_receipt_coverage_proof(
            out_dir=out_dir,
            now_utc=FIXED_NOW,
            query_cases=duplicate_cases,
        )

    assert not out_dir.exists()


def test_cli_json_reports_coverage_without_raw_markers(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli-proof"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-07T17:00:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["query_count"] == 3
    assert payload["receipt_coverage_ratio"] == 1.0
    assert payload["all_queries_receipt_bound"] is True
    assert payload["authority_boundary"]["default_runtime_receipt_emission_changed"] is False
    assert payload["authority_boundary"]["runtime_authority_changed"] is False
    assert payload["authority_boundary"]["network"] is False
    assert "DO_NOT_LEAK" not in result.stdout
    assert "private oncology query" not in result.stdout
    assert "context secret" not in result.stdout


def test_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = _run(
        "--json",
        "--out-dir",
        str(tmp_path / "bad-now"),
        "--now",
        "2026-06-07T20:00:00+03:00",
    )

    assert result.returncode == 1
    assert "UTC timestamp ending in Z" in result.stderr

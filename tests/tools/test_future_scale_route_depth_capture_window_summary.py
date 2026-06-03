from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_future_scale_route_depth_benchmark as route_depth  # noqa: E402
import verify_future_scale_route_depth_capture_window_summary as summary  # noqa: E402


SCRIPT = ROOT / "tools" / "verify_future_scale_route_depth_capture_window_summary.py"
FIXED_NOW = datetime(2026, 6, 2, 20, 55, tzinfo=timezone.utc)


def _capture_buckets(depths: list[int]) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for label in route_depth.ROUTE_DEPTH_BUCKET_LABELS:
        if label == "+Inf":
            buckets[label] = len(depths)
        else:
            buckets[label] = sum(1 for depth in depths if depth <= int(label))
    return buckets


def _valid_capture_window_payload() -> dict:
    profile_depths = {
        "prod_route_alpha": [5, 6],
        "prod_route_beta": [7],
    }
    profiles = [
        {
            "route_profile": route_profile,
            "final_stage": (
                "deterministic_solver"
                if route_profile == "prod_route_alpha"
                else "hex_neighbor_assist_7_cell"
            ),
            "sample_count": len(depths),
            "route_depth_sum": sum(depths),
            "cumulative_buckets": _capture_buckets(depths),
        }
        for route_profile, depths in profile_depths.items()
    ]
    all_depths = [
        depth
        for depths in profile_depths.values()
        for depth in depths
    ]
    return {
        "schema_version": route_depth.PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION,
        "capture_window_id": "prod_window_20260603_1800",
        "source_kind": "operator_owned_metrics_export",
        "operator_owned_export": True,
        "window_start_utc": "2026-06-03T18:00:00Z",
        "window_end_utc": "2026-06-03T18:15:00Z",
        "metric_names": list(route_depth.ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES),
        "label_names": list(route_depth.ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES),
        "bucket_labels": list(route_depth.ROUTE_DEPTH_BUCKET_LABELS),
        "sample_count": len(all_depths),
        "route_depth_sum": sum(all_depths),
        "aggregate_cumulative_buckets": _capture_buckets(all_depths),
        "route_profile_count": len(profiles),
        "route_profiles": profiles,
        "raw_payload_included": False,
        "query_text_included": False,
        "local_paths_recorded": False,
        "network_access": "not_used",
        "cloud_api_calls": 0,
    }


def test_capture_window_summary_verifies_attached_export_without_claim_upgrade() -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )
    attachment = report["production_route_depth_capture_window_attachment"]

    result = summary.build_capture_window_verification_summary(
        benchmark_report=report,
        capture_attachment=attachment,
    )

    assert result["ok"] is True
    assert result["summary_schema_version"] == summary.SUMMARY_SCHEMA_VERSION
    assert result["status"] == summary.SUMMARY_STATUS_READY
    assert result["capture_window_count"] == 1
    assert result["capture_window_ids"] == ["prod_window_20260603_1800"]
    assert result["production_runtime_data_attached"] is True
    assert result["operator_owned_capture_window_contract_verified"] is True
    assert result["operator_owned_input_attested_by_payload"] is True
    assert result["source_kinds"] == ["operator_owned_metrics_export"]
    assert result["claim_gate_satisfied"] is False
    assert result["claim_safe"] is False
    assert result["literal_future_claim_safe"] is False
    assert result["required_runtime_evidence_present"] is False
    assert result["runtime_authority_changed"] is False
    assert result["runtime_authority_granted"] is False
    assert result["controls_present"] is False
    assert result["operator_gate_required"] is False
    assert result["external_writes_applied"] is False
    assert result["artifact_payloads_included"] is False
    assert result["local_paths_recorded"] is False
    assert result["raw_payload_included"] is False
    assert result["query_text_included"] is False
    assert result["external_fetch_performed"] is False
    assert result["bridge_write_performed"] is False
    assert result["live_production_export_claimed_by_tool"] is False
    assert result["network_access"] == "not_used"
    assert result["cloud_api_calls"] == 0
    assert len(result["benchmark_report_digest_sha256"]) == 64
    assert len(result["capture_attachment_file_digest_sha256"]) == 64
    assert len(result["capture_attachment_digest_sha256"]) == 64
    assert len(result["capture_window_digest_sha256s"][0]) == 64
    assert "no input paths" in result["safe_conclusion"]
    assert "does not upgrade future-scale claim gates" in result["safe_conclusion"]


def test_capture_window_summary_fails_closed_without_attached_window() -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)
    attachment = report["production_route_depth_capture_window_attachment"]

    result = summary.build_capture_window_verification_summary(
        benchmark_report=report,
        capture_attachment=attachment,
    )

    assert result["ok"] is False
    assert result["status"] == summary.SUMMARY_STATUS_BLOCKED
    assert "capture_attachment_not_operator_attached" in result["blockers"]
    assert "production_runtime_data_not_attached" in result["blockers"]
    assert "capture_window_count_insufficient" in result["blockers"]
    assert result["claim_gate_satisfied"] is False
    assert result["required_runtime_evidence_present"] is False
    assert result["operator_owned_capture_window_contract_verified"] is False


def test_capture_window_summary_fails_closed_on_attachment_mismatch() -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )
    attachment = deepcopy(report["production_route_depth_capture_window_attachment"])
    attachment["capture_window_count"] = 2

    result = summary.build_capture_window_verification_summary(
        benchmark_report=report,
        capture_attachment=attachment,
    )

    assert result["ok"] is False
    assert "capture_attachment_mismatch" in result["blockers"]
    assert "capture_window_count_mismatch" in result["blockers"]
    assert result["operator_owned_capture_window_contract_verified"] is False
    assert result["claim_gate_satisfied"] is False


def test_capture_window_summary_fails_closed_on_invalid_benchmark_report() -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )
    report["claim_gate_satisfied"] = True
    attachment = report["production_route_depth_capture_window_attachment"]

    result = summary.build_capture_window_verification_summary(
        benchmark_report=report,
        capture_attachment=attachment,
    )

    assert result["ok"] is False
    assert "benchmark_report_contract_invalid" in result["blockers"]
    assert result["benchmark_validation_error_count"] > 0
    assert result["claim_gate_satisfied"] is False


def test_capture_window_summary_rejects_non_finite_without_echoing() -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )
    attachment = deepcopy(report["production_route_depth_capture_window_attachment"])
    attachment["capture_window_count"] = math.nan
    report["production_route_depth_capture_window_attachment"] = attachment

    result = summary.build_capture_window_verification_summary(
        benchmark_report=report,
        capture_attachment=attachment,
    )

    assert result["ok"] is False
    assert "capture_window_count_not_int" in result["blockers"]
    assert "capture_window_count_unsafe" in result["blockers"]
    assert result["capture_window_count"] is None
    json.dumps(result, allow_nan=False)


def test_capture_window_summary_redacts_unsafe_capture_window_id() -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )
    attachment = deepcopy(report["production_route_depth_capture_window_attachment"])
    attachment["capture_windows"][0]["capture_window_id"] = (
        "C:\\Users\\janik\\private\\capture.json"
    )
    report["production_route_depth_capture_window_attachment"] = attachment

    result = summary.build_capture_window_verification_summary(
        benchmark_report=report,
        capture_attachment=attachment,
    )

    assert result["ok"] is False
    assert "capture_window_id_unsafe" in result["blockers"]
    assert result["capture_window_ids"] == []
    rendered = json.dumps(result, allow_nan=False)
    assert "C:\\Users" not in rendered
    assert "capture.json" not in rendered


def test_capture_window_summary_cli_rejects_non_finite_without_traceback(
    tmp_path: Path,
) -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )
    attachment = deepcopy(report["production_route_depth_capture_window_attachment"])
    attachment["capture_window_count"] = math.nan
    report["production_route_depth_capture_window_attachment"] = attachment
    benchmark_path = tmp_path / "benchmark.json"
    attachment_path = tmp_path / "attachment.json"
    benchmark_path.write_text(json.dumps(report), encoding="utf-8")
    attachment_path.write_text(json.dumps(attachment), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--benchmark-json",
            str(benchmark_path),
            "--capture-attachment-json",
            str(attachment_path),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "capture_window_count_unsafe" in payload["blockers"]
    assert "NaN" not in proc.stdout
    assert "Traceback" not in proc.stderr


def test_capture_window_summary_cli_does_not_leak_input_paths(tmp_path: Path) -> None:
    report = route_depth.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )
    input_dir = tmp_path / "operator" / "private"
    input_dir.mkdir(parents=True)
    benchmark_path = input_dir / "future_scale_route_depth_benchmark.json"
    attachment_path = (
        input_dir
        / "future_scale_route_depth_production_capture_window_attachment.json"
    )
    benchmark_path.write_text(
        json.dumps(report, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    attachment_path.write_text(
        json.dumps(
            report["production_route_depth_capture_window_attachment"],
            sort_keys=True,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--benchmark-json",
            str(benchmark_path),
            "--capture-attachment-json",
            str(attachment_path),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    combined = proc.stdout + proc.stderr
    assert str(tmp_path) not in combined
    assert str(benchmark_path) not in combined
    assert str(attachment_path) not in combined
    assert "C:\\Users" not in combined
    assert "Bearer " not in combined
    assert '"raw_payload":' not in combined

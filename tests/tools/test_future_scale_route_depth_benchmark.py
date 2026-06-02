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

import run_future_scale_route_depth_benchmark as harness  # noqa: E402


SCRIPT = ROOT / "tools" / "run_future_scale_route_depth_benchmark.py"
FIXED_NOW = datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_route_depth_benchmark_reports_local_fixture_measurement() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    assert report["ok"] is True
    assert report["report_version"] == harness.REPORT_VERSION
    assert report["schema_version"] == harness.SCHEMA_VERSION
    assert report["generated_at_utc"] == "2026-06-03T00:00:00Z"
    assert report["axis_id"] == "route_depth"
    assert report["measurement_label"] == "MEASURED_LOCAL_ONLY"
    assert (
        report["benchmark_scope"]
        == "local_deterministic_sanitized_route_trace_fixture_only"
    )
    assert report["evidence_status"] == "measured_local"
    assert report["benchmark_artifact_present"] is True
    assert report["measured_value_present"] is True
    assert report["required_runtime_evidence_present"] is False
    assert report["claim_gate_satisfied"] is False
    assert report["claim_safe"] is False
    assert report["literal_future_claim_safe"] is False
    assert report["runtime_authority_changed"] is False
    assert report["runtime_authority_granted"] is False
    assert report["controls_present"] is False
    assert report["operator_gate_required"] is False
    assert report["external_writes_applied"] is False
    assert report["provider_jobs_delta"] == 0
    assert report["builder_jobs_delta"] == 0
    assert report["cloud_api_calls"] == 0
    assert report["no_cloud_api_calls"] is True
    assert report["no_model_pull_or_download"] is True
    assert report["contract_validation"] == {
        "ok": True,
        "error_count": 0,
        "errors": [],
    }

    result = report["benchmark_result"]
    assert result["trace_count"] == 4
    assert result["route_depth_values"] == [2, 5, 6, 7]
    assert result["route_depth_histogram"] == {"2": 1, "5": 1, "6": 1, "7": 1}
    assert result["min_route_depth"] == 2
    assert result["max_route_depth"] == 7
    assert result["mean_route_depth"] == 5.0
    assert result["p50_route_depth"] == 5
    assert result["p95_route_depth"] == 7
    assert result["p99_route_depth"] == 7
    assert result["route_depth_histogram_exported"] is True
    assert result["runtime_route_depth_histogram_exported"] is False
    assert result["result_is_production_baseline"] is False


def test_fixture_cases_use_sanitized_stage_sequences_only() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)
    by_id = {case["case_id"]: case for case in report["cases"]}

    assert by_id["cache_hit_short_route"]["sanitized_stage_sequence"] == [
        "language_detection",
        "hot_cache",
    ]
    assert by_id["deterministic_solver_route"]["sanitized_stage_sequence"] == [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "deterministic_solver",
    ]
    assert by_id["hybrid_retrieval_then_fallback_route"][
        "sanitized_stage_sequence"
    ] == [
        "language_detection",
        "hot_cache",
        "memory_context",
        "route_selection",
        "hybrid_retrieval_8_cell",
        "orchestrator_llm_fallback",
    ]
    assert by_id["hex_neighbor_assist_long_route"]["dropped_event_count"] == 1
    assert all(case["raw_private_markers_removed"] is True for case in by_id.values())

    serialized = json.dumps(report, sort_keys=True)
    assert "WD_ROUTE_DEPTH_PRIVATE" not in serialized
    assert "ignored_private_stage" not in serialized
    assert '"query"' not in serialized
    assert '"profile"' not in serialized


def test_validate_rejects_claim_gate_type_confusion() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["claim_gate_satisfied"] = "false"
    errors = harness.validate_benchmark_report(mutated)
    assert "claim_gate_satisfied must be exact false bool" in errors

    mutated = deepcopy(report)
    mutated["external_writes_applied"] = 0
    errors = harness.validate_benchmark_report(mutated)
    assert "external_writes_applied must be exact false bool" in errors

    mutated = deepcopy(report)
    mutated["no_cloud_api_calls"] = "true"
    errors = harness.validate_benchmark_report(mutated)
    assert "no_cloud_api_calls must be exact true bool" in errors


def test_validate_rejects_claim_upgrade_and_non_finite_depth_metric() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    upgraded = deepcopy(report)
    upgraded["measurement_label"] = "PROVEN"
    upgraded["claim_safe"] = True
    errors = harness.validate_benchmark_report(upgraded)
    assert "measurement_label must not upgrade to a proven claim" in errors
    assert "measurement_label must be MEASURED_LOCAL_ONLY" in errors
    assert "claim_safe must be exact false bool" in errors

    non_finite = deepcopy(report)
    non_finite["benchmark_result"]["mean_route_depth"] = math.inf
    errors = harness.validate_benchmark_report(non_finite)
    assert "benchmark_result.mean_route_depth does not match values" in errors
    assert any("non-finite number" in error for error in errors)


def test_validate_rejects_route_depth_count_mismatches() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["cases"][0]["route_depth"] = "2"
    errors = harness.validate_benchmark_report(mutated)
    assert "cases[0].route_depth must be a positive int" in errors

    mutated = deepcopy(report)
    mutated["benchmark_result"]["route_depth_histogram"] = {"2": 4}
    errors = harness.validate_benchmark_report(mutated)
    assert "benchmark_result.route_depth_histogram does not match values" in errors

    mutated = deepcopy(report)
    mutated["benchmark_result"]["p95_route_depth"] = 6
    errors = harness.validate_benchmark_report(mutated)
    assert "benchmark_result.p95_route_depth does not match values" in errors


def test_measurement_rejects_non_finite_or_leaking_sanitized_values() -> None:
    non_finite_fixture = {
        "case_id": "bad_nonfinite_memory_score",
        "expected_route_depth": 1,
        "raw_trace": [
            {"stage": "memory_context", "memory_score": math.inf},
        ],
    }
    try:
        harness._measure_route_depth_fixture(non_finite_fixture)
    except ValueError as exc:
        assert "non-finite number" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-finite sanitized field was accepted")

    leaking_fixture = {
        "case_id": "bad_provider_source",
        "expected_route_depth": 1,
        "raw_trace": [
            {"stage": "orchestrator_llm_fallback", "source": "gpt-4o"},
        ],
    }
    try:
        harness._measure_route_depth_fixture(leaking_fixture)
    except ValueError as exc:
        assert "forbidden string" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("provider-like sanitized field was accepted")


def test_cli_json_writes_artifacts_without_absolute_path_leak(tmp_path: Path) -> None:
    out_dir = tmp_path / "route-depth"

    result = _run_cli(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-03T00:00:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["generated_at_utc"] == "2026-06-03T00:00:00Z"
    assert payload["ok"] is True
    json_path = out_dir / harness.JSON_ARTIFACT_NAME
    md_path = out_dir / harness.MARKDOWN_ARTIFACT_NAME
    assert json_path.exists()
    assert md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["ok"] is True
    combined = result.stdout + result.stderr
    combined += json_path.read_text(encoding="utf-8")
    combined += md_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in combined
    assert "C:\\Users" not in combined
    assert "/tmp/" not in combined
    assert "Bearer " not in combined
    assert "bearer " not in combined
    assert "WD_ROUTE_DEPTH_PRIVATE" not in combined
    assert "gpt-4o" not in combined


def test_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = _run_cli(
        "--json",
        "--out-dir",
        str(tmp_path / "out"),
        "--now",
        "2026-06-03T03:00:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr


def test_markdown_preserves_no_overclaim_guardrails() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    markdown = harness.render_markdown(report)

    assert "Future-Scale Route Depth Benchmark" in markdown
    assert "claim_gate_satisfied: `false`" in markdown
    assert "claim_safe: `false`" in markdown
    assert "literal_future_claim_safe: `false`" in markdown
    assert "required_runtime_evidence_present: `false`" in markdown
    assert "external_writes_applied: `false`" in markdown
    assert "not a production baseline" in markdown
    assert "not a claim that route depth is optimized" in markdown
    assert "p95_route_depth: `7`" in markdown

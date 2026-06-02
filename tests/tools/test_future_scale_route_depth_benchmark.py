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
FIXED_NOW = datetime(2026, 6, 2, 20, 55, tzinfo=timezone.utc)


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
    assert report["generated_at_utc"] == "2026-06-02T20:55:00Z"
    assert report["axis_id"] == "route_depth"
    assert report["measurement_label"] == "MEASURED_LOCAL_ONLY"
    assert (
        report["benchmark_scope"]
        == "local_deterministic_sanitized_route_stage_trace_fixture"
    )
    assert report["evidence_status"] == "measured_local"
    assert report["trace_stage_policy"] == "allowlisted_stage_names_only_no_query_or_payload"
    assert report["claim_gate_satisfied"] is False
    assert report["claim_safe"] is False
    assert report["literal_future_claim_safe"] is False
    assert report["required_runtime_evidence_present"] is False
    assert report["runtime_authority_changed"] is False
    assert report["runtime_authority_granted"] is False
    assert report["controls_present"] is False
    assert report["operator_gate_required"] is False
    assert report["external_writes_applied"] is False
    assert report["provider_jobs_delta"] == 0
    assert report["builder_jobs_delta"] == 0
    assert report["cloud_api_calls"] == 0
    assert report["network_access"] == "not_used"
    assert report["contract_validation"] == {
        "ok": True,
        "error_count": 0,
        "errors": [],
    }

    result = report["benchmark_result"]
    assert result["sample_count"] == 5
    assert result["min_depth"] == 2
    assert result["max_depth"] == 8
    assert result["mean_depth"] == 5.6
    assert result["p50_depth"] == 6.0
    assert result["p95_depth"] == 7.8
    assert result["p99_depth"] == 7.96
    assert result["depth_histogram"]["2"] == 1
    assert result["depth_histogram"]["5"] == 1
    assert result["depth_histogram"]["6"] == 1
    assert result["depth_histogram"]["7"] == 1
    assert result["depth_histogram"]["8"] == 1
    assert result["is_production_baseline"] is False
    assert report["source"]["fixture_set_alias"] == "route_depth_static_trace_set_v1"
    assert len(report["source"]["fixture_set_sha256"]) == 64
    assert (
        report["source"]["sanitizer_api"]
        == "waggledance.adapters.http.routes.chat._sanitize_route_stage_trace"
    )
    json.dumps(report, allow_nan=False)


def test_fixture_cases_use_only_allowlisted_stage_names() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)
    by_id = {case["case_id"]: case for case in report["cases"]}

    assert by_id["hot_cache_hit"]["route_depth"] == 2
    assert by_id["hot_cache_hit"]["final_stage"] == "hot_cache"
    assert by_id["deterministic_solver_answer"]["route_depth"] == 5
    assert by_id["deterministic_solver_answer"]["final_stage"] == "deterministic_solver"
    assert by_id["authoritative_hybrid_answer"]["route_depth"] == 6
    assert by_id["authoritative_hybrid_answer"]["final_stage"] == "hybrid_retrieval_8_cell"
    assert by_id["hex_neighbor_answer"]["route_depth"] == 7
    assert by_id["hex_neighbor_answer"]["final_stage"] == "hex_neighbor_assist_7_cell"
    assert by_id["orchestrator_fallback_answer"]["route_depth"] == 8
    assert by_id["orchestrator_fallback_answer"]["final_stage"] == "orchestrator_llm_fallback"
    assert all(
        stage in report["allowed_route_stage_order"]
        for case in report["cases"]
        for stage in case["observed_stages"]
    )


def test_custom_trace_fixture_ignores_private_fields_and_unknown_stages() -> None:
    report = harness.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        trace_fixtures=[
            {
                "case_id": "custom_sanitized_trace",
                "trace": [
                    {
                        "stage": "language_detection",
                        "raw_query": r"C:\Users\janik\secret prompt",
                    },
                    {
                        "stage": "unknown_raw_stage",
                        "payload": "Bearer SECRET_TOKEN_1234567890",
                    },
                    {
                        "stage": "orchestrator_llm_fallback",
                        "raw_response": "sk-1234567890abcdef1234567890abcdef",
                    },
                ],
            }
        ],
    )

    assert report["ok"] is True
    assert report["cases"] == [
        {
            "case_id": "custom_sanitized_trace",
            "route_depth": 2,
            "final_stage": "orchestrator_llm_fallback",
            "observed_stages": [
                "language_detection",
                "orchestrator_llm_fallback",
            ],
            "ignored_event_count": 1,
        }
    ]
    payload = json.dumps(report, sort_keys=True)
    assert "SECRET_TOKEN" not in payload
    assert "sk-1234567890" not in payload
    assert "Users" not in payload
    assert "raw_query" not in payload
    assert "raw_response" not in payload


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
    mutated["required_runtime_evidence_present"] = True
    errors = harness.validate_benchmark_report(mutated)
    assert "required_runtime_evidence_present must be exact false bool" in errors


def test_validate_rejects_non_finite_and_case_type_confusion() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    non_finite = deepcopy(report)
    non_finite["benchmark_result"]["p95_depth"] = math.inf
    errors = harness.validate_benchmark_report(non_finite)
    assert "benchmark_result.p95_depth must be finite" in errors
    assert any("non-finite number" in error for error in errors)

    bad_case = deepcopy(report)
    bad_case["cases"][0]["route_depth"] = "2"
    errors = harness.validate_benchmark_report(bad_case)
    assert "cases[0].route_depth must be a non-negative int" in errors

    bad_histogram = deepcopy(report)
    bad_histogram["benchmark_result"]["depth_histogram"]["8"] = 0
    errors = harness.validate_benchmark_report(bad_histogram)
    assert "benchmark_result.depth_histogram does not sum to sample_count" in errors


def test_validate_rejects_path_and_secret_leaks() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    for value in [
        r"C:\tmp\route-depth.json",
        "C:tmp",
        "data/tmp/route-depth.json",
        "../route-depth.json",
        "/mnt/data/route-depth.json",
        "Bearer SECRET_TOKEN_1234567890",
        "sk-1234567890abcdef1234567890abcdef",
        "AKIA1234567890ABCDEFEXTRA",
        "gpt-4o",
        "openai",
        "claude-3-5-sonnet",
        "gemini-1.5-pro",
        "hf://org/model/org/model:latest",
        "org/model:latest",
    ]:
        mutated = deepcopy(report)
        mutated["non_claims"].append(value)
        errors = harness.validate_benchmark_report(mutated)
        assert any("forbidden secret/path-like string" in error for error in errors)


def test_cli_json_writes_artifacts_without_absolute_path_leak(tmp_path: Path) -> None:
    out_dir = tmp_path / "route-depth"

    result = _run_cli(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-02T20:55:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["generated_at_utc"] == "2026-06-02T20:55:00Z"
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
    assert "Bearer " not in combined


def test_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = _run_cli(
        "--json",
        "--out-dir",
        str(tmp_path / "out"),
        "--now",
        "2026-06-02T23:55:00+03:00",
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
    assert "not proof of superior intelligence" in markdown

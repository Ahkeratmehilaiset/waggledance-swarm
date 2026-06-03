from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_future_scale_route_depth_benchmark as harness  # noqa: E402


SCRIPT = ROOT / "tools" / "run_future_scale_route_depth_benchmark.py"
FIXED_NOW = datetime(2026, 6, 2, 20, 55, tzinfo=timezone.utc)


def _capture_buckets(depths: list[int]) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for label in harness.ROUTE_DEPTH_BUCKET_LABELS:
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
        "schema_version": harness.PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION,
        "capture_window_id": "prod_window_20260603_1800",
        "source_kind": "operator_owned_metrics_export",
        "operator_owned_export": True,
        "window_start_utc": "2026-06-03T18:00:00Z",
        "window_end_utc": "2026-06-03T18:15:00Z",
        "metric_names": list(harness.ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES),
        "label_names": list(harness.ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES),
        "bucket_labels": list(harness.ROUTE_DEPTH_BUCKET_LABELS),
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
    assert harness.PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_NAME in (
        report["artifact_write_scope"]
    )
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
    artifact = report["production_route_depth_histogram_artifact"]
    assert (
        artifact["schema_version"]
        == harness.PRODUCTION_HISTOGRAM_SCHEMA_VERSION
    )
    assert (
        artifact["artifact_status"]
        == "production_histogram_artifact_contract_available"
    )
    assert artifact["production_runtime_data_attached"] is False
    assert artifact["production_data_source"] == "not_attached"
    assert artifact["required_runtime_evidence_present"] is False
    assert artifact["claim_gate_satisfied"] is False
    assert artifact["claim_safe"] is False
    assert artifact["literal_future_claim_safe"] is False
    assert artifact["runtime_authority_changed"] is False
    assert artifact["runtime_authority_granted"] is False
    assert artifact["controls_present"] is False
    assert artifact["operator_gate_required"] is False
    assert artifact["external_writes_applied"] is False
    assert artifact["network_access"] == "not_used"
    assert artifact["cloud_api_calls"] == 0
    assert artifact["metric_names"] == list(
        harness.ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES
    )
    assert artifact["label_names"] == list(
        harness.ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES
    )
    assert artifact["bucket_labels"] == list(harness.ROUTE_DEPTH_BUCKET_LABELS)
    assert artifact["route_profile_count"] == 5
    assert artifact["sample_count"] == 5
    assert artifact["route_depth_sum"] == 28
    assert artifact["aggregate_cumulative_buckets"]["0"] == 0
    assert artifact["aggregate_cumulative_buckets"]["2"] == 1
    assert artifact["aggregate_cumulative_buckets"]["5"] == 2
    assert artifact["aggregate_cumulative_buckets"]["8"] == 5
    assert artifact["aggregate_cumulative_buckets"]["+Inf"] == 5
    assert len(artifact["artifact_digest_sha256"]) == 64
    assert "live production" in artifact["blockers_to_runtime_claim"][0]
    assert "does not attach live production runtime data" in artifact["safe_conclusion"]
    attachment = report["production_route_depth_capture_window_attachment"]
    assert (
        attachment["schema_version"]
        == harness.PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_SCHEMA_VERSION
    )
    assert (
        attachment["capture_window_schema_version"]
        == harness.PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION
    )
    assert (
        attachment["attachment_status"]
        == "capture_window_attachment_contract_available"
    )
    assert attachment["production_runtime_data_attached"] is False
    assert attachment["production_data_source"] == "not_attached"
    assert attachment["required_runtime_evidence_present"] is False
    assert attachment["claim_gate_satisfied"] is False
    assert attachment["claim_safe"] is False
    assert attachment["literal_future_claim_safe"] is False
    assert attachment["runtime_authority_changed"] is False
    assert attachment["runtime_authority_granted"] is False
    assert attachment["controls_present"] is False
    assert attachment["operator_gate_required"] is False
    assert attachment["external_writes_applied"] is False
    assert attachment["network_access"] == "not_used"
    assert attachment["cloud_api_calls"] == 0
    assert attachment["metric_names"] == list(
        harness.ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES
    )
    assert attachment["label_names"] == list(
        harness.ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES
    )
    assert attachment["bucket_labels"] == list(harness.ROUTE_DEPTH_BUCKET_LABELS)
    assert attachment["allowed_source_kinds"] == ["operator_owned_metrics_export"]
    assert attachment["capture_window_count"] == 0
    assert attachment["capture_windows"] == []
    assert len(attachment["attachment_digest_sha256"]) == 64
    assert (
        "does not by itself satisfy runtime evidence"
        in attachment["safe_conclusion"]
    )
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


def test_validate_rejects_malformed_production_histogram_artifact() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    truthy_gate = deepcopy(report)
    truthy_gate["production_route_depth_histogram_artifact"][
        "claim_gate_satisfied"
    ] = True
    errors = harness.validate_benchmark_report(truthy_gate)
    assert (
        "production histogram claim_gate_satisfied must be exact false bool"
        in errors
    )

    attached_runtime = deepcopy(report)
    attached_runtime["production_route_depth_histogram_artifact"][
        "production_runtime_data_attached"
    ] = "false"
    errors = harness.validate_benchmark_report(attached_runtime)
    assert (
        "production histogram production_runtime_data_attached must be false"
        in errors
    )

    bad_bucket_total = deepcopy(report)
    bad_bucket_total["production_route_depth_histogram_artifact"][
        "aggregate_cumulative_buckets"
    ]["+Inf"] = 4
    errors = harness.validate_benchmark_report(bad_bucket_total)
    assert (
        "production histogram aggregate_cumulative_buckets.+Inf must equal sample_count"
        in errors
    )

    bad_digest = deepcopy(report)
    bad_digest["production_route_depth_histogram_artifact"][
        "artifact_digest_sha256"
    ] = "0" * 64
    errors = harness.validate_benchmark_report(bad_digest)
    assert "production histogram artifact_digest_sha256 mismatch" in errors

    leaked_label = deepcopy(report)
    leaked_label["production_route_depth_histogram_artifact"]["route_profiles"][
        0
    ]["route_profile"] = "cohere_internal_model"
    errors = harness.validate_benchmark_report(leaked_label)
    assert any("forbidden secret/path-like string" in error for error in errors)


def test_operator_capture_window_attachment_validates_without_claim_upgrade() -> None:
    report = harness.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )

    assert report["ok"] is True
    assert report["required_runtime_evidence_present"] is False
    assert report["claim_gate_satisfied"] is False
    assert report["claim_safe"] is False
    attachment = report["production_route_depth_capture_window_attachment"]
    assert attachment["attachment_status"] == "operator_capture_window_attached"
    assert attachment["production_runtime_data_attached"] is True
    assert attachment["production_data_source"] == "operator_owned_metrics_export"
    assert attachment["required_runtime_evidence_present"] is False
    assert attachment["claim_gate_satisfied"] is False
    assert attachment["claim_safe"] is False
    assert attachment["literal_future_claim_safe"] is False
    assert attachment["runtime_authority_changed"] is False
    assert attachment["runtime_authority_granted"] is False
    assert attachment["operator_gate_required"] is False
    assert attachment["external_writes_applied"] is False
    assert attachment["capture_window_count"] == 1
    assert len(attachment["attachment_digest_sha256"]) == 64
    window = attachment["capture_windows"][0]
    assert window["capture_window_id"] == "prod_window_20260603_1800"
    assert window["sample_count"] == 3
    assert window["route_depth_sum"] == 18
    assert window["aggregate_cumulative_buckets"]["+Inf"] == 3
    assert window["route_profile_count"] == 2
    assert window["raw_payload_included"] is False
    assert window["query_text_included"] is False
    assert window["local_paths_recorded"] is False
    assert len(window["window_digest_sha256"]) == 64
    json.dumps(report, allow_nan=False)


def test_validate_rejects_malformed_capture_window_attachment() -> None:
    report = harness.build_future_scale_route_depth_benchmark(
        now_utc=FIXED_NOW,
        production_capture_window=_valid_capture_window_payload(),
    )

    truthy_gate = deepcopy(report)
    truthy_gate["production_route_depth_capture_window_attachment"][
        "claim_gate_satisfied"
    ] = True
    errors = harness.validate_benchmark_report(truthy_gate)
    assert (
        "production capture attachment claim_gate_satisfied must be exact false bool"
        in errors
    )

    raw_payload = deepcopy(report)
    raw_payload["production_route_depth_capture_window_attachment"][
        "capture_windows"
    ][0]["raw_payload_included"] = True
    errors = harness.validate_benchmark_report(raw_payload)
    assert (
        "production capture attachment capture_windows[0].raw_payload_included "
        "must be false"
        in errors
    )

    bad_bucket_total = deepcopy(report)
    bad_bucket_total["production_route_depth_capture_window_attachment"][
        "capture_windows"
    ][0]["aggregate_cumulative_buckets"]["+Inf"] = 2
    errors = harness.validate_benchmark_report(bad_bucket_total)
    assert (
        "production capture attachment capture_windows[0].aggregate_cumulative_buckets"
        ".+Inf must equal sample_count"
        in errors
    )

    bad_window_digest = deepcopy(report)
    bad_window_digest["production_route_depth_capture_window_attachment"][
        "capture_windows"
    ][0]["window_digest_sha256"] = "0" * 64
    errors = harness.validate_benchmark_report(bad_window_digest)
    assert (
        "production capture attachment capture_windows[0].window_digest_sha256 "
        "mismatch"
        in errors
    )

    bad_attachment_digest = deepcopy(report)
    bad_attachment_digest["production_route_depth_capture_window_attachment"][
        "attachment_digest_sha256"
    ] = "0" * 64
    errors = harness.validate_benchmark_report(bad_attachment_digest)
    assert (
        "production capture attachment attachment_digest_sha256 mismatch"
        in errors
    )


def test_capture_window_input_rejects_extra_fields_and_unsafe_values() -> None:
    extra = _valid_capture_window_payload()
    extra["raw_payload"] = "not recorded"
    with pytest.raises(ValueError, match="unsupported fields"):
        harness.build_future_scale_route_depth_benchmark(
            now_utc=FIXED_NOW,
            production_capture_window=extra,
        )

    unsafe = _valid_capture_window_payload()
    unsafe["local_paths_recorded"] = True
    with pytest.raises(ValueError, match="local_paths_recorded must be false"):
        harness.build_future_scale_route_depth_benchmark(
            now_utc=FIXED_NOW,
            production_capture_window=unsafe,
        )

    stale_time = _valid_capture_window_payload()
    stale_time["window_end_utc"] = "2026-06-03T17:59:59Z"
    with pytest.raises(ValueError, match="window_end_utc must be after start"):
        harness.build_future_scale_route_depth_benchmark(
            now_utc=FIXED_NOW,
            production_capture_window=stale_time,
        )


def test_validate_rejects_path_and_secret_leaks() -> None:
    report = harness.build_future_scale_route_depth_benchmark(now_utc=FIXED_NOW)

    for value in [
        r"C:\tmp\route-depth.json",
        "C:tmp",
        "data/tmp/route-depth.json",
        "../route-depth.json",
        "/mnt/data/route-depth.json",
        "docs/internal/path.md",
        "tools/secret_dump.py",
        "Bearer SECRET_TOKEN_1234567890",
        "sk-1234567890abcdef1234567890abcdef",
        "AKIA1234567890ABCDEFEXTRA",
        "anthropic",
        "cohere",
        "command",
        "command-r",
        "deepseek",
        "falcon",
        "gemma",
        "google",
        "gpt-4o",
        "huggingface",
        "llama",
        "mistral",
        "mixtral",
        "mpt",
        "ollama",
        "openai",
        "phi",
        "poro",
        "qwen",
        "claude-3-5-sonnet",
        "gemini-1.5-pro",
        "yi",
        "gpt4o_hit",
        "llama3_case",
        "cohere_internal_model",
        "mpt7b_case",
        "hf://org/model/org/model:latest",
        "org/model:latest",
    ]:
        mutated = deepcopy(report)
        mutated["non_claims"].append(value)
        errors = harness.validate_benchmark_report(mutated)
        assert any("forbidden secret/path-like string" in error for error in errors)

    clean = deepcopy(report)
    clean["git"]["branch"] = "feature/normal-branch"
    assert harness.validate_benchmark_report(clean) == []

    clean = deepcopy(report)
    clean["non_claims"].append("does not read docs paths")
    assert harness.validate_benchmark_report(clean) == []

    clean = deepcopy(report)
    clean["source_paths"] = list(harness.SOURCE_PATHS)
    assert harness.validate_benchmark_report(clean) == []

    for path, value in [
        ("source_paths", "docs/internal/path.md"),
        ("axis_definition_source", "docs/internal/path.md"),
    ]:
        mutated = deepcopy(report)
        if path == "source_paths":
            mutated[path].append(value)
        else:
            mutated[path] = value
        errors = harness.validate_benchmark_report(mutated)
        assert any("forbidden secret/path-like string" in error for error in errors)

    clean = deepcopy(report)
    clean["cases"][0]["case_id"] = "yield_route_case"
    assert harness.validate_benchmark_report(clean) == []

    for value in [
        "gpt4o_hit",
        "llama3_case",
        "cohere_internal_model",
        "mpt7b_case",
    ]:
        mutated = deepcopy(report)
        mutated["cases"][0]["case_id"] = value
        errors = harness.validate_benchmark_report(mutated)
        assert any("forbidden secret/path-like string" in error for error in errors)

    for value in [
        "cohere-internal-model",
        "command-r-internal-model",
        "falcon-internal-model",
        "yi-internal-model",
        "mpt-internal-model",
    ]:
        mutated = deepcopy(report)
        mutated["git"]["branch"] = value
        errors = harness.validate_benchmark_report(mutated)
        assert any("forbidden secret/path-like string" in error for error in errors)


def test_cli_json_writes_artifacts_without_absolute_path_leak(tmp_path: Path) -> None:
    out_dir = tmp_path / "route-depth"
    capture_path = tmp_path / "input" / "capture-window.json"
    capture_path.parent.mkdir()
    capture_path.write_text(
        json.dumps(_valid_capture_window_payload(), sort_keys=True),
        encoding="utf-8",
    )

    result = _run_cli(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-02T20:55:00Z",
        "--production-capture-window-json",
        str(capture_path),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["generated_at_utc"] == "2026-06-02T20:55:00Z"
    assert payload["ok"] is True
    json_path = out_dir / harness.JSON_ARTIFACT_NAME
    histogram_path = out_dir / harness.PRODUCTION_HISTOGRAM_ARTIFACT_NAME
    capture_attachment_path = out_dir / harness.PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_NAME
    md_path = out_dir / harness.MARKDOWN_ARTIFACT_NAME
    assert json_path.exists()
    assert histogram_path.exists()
    assert capture_attachment_path.exists()
    assert md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["ok"] is True
    histogram_payload = json.loads(histogram_path.read_text(encoding="utf-8"))
    assert (
        histogram_payload["artifact_status"]
        == "production_histogram_artifact_contract_available"
    )
    assert histogram_payload["production_runtime_data_attached"] is False
    attachment_payload = json.loads(
        capture_attachment_path.read_text(encoding="utf-8")
    )
    assert attachment_payload["attachment_status"] == "operator_capture_window_attached"
    assert attachment_payload["production_runtime_data_attached"] is True
    assert attachment_payload["capture_window_count"] == 1
    combined = result.stdout + result.stderr
    combined += json_path.read_text(encoding="utf-8")
    combined += histogram_path.read_text(encoding="utf-8")
    combined += capture_attachment_path.read_text(encoding="utf-8")
    combined += md_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in combined
    assert str(capture_path) not in combined
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
    assert (
        "production_histogram_artifact: "
        "`production_histogram_artifact_contract_available`"
        in markdown
    )
    assert "production_histogram_runtime_data_attached: `false`" in markdown
    assert (
        "production_capture_window_attachment: "
        "`capture_window_attachment_contract_available`"
        in markdown
    )
    assert "production_capture_window_runtime_data_attached: `false`" in markdown
    assert "production_capture_window_count: `0`" in markdown
    assert "not a production baseline" in markdown
    assert "not proof of superior intelligence" in markdown

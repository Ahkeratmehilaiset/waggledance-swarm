from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_future_scale_latency_bench as harness  # noqa: E402

SCRIPT = ROOT / "tools" / "run_future_scale_latency_bench.py"
FIXED_NOW = datetime(2026, 6, 4, 20, 15, tzinfo=timezone.utc)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_latency_benchmark_reports_local_fixture_measurement() -> None:
    report = harness.build_future_scale_latency_benchmark(now_utc=FIXED_NOW)

    assert report["benchmark_version"] == "future_scale_latency.v1"
    assert report["schema_version"] == "latency_benchmark.v1"
    assert report["generated_at_utc"] == "2026-06-04T20:15:00Z"
    assert report["measurement_scope"] == "local"
    assert report["stage_aliases_used"] == [
        "language_detection",
        "hot_cache",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
    ]
    assert report["synthetic_fixtures_alias"] == "v3.latency_fixtures.local.v1"
    assert report["fixture_case_count"] == 20
    assert len(report["fixtures_sha256"]) == 64
    assert report["latency_metric_family"] == (
        "waggledance_route_stage_request_latency_histogram_ms"
    )
    assert report["latency_bucket_metric"].endswith("_bucket")
    assert report["latency_sum_metric"].endswith("_sum")
    assert report["latency_count_metric"].endswith("_count")
    assert report["latency_observations"] == [
        {
            "run_id": "run-001",
            "stage_alias": "language_detection",
            "p50_ms": 1.4,
            "p95_ms": 1.76,
            "p99_ms": 1.792,
            "samples": 5,
            "finite": True,
            "delta_vs_baseline_p95": None,
        },
        {
            "run_id": "run-002",
            "stage_alias": "hot_cache",
            "p50_ms": 0.4,
            "p95_ms": 0.58,
            "p99_ms": 0.596,
            "samples": 5,
            "finite": True,
            "delta_vs_baseline_p95": None,
        },
        {
            "run_id": "run-003",
            "stage_alias": "deterministic_solver",
            "p50_ms": 6.0,
            "p95_ms": 7.8,
            "p99_ms": 7.96,
            "samples": 5,
            "finite": True,
            "delta_vs_baseline_p95": None,
        },
        {
            "run_id": "run-004",
            "stage_alias": "hybrid_retrieval_8_cell",
            "p50_ms": 13.0,
            "p95_ms": 16.6,
            "p99_ms": 16.92,
            "samples": 5,
            "finite": True,
            "delta_vs_baseline_p95": None,
        },
    ]
    assert report["aggregate"] == {
        "mean_p95_ms": 6.685,
        "median_p95_ms": 4.78,
        "p95_of_p99_ms": 15.576,
        "p99_of_p95_ms": 16.336,
        "finite": True,
    }
    assert report["internal_controls"] == {
        "positive_control_p95_ms": 3.0,
        "negative_control_p95_ms": 18.0,
        "control_delta_ms": 15.0,
        "controls_measured": True,
    }
    assert report["claim_gate_satisfied"] is False
    assert report["claim_safe"] is False
    assert report["literal_future_claim_safe"] is False
    assert report["controls_present"] is False
    assert report["runtime_authority_granted"] is False
    assert report["external_writes_applied"] is False
    assert report["required_runtime_evidence_present"] is False
    assert report["no_cloud_api_calls"] is True
    assert report["no_model_pull_or_download"] is True
    assert report["deterministic_seed"].startswith("latency-bench-20260604-seed-")
    assert report["reproduce_command"] == (
        "python tools/run_future_scale_latency_bench.py "
        "--fixtures v3.latency_fixtures.local.v1 --offline --deterministic"
    )
    assert harness.validate_latency_benchmark_report(report) == []
    json.dumps(report, allow_nan=False)


def test_build_accepts_custom_local_fixture_alias_and_samples() -> None:
    report = harness.build_future_scale_latency_benchmark(
        fixtures_alias="latency.local.micro_fixture.v1",
        stage_latency_samples_ms={"language_detection": (2.0, 4.0, 6.0)},
        now_utc=FIXED_NOW,
    )

    assert report["stage_aliases_used"] == ["language_detection"]
    assert report["fixture_case_count"] == 3
    assert report["latency_observations"] == [
        {
            "run_id": "run-001",
            "stage_alias": "language_detection",
            "p50_ms": 4.0,
            "p95_ms": 5.8,
            "p99_ms": 5.96,
            "samples": 3,
            "finite": True,
            "delta_vs_baseline_p95": None,
        }
    ]
    assert report["aggregate"]["mean_p95_ms"] == 5.8
    assert report["aggregate"]["p95_of_p99_ms"] == 5.96
    assert harness.validate_latency_benchmark_report(report) == []


def test_validate_rejects_claim_gate_type_confusion() -> None:
    report = harness.build_future_scale_latency_benchmark(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["claim_gate_satisfied"] = "false"
    errors = harness.validate_latency_benchmark_report(mutated)
    assert "claim_gate_satisfied must be exact false bool" in errors

    mutated = deepcopy(report)
    mutated["required_runtime_evidence_present"] = True
    errors = harness.validate_latency_benchmark_report(mutated)
    assert "required_runtime_evidence_present must be exact false bool" in errors


def test_validate_rejects_non_finite_and_aggregate_tampering() -> None:
    report = harness.build_future_scale_latency_benchmark(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["latency_observations"][0]["p95_ms"] = float("inf")
    mutated["aggregate"]["mean_p95_ms"] = float("nan")
    errors = harness.validate_latency_benchmark_report(mutated)
    assert "latency_observations[0].p95_ms must be finite" in errors
    assert any("non-finite number" in error for error in errors)

    tampered = deepcopy(report)
    tampered["aggregate"]["median_p95_ms"] = 42.0
    errors = harness.validate_latency_benchmark_report(tampered)
    assert "aggregate.median_p95_ms does not match latency_observations" in errors


def test_validate_rejects_path_secret_and_provider_leaks() -> None:
    report = harness.build_future_scale_latency_benchmark(now_utc=FIXED_NOW)

    for value in [
        r"C:\tmp\latency.json",
        "C:tmp",
        "../latency.json",
        "/mnt/data/latency.json",
        "Bearer SECRET_TOKEN_1234567890",
        "sk-1234567890abcdef1234567890abcdef",
        "AKIA1234567890ABCDEFEXTRA",
        "gpt4o_hit",
        "cohere_internal_model",
        "command-r-internal-model",
        "falcon-internal-model",
        "mpt7b_case",
    ]:
        mutated = deepcopy(report)
        mutated["latency_observations"][0]["run_id"] = value
        errors = harness.validate_latency_benchmark_report(mutated)
        assert errors


def test_cli_requires_offline_and_deterministic_flags() -> None:
    missing = _run_cli("--json")
    assert missing.returncode == 2
    assert "--offline --deterministic" in missing.stderr

    completed = _run_cli(
        "--fixtures",
        "v3.latency_fixtures.local.v1",
        "--offline",
        "--deterministic",
        "--now",
        "2026-06-04T20:15:00Z",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["aggregate"]["mean_p95_ms"] == 6.685
    assert payload["claim_gate_satisfied"] is False


def test_cli_writes_only_explicit_artifacts_without_path_leak(tmp_path: Path) -> None:
    completed = _run_cli(
        "--offline",
        "--deterministic",
        "--now",
        "2026-06-04T20:15:00Z",
        "--out-dir",
        str(tmp_path),
    )

    assert completed.returncode == 0, completed.stderr
    json_path = tmp_path / harness.JSON_ARTIFACT_NAME
    md_path = tmp_path / harness.MARKDOWN_ARTIFACT_NAME
    assert json_path.exists()
    assert md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["required_runtime_evidence_present"] is False
    combined = completed.stdout + completed.stderr
    combined += json_path.read_text(encoding="utf-8")
    combined += md_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in combined
    assert "C:\\Users" not in combined
    assert "Bearer " not in combined
    assert "production evidence" in md_path.read_text(encoding="utf-8")


def test_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = _run_cli(
        "--offline",
        "--deterministic",
        "--out-dir",
        str(tmp_path / "out"),
        "--now",
        "2026-06-04T23:15:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr

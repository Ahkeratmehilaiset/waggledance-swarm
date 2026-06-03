from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_future_scale_insight_bench as harness  # noqa: E402


SCRIPT = ROOT / "tools" / "run_future_scale_insight_bench.py"
FIXED_NOW = datetime(2026, 6, 3, 15, 30, tzinfo=timezone.utc)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_insight_score_benchmark_reports_local_dream_mode_measurement() -> None:
    report = harness.build_future_scale_insight_benchmark(now_utc=FIXED_NOW)

    assert report["benchmark_version"] == "future_scale_insight_score.v1"
    assert report["schema_version"] == "insight_score_benchmark.v1"
    assert report["generated_at_utc"] == "2026-06-03T15:30:00Z"
    assert report["measurement_scope"] == "local"
    assert report["corpus_alias"] == "v12.a3.synth_adversarial.v0"
    assert report["corpus_case_count"] == 12
    assert len(report["corpus_sha256"]) == 64
    assert report["solver_aliases_used"] == [
        "dream.math.v1",
        "dream.routing.v1",
        "dream.safety.v1",
    ]
    assert [run["insight_score"] for run in report["insight_runs"]] == [
        0.25,
        0.5,
        -0.25,
    ]
    assert report["aggregate"]["mean_insight_score"] == 0.166667
    assert report["aggregate"]["median_insight_score"] == 0.25
    assert report["aggregate"]["scale_trend_slope"] == -0.25
    assert report["aggregate"]["p95_insight"] == 0.475
    assert report["internal_controls"] == {
        "positive_control_score": 1.0,
        "negative_control_score": -1.0,
        "control_delta": 2.0,
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
    assert report["deterministic_seed"].startswith("insight-bench-20260603-seed-")
    assert report["reproduce_command"] == (
        "python tools/run_future_scale_insight_bench.py "
        "--corpus v12.a3.synth_adversarial.v0 --offline --deterministic"
    )
    assert harness.validate_insight_benchmark_report(report) == []
    json.dumps(report, allow_nan=False)


def test_build_uses_existing_compute_insight_score_semantics() -> None:
    report = harness.build_future_scale_insight_benchmark(
        now_utc=FIXED_NOW,
        run_fixtures=[
            {
                "run_id": "run-101",
                "solver_alias": "dream.math.v1",
                "outcomes": ("success", "success", "failure", "inconclusive"),
            }
        ],
    )

    assert report["insight_runs"] == [
        {
            "run_id": "run-101",
            "solver_alias": "dream.math.v1",
            "insight_score": 0.25,
            "cases_evaluated": 4,
            "finite": True,
        }
    ]
    assert report["aggregate"]["mean_insight_score"] == 0.25
    assert report["aggregate"]["scale_trend_slope"] == 0.0


def test_validate_rejects_claim_gate_type_confusion() -> None:
    report = harness.build_future_scale_insight_benchmark(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["claim_gate_satisfied"] = "false"
    errors = harness.validate_insight_benchmark_report(mutated)
    assert "claim_gate_satisfied must be exact false bool" in errors

    mutated = deepcopy(report)
    mutated["required_runtime_evidence_present"] = True
    errors = harness.validate_insight_benchmark_report(mutated)
    assert "required_runtime_evidence_present must be exact false bool" in errors


def test_validate_rejects_non_finite_and_aggregate_tampering() -> None:
    report = harness.build_future_scale_insight_benchmark(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["insight_runs"][0]["insight_score"] = float("inf")
    mutated["aggregate"]["mean_insight_score"] = float("nan")
    errors = harness.validate_insight_benchmark_report(mutated)
    assert "insight_runs[0].insight_score must be finite" in errors
    assert any("non-finite number" in error for error in errors)

    tampered = deepcopy(report)
    tampered["aggregate"]["median_insight_score"] = 1.0
    errors = harness.validate_insight_benchmark_report(tampered)
    assert "aggregate.median_insight_score does not match insight_runs" in errors


def test_validate_rejects_path_secret_and_provider_leaks() -> None:
    report = harness.build_future_scale_insight_benchmark(now_utc=FIXED_NOW)

    for value in [
        r"C:\tmp\insight.json",
        "C:tmp",
        "../insight.json",
        "/mnt/data/insight.json",
        "Bearer SECRET_TOKEN_1234567890",
        "sk-1234567890abcdef1234567890abcdef",
        "AKIA1234567890ABCDEFEXTRA",
        "gpt-4o",
        "claude-3-5-sonnet",
        "cohere_internal_model",
        "mpt7b_case",
        "hf://org/model/org/model:latest",
    ]:
        mutated = deepcopy(report)
        mutated["insight_runs"][0]["run_id"] = value
        errors = harness.validate_insight_benchmark_report(mutated)
        assert errors


def test_cli_requires_offline_and_deterministic_flags() -> None:
    missing = _run_cli("--json")
    assert missing.returncode == 2
    assert "--offline --deterministic" in missing.stderr

    completed = _run_cli(
        "--corpus",
        "v12.a3.synth_adversarial.v0",
        "--offline",
        "--deterministic",
        "--now",
        "2026-06-03T15:30:00Z",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["aggregate"]["mean_insight_score"] == 0.166667
    assert payload["claim_gate_satisfied"] is False


def test_cli_writes_only_explicit_artifacts(tmp_path: Path) -> None:
    completed = _run_cli(
        "--offline",
        "--deterministic",
        "--now",
        "2026-06-03T15:30:00Z",
        "--out-dir",
        str(tmp_path),
    )
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / harness.JSON_ARTIFACT_NAME).exists()
    assert (tmp_path / harness.MARKDOWN_ARTIFACT_NAME).exists()
    payload = json.loads((tmp_path / harness.JSON_ARTIFACT_NAME).read_text())
    assert payload["required_runtime_evidence_present"] is False
    assert "production evidence" in (tmp_path / harness.MARKDOWN_ARTIFACT_NAME).read_text(
        encoding="utf-8"
    )

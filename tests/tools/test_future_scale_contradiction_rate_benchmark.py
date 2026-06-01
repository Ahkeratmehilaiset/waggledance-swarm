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

import run_future_scale_contradiction_rate_benchmark as harness  # noqa: E402


SCRIPT = ROOT / "tools" / "run_future_scale_contradiction_rate_benchmark.py"
FIXED_NOW = datetime(2026, 6, 1, 20, 55, tzinfo=timezone.utc)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_contradiction_rate_benchmark_reports_local_fixture_measurement() -> None:
    report = harness.build_future_scale_contradiction_rate_benchmark(now_utc=FIXED_NOW)

    assert report["ok"] is True
    assert report["report_version"] == harness.REPORT_VERSION
    assert report["generated_at_utc"] == "2026-06-01T20:55:00Z"
    assert report["axis_id"] == "contradiction_rate"
    assert report["measurement_label"] == "MEASURED_LOCAL_ONLY"
    assert report["benchmark_scope"] == "local_deterministic_fixture_only"
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
    assert report["contract_validation"] == {
        "ok": True,
        "error_count": 0,
        "errors": [],
    }

    result = report["benchmark_result"]
    assert result["proposal_count"] == 3
    assert result["expected_contradictions"] == 1
    assert result["contradiction_rejections"] == 1
    assert result["false_positive_count"] == 0
    assert result["false_negative_count"] == 0
    assert result["contradiction_rate"] == 0.333333
    assert result["rate_is_production_baseline"] is False


def test_fixture_cases_exercise_contradiction_gate_and_non_contradictions() -> None:
    report = harness.build_future_scale_contradiction_rate_benchmark(now_utc=FIXED_NOW)
    by_id = {case["case_id"]: case for case in report["cases"]}

    conflict = by_id["thermal_conflicting_existing_invariant"]
    assert conflict["expected_contradiction"] is True
    assert conflict["detected_contradiction"] is True
    assert conflict["proposal_gate_verdict"] == "REJECT_CONTRADICTION"
    assert conflict["gate_ok"] is False
    assert conflict["contradiction_gate_error_count"] == 1

    consistent = by_id["thermal_consistent_existing_invariant"]
    assert consistent["expected_contradiction"] is False
    assert consistent["detected_contradiction"] is False
    assert consistent["proposal_gate_verdict"] == "ACCEPT_CANDIDATE"
    assert consistent["gate_ok"] is True

    other_cell = by_id["other_cell_conflict_ignored"]
    assert other_cell["expected_contradiction"] is False
    assert other_cell["detected_contradiction"] is False
    assert other_cell["proposal_gate_verdict"] == "ACCEPT_CANDIDATE"
    assert other_cell["gate_ok"] is True


def test_validate_rejects_claim_gate_type_confusion() -> None:
    report = harness.build_future_scale_contradiction_rate_benchmark(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["claim_gate_satisfied"] = "false"
    errors = harness.validate_benchmark_report(mutated)
    assert "claim_gate_satisfied must be exact false bool" in errors

    mutated = deepcopy(report)
    mutated["external_writes_applied"] = 0
    errors = harness.validate_benchmark_report(mutated)
    assert "external_writes_applied must be exact false bool" in errors


def test_validate_rejects_claim_upgrade_and_non_finite_rate() -> None:
    report = harness.build_future_scale_contradiction_rate_benchmark(now_utc=FIXED_NOW)

    upgraded = deepcopy(report)
    upgraded["measurement_label"] = "PROVEN"
    upgraded["claim_safe"] = True
    errors = harness.validate_benchmark_report(upgraded)
    assert "measurement_label must not upgrade to a proven claim" in errors
    assert "measurement_label must be MEASURED_LOCAL_ONLY" in errors
    assert "claim_safe must be exact false bool" in errors

    non_finite = deepcopy(report)
    non_finite["benchmark_result"]["contradiction_rate"] = math.inf
    errors = harness.validate_benchmark_report(non_finite)
    assert "benchmark_result.contradiction_rate must be finite" in errors
    assert any("non-finite number" in error for error in errors)


def test_validate_rejects_case_bool_type_confusion() -> None:
    report = harness.build_future_scale_contradiction_rate_benchmark(now_utc=FIXED_NOW)
    mutated = deepcopy(report)
    mutated["cases"][0]["detected_contradiction"] = "true"

    errors = harness.validate_benchmark_report(mutated)

    assert "cases[0].detected_contradiction must be exact bool" in errors


def test_cli_json_writes_artifacts_without_absolute_path_leak(tmp_path: Path) -> None:
    out_dir = tmp_path / "contradiction-rate"

    result = _run_cli(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-01T20:55:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["generated_at_utc"] == "2026-06-01T20:55:00Z"
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
        "2026-06-01T23:55:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr


def test_markdown_preserves_no_overclaim_guardrails() -> None:
    report = harness.build_future_scale_contradiction_rate_benchmark(now_utc=FIXED_NOW)

    markdown = harness.render_markdown(report)

    assert "Future-Scale Contradiction Rate Benchmark" in markdown
    assert "claim_gate_satisfied: `false`" in markdown
    assert "claim_safe: `false`" in markdown
    assert "literal_future_claim_safe: `false`" in markdown
    assert "external_writes_applied: `false`" in markdown
    assert "not a production baseline" in markdown
    assert "not a claim that contradiction handling is solved" in markdown
    assert "REJECT_CONTRADICTION" in markdown

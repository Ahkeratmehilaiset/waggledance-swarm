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

import build_runtime_gap_scheduler_candidate_artifact as builder  # noqa: E402
import run_runtime_gap_detector_report as gap_report  # noqa: E402


SCRIPT = ROOT / "tools" / "build_runtime_gap_scheduler_candidate_artifact.py"
FIXED_NOW = datetime(2026, 6, 6, 18, 45, tzinfo=timezone.utc)


def _report(**overrides):
    report = gap_report.build_runtime_gap_detector_report(
        now_utc=FIXED_NOW,
        **overrides,
    )
    assert gap_report.validate_runtime_gap_detector_report(report) == []
    return report


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_artifact_renders_ready_candidates_without_authority() -> None:
    artifact = builder.build_runtime_gap_scheduler_candidate_artifact(_report())

    assert artifact["artifact_version"] == builder.ARTIFACT_VERSION
    assert artifact["schema_version"] == builder.SCHEMA_VERSION
    assert artifact["measurement_scope"] == builder.MEASUREMENT_SCOPE
    assert artifact["generated_at_utc"] == "2026-06-06T18:45:00Z"
    assert artifact["scheduler_candidate_count"] == 1
    assert artifact["blocked_candidate_count"] == 1
    assert artifact["artifact_path_free"] is True
    assert artifact["bridge_event_written"] is False
    assert artifact["scheduler_enqueue_allowed"] is False
    assert artifact["scheduler_tick_allowed"] is False

    candidate = artifact["scheduler_candidates"][0]
    assert candidate["candidate_kind"] == "runtime_gap_signal_group"
    assert candidate["intent_key"] == "threshold_rule:energy:hot_threshold"
    assert candidate["queue_priority"] == "normal"
    assert candidate["priority_weight"] == 30
    assert candidate["fast_track_priority"] is False
    assert candidate["raw_signal_payload_included"] is False
    assert candidate["raw_query_exported"] is False
    for field in builder.TOP_LEVEL_FALSE_FIELDS:
        assert artifact[field] is False
    for field in builder.CANDIDATE_FALSE_FIELDS:
        assert candidate[field] is False
    assert builder.validate_runtime_gap_scheduler_candidate_artifact(artifact) == []
    assert "AutogrowthScheduler" not in json.dumps(artifact)
    assert "RuntimeGapDetector.record" not in json.dumps(artifact)


def test_artifact_rejects_source_report_exact_false_type_confusion() -> None:
    report = _report()
    report["queue_writes_applied"] = "false"

    with pytest.raises(ValueError, match="source report invalid"):
        builder.build_runtime_gap_scheduler_candidate_artifact(report)


def test_artifact_validation_rejects_preview_exact_false_type_confusion() -> None:
    artifact = builder.build_runtime_gap_scheduler_candidate_artifact(_report())

    mutated = deepcopy(artifact)
    mutated["scheduler_enqueue_allowed"] = "false"
    errors = builder.validate_runtime_gap_scheduler_candidate_artifact(mutated)
    assert "scheduler_enqueue_allowed must be exact false bool" in errors

    mutated = deepcopy(artifact)
    mutated["scheduler_candidates"][0]["gate_skip_allowed"] = "false"
    errors = builder.validate_runtime_gap_scheduler_candidate_artifact(mutated)
    assert (
        "scheduler_candidates[0].gate_skip_allowed must be exact false bool"
        in errors
    )


def test_artifact_rejects_unsafe_source_candidate_scalar() -> None:
    report = _report()
    report["candidate_intents"][0]["intent_seed"] = r"C:\secret\gap.json"

    with pytest.raises(ValueError, match="source report invalid"):
        builder.build_runtime_gap_scheduler_candidate_artifact(report)


def test_no_ready_candidates_does_not_fall_back_to_default_preview() -> None:
    report = _report(min_signals_per_intent=4)
    artifact = builder.build_runtime_gap_scheduler_candidate_artifact(report)

    assert report["scheduler_candidate_count"] == 0
    assert artifact["scheduler_candidate_count"] == 0
    assert artifact["scheduler_candidates"] == []
    assert artifact["blocked_candidate_count"] == 2
    assert builder.validate_runtime_gap_scheduler_candidate_artifact(artifact) == []


def test_cli_requires_offline_and_deterministic_flags() -> None:
    missing = _run_cli("--json")

    assert missing.returncode == 2
    assert "--offline --deterministic" in missing.stderr


def test_cli_reads_report_without_recording_input_path(tmp_path: Path) -> None:
    report_path = tmp_path / "runtime_gap_detector_report.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    out_dir = tmp_path / "out"

    completed = _run_cli(
        "--offline",
        "--deterministic",
        "--report-json",
        str(report_path),
        "--out-dir",
        str(out_dir),
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    artifact = json.loads(completed.stdout)
    serialized = json.dumps(artifact, sort_keys=True)
    assert artifact["scheduler_candidate_count"] == 1
    assert str(report_path) not in serialized
    assert "runtime_gap_detector_report.json" not in serialized
    assert (out_dir / builder.JSON_ARTIFACT_NAME).exists()
    assert (out_dir / builder.MARKDOWN_ARTIFACT_NAME).exists()


def test_cli_empty_operator_export_does_not_use_default_fixtures(
    tmp_path: Path,
) -> None:
    export = tmp_path / "empty_signals.json"
    export.write_text('{"signals":[]}', encoding="utf-8")

    completed = _run_cli(
        "--offline",
        "--deterministic",
        "--signals-json",
        str(export),
        "--json",
    )

    assert completed.returncode == 1
    assert "signal_fixtures must be non-empty" in completed.stderr
    assert completed.stdout == ""

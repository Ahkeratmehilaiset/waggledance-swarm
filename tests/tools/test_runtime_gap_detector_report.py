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

import run_runtime_gap_detector_report as harness  # noqa: E402


SCRIPT = ROOT / "tools" / "run_runtime_gap_detector_report.py"
FIXED_NOW = datetime(2026, 6, 4, 9, 10, tzinfo=timezone.utc)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_report_marks_only_threshold_group_ready_for_scheduler_candidate() -> None:
    report = harness.build_runtime_gap_detector_report(now_utc=FIXED_NOW)

    assert report["report_version"] == "wd.runtime_gap_detector_report.v1"
    assert report["schema_version"] == "runtime_gap_detector_report.v1"
    assert report["generated_at_utc"] == "2026-06-04T09:10:00Z"
    assert report["measurement_scope"] == "local_read_only_gap_signal_report"
    assert report["input_source_kind"] == "deterministic_fixture"
    assert report["input_signal_count"] == 6
    assert report["accepted_low_risk_signal_count"] == 4
    assert report["candidate_intent_count"] == 2
    assert report["scheduler_candidate_count"] == 1
    assert report["ready_for_scheduler_candidate"] is True
    assert report["signal_rejections"] == {
        "total": 2,
        "by_reason": {
            "family_not_low_risk": 1,
            "missing_family_kind": 1,
        },
    }

    by_key = {item["intent_key"]: item for item in report["candidate_intents"]}
    ready = by_key["threshold_rule:energy:hot_threshold"]
    blocked = by_key["lookup_table:general:color_map"]
    assert ready["signal_count"] == 3
    assert ready["priority_estimate"] == 30
    assert ready["ready_for_scheduler_candidate"] is True
    assert ready["blockers"] == []
    assert len(ready["spec_seed_digest"]) == 64
    assert blocked["ready_for_scheduler_candidate"] is False
    assert blocked["blockers"] == ["below_min_signals"]
    assert harness.validate_runtime_gap_detector_report(report) == []
    json.dumps(report, allow_nan=False)


def test_report_never_grants_runtime_or_queue_authority() -> None:
    report = harness.build_runtime_gap_detector_report(now_utc=FIXED_NOW)

    for field in harness.SAFE_FALSE_FIELDS:
        assert report[field] is False
    assert report["no_cloud_api_calls"] is True
    assert report["no_model_pull_or_download"] is True
    assert "AutogrowthScheduler" not in json.dumps(report)
    assert "RuntimeGapDetector.record" not in json.dumps(report)


def test_validate_rejects_claim_gate_type_confusion() -> None:
    report = harness.build_runtime_gap_detector_report(now_utc=FIXED_NOW)

    mutated = deepcopy(report)
    mutated["queue_writes_applied"] = "false"
    errors = harness.validate_runtime_gap_detector_report(mutated)
    assert "queue_writes_applied must be exact false bool" in errors

    mutated = deepcopy(report)
    mutated["scheduler_tick_executed"] = True
    errors = harness.validate_runtime_gap_detector_report(mutated)
    assert "scheduler_tick_executed must be exact false bool" in errors


def test_validate_rejects_path_secret_and_provider_leaks() -> None:
    report = harness.build_runtime_gap_detector_report(now_utc=FIXED_NOW)

    for value in [
        r"C:\tmp\gap.json",
        "../gap.json",
        "/tmp/gap.json",
        "Bearer SECRET_TOKEN_1234567890",
        "sk-1234567890abcdef1234567890abcdef",
        "gpt-4o-gap",
        "grok-scout-gap",
        "hf://org/model",
    ]:
        mutated = deepcopy(report)
        mutated["candidate_intents"][0]["intent_seed"] = value
        assert harness.validate_runtime_gap_detector_report(mutated)


def test_unsafe_input_payload_is_rejected_without_raw_leak() -> None:
    report = harness.build_runtime_gap_detector_report(
        now_utc=FIXED_NOW,
        signal_fixtures=[
            {
                "kind": "runtime_miss",
                "family_kind": "threshold_rule",
                "cell_coord": "energy",
                "intent_seed": "hot_threshold",
                "weight": 1.0,
                "payload": {"note": r"C:\secret\payload.json"},
                "spec_seed": {"spec": {"threshold": 30.0}},
            },
            {
                "kind": "runtime_miss",
                "family_kind": "threshold_rule",
                "cell_coord": "energy",
                "intent_seed": "hot_threshold",
                "weight": 1.0,
                "payload": {"note": "Bearer SECRET_TOKEN_1234567890"},
                "spec_seed": {"spec": {"threshold": 30.0}},
            },
        ],
    )

    serialized = json.dumps(report, sort_keys=True)
    assert report["accepted_low_risk_signal_count"] == 0
    assert report["scheduler_candidate_count"] == 0
    assert report["input_rejections"] == {
        "total": 2,
        "by_reason": {"unsafe_scalar": 2},
    }
    assert "SECRET_TOKEN" not in serialized
    assert "secret" not in serialized.lower()
    assert r"C:\secret" not in serialized


def test_cli_requires_offline_and_deterministic_flags() -> None:
    missing = _run_cli("--json")
    assert missing.returncode == 2
    assert "--offline --deterministic" in missing.stderr

    completed = _run_cli(
        "--offline",
        "--deterministic",
        "--now",
        "2026-06-04T09:10:00Z",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["scheduler_candidate_count"] == 1
    assert payload["queue_writes_applied"] is False


def test_cli_reads_signal_export_without_recording_input_path(tmp_path: Path) -> None:
    export = tmp_path / "signals.json"
    export.write_text(
        json.dumps(
            {
                "signals": [
                    {
                        "kind": "runtime_miss",
                        "family_kind": "lookup_table",
                        "cell_coord": "general",
                        "intent_seed": "color",
                        "weight": 1.0,
                        "payload": {"miss_reason": "miss_no_solver"},
                        "spec_seed": {"spec": {"table": {"blue": "route"}}},
                    },
                    {
                        "kind": "runtime_miss",
                        "family_kind": "lookup_table",
                        "cell_coord": "general",
                        "intent_seed": "color",
                        "weight": 1.0,
                        "payload": {"miss_reason": "miss_no_solver"},
                        "spec_seed": {"spec": {"table": {"green": "hold"}}},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    completed = _run_cli(
        "--offline",
        "--deterministic",
        "--signals-json",
        str(export),
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-04T09:10:00Z",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads((out_dir / harness.JSON_ARTIFACT_NAME).read_text())
    assert payload["input_source_kind"] == "operator_owned_signal_export"
    assert payload["scheduler_candidate_count"] == 1
    serialized = json.dumps(payload)
    assert str(export) not in serialized
    assert "signals.json" not in serialized
    assert (out_dir / harness.MARKDOWN_ARTIFACT_NAME).exists()

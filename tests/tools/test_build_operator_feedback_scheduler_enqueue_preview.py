from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_operator_feedback_scheduler_enqueue_preview as builder  # noqa: E402


SCRIPT = ROOT / "tools" / "build_operator_feedback_scheduler_enqueue_preview.py"
UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
OPERATOR_A = f"bridge:operator:{UUID_A}"
OPERATOR_B = f"bridge:operator:{UUID_B}"


def _feedback(
    *,
    feedback_id: str,
    operator_id: str = OPERATOR_A,
    priority: str = "high",
    submitted_at_utc: str = "2026-06-06T12:00:00Z",
) -> dict:
    return {
        "event_type": "ops_feedback",
        "feedback_id": feedback_id,
        "feedback_kind": "needs_solver",
        "query_class_hash": "sha256:" + "a" * 64,
        "operator_id": operator_id,
        "priority": priority,
        "submitted_at_utc": submitted_at_utc,
    }


def _bridge_event(
    feedback: dict,
    *,
    agent_uuid: str = UUID_A,
    ts_utc: str = "2026-06-06T12:00:01Z",
    nested: bool = True,
) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": "operator",
        "type": "message",
        "task_id": "operator-feedback-enqueue-preview-fixture",
        "status": "ops_feedback_received",
        "severity": "",
        "to": "",
        "message": "operator feedback fixture",
        "paths": [],
        "write_scope": [],
        "run_id": "operator-feedback-enqueue-preview-fixture-run",
        "role": "operator",
        "agent_uuid": agent_uuid,
        "session_id": "operator-session",
        "capabilities": ["ops_feedback"],
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": {"ops_feedback": feedback} if nested else feedback,
    }


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_builds_enqueue_preview_from_durable_bridge_log_without_writes(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(_feedback(feedback_id="fb-001")),
    ])

    completed = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-001",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is True
    assert report["durable_bridge_log_source"] is True
    assert report["input_path_recorded"] is False
    assert report["verified_operator_id"] == OPERATOR_A
    assert report["rate_limit_source"] == "durable_bridge_log"
    assert report["events_checked"] == 1
    for field in builder.TOP_LEVEL_FALSE_FIELDS:
        assert report[field] is False

    preflight = report["preflight"]
    preview = report["preview"]
    assert preflight["scheduler_enqueue_allowed"] is False
    assert preflight["scheduler_tick_allowed"] is False
    assert preview["preview_kind"] == "operator_feedback_scheduler_enqueue_preview"
    assert preview["queue_priority"] == "fast_track"
    assert preview["priority_weight"] == 100
    assert preview["fast_track_priority"] is True
    assert preview["scheduler_enqueue_allowed"] is False
    assert preview["scheduler_tick_allowed"] is False
    assert preview["queue_write_applied"] is False
    assert preview["growth_intent_created"] is False
    assert preview["bridge_event_written"] is False
    assert preview["runtime_authority_granted"] is False
    assert preview["gate_skip_allowed"] is False
    assert preview["promotion_gate_skip_allowed"] is False
    assert preview["adversarial_gate_skip_allowed"] is False
    assert preview["canary_gate_skip_allowed"] is False
    assert preview["next_required_integration"] == (
        "scheduler_enqueue_adapter_separate_pr"
    )
    assert builder.validate_operator_feedback_scheduler_enqueue_preview_cli_report(
        report
    ) == []
    assert str(events_path) not in serialized
    assert "events.jsonl" not in serialized


def test_cli_rate_limits_enqueue_preview_from_durable_bridge_log(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events = [
        _bridge_event(
            _feedback(
                feedback_id=f"prior-{index:02d}",
                submitted_at_utc=f"2026-06-06T11:{index:02d}:00Z",
            ),
            ts_utc=f"2026-06-06T11:{index:02d}:01Z",
        )
        for index in range(10)
    ]
    events.append(_bridge_event(_feedback(feedback_id="fb-limited")))
    _write_events(events_path, events)

    completed = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-limited",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    preview = report["preview"]
    assert report["ok"] is True
    assert report["operator_fast_track_count"] == 10
    assert report["global_fast_track_count"] == 10
    assert preview["rate_limited"] is True
    assert preview["queue_priority"] == "normal"
    assert preview["priority_weight"] == 0
    assert preview["fast_track_priority"] is False
    assert preview["scheduler_enqueue_allowed"] is False


def test_cli_rejects_free_string_operator_id(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(
            _feedback(feedback_id="fb-free-string", operator_id="operator:jkh")
        ),
    ])

    completed = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-free-string",
        "--json",
    )

    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert completed.stderr == ""
    assert report["ok"] is False
    assert report["preview"] is None
    assert report["events_checked"] == 1
    assert report["blockers"][0]["code"] == "operator_feedback_validation_failed"
    assert "verified bridge identity" in report["blockers"][0]["message"]


def test_cli_rejects_missing_feedback_id_without_path_leak(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(_feedback(feedback_id="fb-present")),
    ])

    completed = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-missing",
        "--json",
    )

    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert completed.stderr == ""
    assert report["ok"] is False
    assert report["preview"] is None
    assert report["events_checked"] == 1
    assert "durable bridge log" in report["blockers"][0]["message"]
    assert str(events_path) not in serialized
    assert "events.jsonl" not in serialized


def test_cli_tail_limits_bridge_log_window(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(_feedback(feedback_id="fb-old")),
        _bridge_event(
            _feedback(feedback_id="fb-new"),
            ts_utc="2026-06-06T12:01:00Z",
        ),
    ])

    completed = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-old",
        "--tail",
        "1",
        "--json",
    )

    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["events_checked"] == 1
    assert "durable bridge log" in report["blockers"][0]["message"]


def test_cli_rejects_non_finite_json_constants(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"ts_utc":"2026-06-06T12:00:01Z","payload":{"x":Infinity}}\n',
        encoding="utf-8",
    )

    completed = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-001",
        "--json",
    )

    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["blockers"][0]["code"] == "bridge_log_input_invalid"
    assert "bridge log line 1" in report["blockers"][0]["message"]


def test_report_validator_rejects_preview_authority_escalation(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(_feedback(feedback_id="fb-001")),
    ])
    durable_events = builder.preflight_cli.read_durable_bridge_events(events_path)
    report = builder.build_operator_feedback_scheduler_enqueue_preview_cli_report(
        feedback_id="fb-001",
        durable_bridge_events=durable_events,
    )
    report["preview"] = {
        **report["preview"],
        "scheduler_enqueue_allowed": True,
    }

    errors = builder.validate_operator_feedback_scheduler_enqueue_preview_cli_report(
        report
    )

    assert "preview.scheduler_enqueue_allowed must be exact false bool" in errors


def test_cli_rejects_operator_identity_mismatch(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(
            _feedback(feedback_id="fb-mismatch", operator_id=OPERATOR_B),
            agent_uuid=UUID_A,
        ),
    ])

    completed = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-mismatch",
        "--json",
    )

    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["events_checked"] == 1
    assert "verified bridge identity" in report["blockers"][0]["message"]

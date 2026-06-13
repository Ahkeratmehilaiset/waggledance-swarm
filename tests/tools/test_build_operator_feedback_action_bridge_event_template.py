from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_operator_feedback_action_bridge_event_template as builder  # noqa: E402
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


SCRIPT = ROOT / "tools" / "build_operator_feedback_action_bridge_event_template.py"
UUID_A = "11111111-1111-4111-8111-111111111111"
OPERATOR_A = f"bridge:operator:{UUID_A}"


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
) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": "operator",
        "type": "message",
        "task_id": "operator-feedback-action-template-fixture",
        "status": "ops_feedback_received",
        "severity": "",
        "to": "",
        "message": "operator feedback fixture",
        "paths": [],
        "write_scope": [],
        "run_id": "operator-feedback-action-template-fixture-run",
        "role": "operator",
        "agent_uuid": agent_uuid,
        "session_id": "operator-session",
        "capabilities": ["ops_feedback"],
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": {"ops_feedback": feedback},
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


def _build_report(events: list[dict], feedback_id: str = "fb-001") -> dict:
    return builder.build_operator_feedback_action_bridge_event_template(
        feedback_id=feedback_id,
        durable_bridge_events=events,
        agent_id="codex-tools-1",
        task_id="operator-feedback-action-template-test",
        to="operator,codex-lead-1,claude-rco-1",
        run_id="codex-tools-1-20260606T120000Z",
        session_id="codex-tools-1-20260606T120000Z",
    )


def test_action_bridge_event_template_validates_schema_and_no_authority() -> None:
    report = _build_report([_bridge_event(_feedback(feedback_id="fb-001"))])

    assert report["ok"] is True
    assert report["template_version"] == builder.TEMPLATE_VERSION
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
    assert report["fast_track_queue_priority_requested"] is True
    assert report["fast_track_is_queue_priority_only"] is True
    for field in builder.REPORT_FALSE_FIELDS:
        assert report[field] is False

    event = report["bridge_event_template"]
    validate_event(event)
    assert event["type"] == "handoff"
    assert event["status"] == builder.EVENT_STATUS
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["pid"] == 0

    payload = event["payload"]
    assert payload["event_type"] == "feedback_action_taken"
    assert payload["verified_operator_id"] == OPERATOR_A
    assert payload["rate_limit_source"] == "durable_bridge_log"
    assert payload["template_only"] is True
    assert payload["direct_bridge_write_performed"] is False
    action = payload["feedback_action_taken"]
    assert action["event_type"] == "feedback_action_taken"
    assert action["feedback_id"] == "fb-001"
    assert action["operator_id"] == OPERATOR_A
    assert action["bridge_event_written"] is False
    assert action["runtime_authority_granted"] is False
    assert action["canary_activation_applied"] is False
    assert action["gap_signal"]["queue_priority"] == "fast_track"
    assert action["gap_signal"]["queue_priority_only"] is True
    assert action["gap_signal"]["gate_skip_allowed"] is False
    assert action["gap_signal"]["runtime_authority_granted"] is False

    candidate = payload["scheduler_candidate_artifact"]
    assert candidate["queue_priority"] == "fast_track"
    assert candidate["fast_track_priority"] is True
    assert candidate["scheduler_enqueue_allowed"] is False
    assert candidate["scheduler_tick_allowed"] is False
    assert candidate["gate_skip_allowed"] is False
    authority = payload["authority_boundary"]
    assert authority["fast_track_queue_priority_requested"] is True
    assert authority["fast_track_is_queue_priority_only"] is True
    assert authority["bridge_event_written"] is False
    assert authority["scheduler_enqueue_allowed"] is False
    assert authority["scheduler_tick_allowed"] is False
    assert authority["gate_skip_allowed"] is False
    assert builder.validate_operator_feedback_action_bridge_event_template_report(
        report
    ) == []


def test_action_bridge_event_template_cli_json_is_path_free(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(_feedback(feedback_id="fb-001")),
    ])

    result = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-001",
        "--agent",
        "codex-tools-1",
        "--task-id",
        "operator-feedback-action-template-test",
        "--to",
        "operator,codex-lead-1,claude-rco-1",
        "--run-id",
        "codex-tools-1-20260606T120000Z",
        "--session-id",
        "codex-tools-1-20260606T120000Z",
        "--now",
        "2026-06-06T12:30:00Z",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    validate_event(report["bridge_event_template"])
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is True
    assert report["events_checked"] == 1
    assert report["bridge_event_written"] is False
    assert report["scheduler_enqueue_allowed"] is False
    assert report["runtime_authority_granted"] is False
    assert str(events_path) not in serialized
    assert "events.jsonl" not in serialized


def test_action_bridge_event_template_rate_limits_from_durable_log() -> None:
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

    report = _build_report(events, feedback_id="fb-limited")

    assert report["ok"] is True
    payload = report["bridge_event_template"]["payload"]
    assert payload["operator_fast_track_count"] == 10
    assert payload["global_fast_track_count"] == 10
    assert payload["feedback_action_taken"]["rate_limited"] is True
    assert payload["scheduler_candidate_artifact"]["queue_priority"] == "normal"
    assert payload["scheduler_candidate_artifact"]["fast_track_priority"] is False
    assert payload["fast_track_queue_priority_requested"] is False


def test_action_bridge_event_template_rejects_free_string_operator_id() -> None:
    report = _build_report([
        _bridge_event(
            _feedback(feedback_id="fb-free-string", operator_id="operator:jkh")
        ),
    ], feedback_id="fb-free-string")

    assert report["ok"] is False
    assert report["events_checked"] == 1
    assert report["bridge_event_written"] is False
    assert any("verified_bridge_identity" in item for item in report["blockers"])


def test_action_bridge_event_template_missing_feedback_preserves_event_count(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _bridge_event(_feedback(feedback_id="fb-present")),
    ])

    result = _run_cli(
        "--events",
        str(events_path),
        "--feedback-id",
        "fb-missing",
        "--agent",
        "codex-tools-1",
        "--task-id",
        "operator-feedback-action-template-test",
        "--json",
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert report["events_checked"] == 1
    assert "durable_bridge_log" in report["blockers"][0]
    assert str(events_path) not in serialized
    assert "events.jsonl" not in serialized


def test_action_bridge_event_template_rejects_invalid_target() -> None:
    report = builder.build_operator_feedback_action_bridge_event_template(
        feedback_id="fb-001",
        durable_bridge_events=[_bridge_event(_feedback(feedback_id="fb-001"))],
        agent_id="codex-tools-1",
        task_id="operator-feedback-action-template-test",
        to="operator,bad target",
    )

    assert report["ok"] is False
    assert any("to_unsafe" in item for item in report["blockers"])

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_operator_feedback_action_bridge_event_template as template_builder  # noqa: E402
import build_operator_feedback_action_bridge_event_template_index_entry as indexer  # noqa: E402


SCRIPT = (
    ROOT
    / "tools"
    / "build_operator_feedback_action_bridge_event_template_index_entry.py"
)
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


def _template_report(
    events: list[dict] | None = None,
    feedback_id: str = "fb-001",
) -> dict:
    durable_events = events or [_bridge_event(_feedback(feedback_id=feedback_id))]
    return template_builder.build_operator_feedback_action_bridge_event_template(
        feedback_id=feedback_id,
        durable_bridge_events=durable_events,
        agent_id="codex-tools-1",
        task_id="codex-tools-1/operator-feedback-action-template-test",
        to="operator,codex-lead-1,claude-rco-1",
        run_id="codex-tools-1-20260606T120000Z",
        session_id="codex-tools-1-20260606T120000Z",
        now_utc=indexer._parse_utc("2026-06-06T12:30:00Z"),
    )


def _encoded(report: dict) -> bytes:
    return json.dumps(report, sort_keys=True, allow_nan=False).encode("utf-8")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_index_entry_validates_template_and_no_authority() -> None:
    template_report = _template_report()

    entry = indexer.build_operator_feedback_action_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
        now_utc=indexer._parse_utc("2026-06-06T12:45:00Z"),
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == indexer.INDEX_ENTRY_VERSION
    assert entry["artifact_count"] == 1
    assert entry["template_only"] is True
    assert entry["manual_review_required"] is True
    assert entry["fast_track_queue_priority_requested"] is True
    assert entry["fast_track_is_queue_priority_only"] is True
    for field in indexer.AUTHORITY_FALSE_FIELDS:
        assert entry[field] is False

    template_index_entry = entry["template_index_entry"]
    assert template_index_entry["artifact_id"] == indexer.TEMPLATE_ARTIFACT_ID
    assert template_index_entry["event_status"] == template_builder.EVENT_STATUS
    assert template_index_entry["feedback_event_type"] == "feedback_action_taken"
    assert template_index_entry["feedback_id"] == "fb-001"
    assert template_index_entry["verified_operator_id"] == OPERATOR_A
    assert template_index_entry["rate_limit_source"] == "durable_bridge_log"
    assert template_index_entry["global_fast_track_per_hour_max"] == 30
    assert template_index_entry["queue_priority"] == "fast_track"
    assert template_index_entry["fast_track_grants_runtime_authority"] is False
    assert template_index_entry["scheduler_enqueue_allowed"] is False
    assert template_index_entry["gate_skip_allowed"] is False
    assert entry["operator_feedback_action"]["scheduler_enqueue_allowed"] is False
    assert entry["operator_feedback_action"]["gate_skip_allowed"] is False
    assert (
        indexer.validate_operator_feedback_action_bridge_event_template_index_entry(
            entry
        )
        == []
    )


def test_index_entry_cli_json_is_path_free(tmp_path: Path) -> None:
    template_report = _template_report()
    template_path = tmp_path / "template.json"
    template_path.write_bytes(_encoded(template_report))

    result = _run_cli(
        "--bridge-event-template-json",
        str(template_path),
        "--now",
        "2026-06-06T12:45:00Z",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)
    serialized = json.dumps(entry, sort_keys=True)
    assert entry["ok"] is True
    assert str(template_path) not in serialized
    assert "template.json" not in serialized
    assert "C:\\Python\\project2-master" not in serialized
    assert "artifact_payloads_included" in serialized
    assert entry["artifacts"][0]["raw_artifact_payload_included"] is False
    assert entry["artifacts"][0]["local_path_recorded"] is False


def test_index_entry_records_rate_limited_normal_priority() -> None:
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
    template_report = _template_report(events, feedback_id="fb-limited")

    entry = indexer.build_operator_feedback_action_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert entry["ok"] is True
    template_index_entry = entry["template_index_entry"]
    assert template_index_entry["queue_priority"] == "normal"
    assert template_index_entry["fast_track_queue_priority_requested"] is False
    assert template_index_entry["operator_fast_track_count"] == 10
    assert template_index_entry["global_fast_track_count"] == 10
    assert template_index_entry["global_fast_track_per_hour_max"] == 30
    assert template_index_entry["fast_track_grants_runtime_authority"] is False


def test_index_entry_rejects_scheduler_enqueue_tampering() -> None:
    template_report = _template_report()
    template_report["bridge_event_template"]["payload"][
        "scheduler_candidate_artifact"
    ]["scheduler_enqueue_allowed"] = True

    entry = indexer.build_operator_feedback_action_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert entry["ok"] is False
    assert entry["scheduler_enqueue_allowed"] is False
    assert any("scheduler_enqueue_allowed" in item for item in entry["blockers"])


def test_index_entry_rejects_unverified_operator_identity() -> None:
    template_report = _template_report()
    template_report["bridge_event_template"]["payload"][
        "verified_operator_id"
    ] = "operator:free-string"

    entry = indexer.build_operator_feedback_action_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert entry["ok"] is False
    assert any("operator_identity" in item for item in entry["blockers"])

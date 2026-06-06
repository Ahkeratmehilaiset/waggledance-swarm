import json
from pathlib import Path

from tools.verify_operator_feedback_wiring_guard import (
    main,
    verify_operator_feedback_wiring_guard,
)


UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"


def _event(
    *,
    payload: dict,
    line_agent: str = "operator",
    agent_uuid: str = UUID_A,
    ts_utc: str = "2026-06-06T01:00:00Z",
    session_id: str = "operator-session",
) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": line_agent,
        "type": "message",
        "task_id": "operator-feedback-fixture",
        "status": "ops_feedback_fixture",
        "severity": "",
        "to": "",
        "message": "",
        "paths": [],
        "write_scope": [],
        "run_id": "fixture-run",
        "role": "operator",
        "agent_uuid": agent_uuid,
        "session_id": session_id,
        "capabilities": ["ops_feedback"],
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": payload,
    }


def _feedback(
    *,
    feedback_id: str,
    operator_id: str = f"bridge:operator:{UUID_A}",
    priority: str = "high",
    submitted_at_utc: str = "2026-06-06T01:00:00Z",
) -> dict:
    return {
        "event_type": "ops_feedback",
        "feedback_id": feedback_id,
        "feedback_kind": "needs_solver",
        "query_class_hash": "a" * 64,
        "operator_id": operator_id,
        "priority": priority,
        "submitted_at_utc": submitted_at_utc,
    }


def _action(
    *,
    feedback_id: str,
    operator_id: str = f"bridge:operator:{UUID_A}",
    lane: str = "fast_track_canary",
    scheduled_for_utc: str = "2026-06-06T01:15:00Z",
    extra: dict | None = None,
) -> dict:
    payload = {
        "schema_version": "operator_feedback_action_plan.v1",
        "event_type": "feedback_action_taken",
        "feedback_id": feedback_id,
        "operator_id": operator_id,
        "lane": lane,
        "scheduled_for_utc": scheduled_for_utc,
        "queue_priority": "high" if lane == "fast_track_canary" else "normal",
        "runtime_authority_granted": False,
        "canary_activation_applied": False,
        "gap_signal": {
            "fast_track_canary": lane == "fast_track_canary",
            "runtime_authority_granted": False,
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def test_guard_accepts_verified_identity_durable_counts_and_queue_only_fast_track(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _event(payload=_feedback(feedback_id="fb-001")),
        _event(
            payload=_action(feedback_id="fb-001"),
            ts_utc="2026-06-06T01:15:00Z",
        ),
    ])

    report = verify_operator_feedback_wiring_guard(
        events_path,
        global_fast_track_per_hour_max=3,
    )

    assert report.ok is True
    assert report.durable_rate_limit_source == "bridge_event_log"
    assert report.operator_identity_ok is True
    assert report.durable_rate_limit_ok is True
    assert report.global_fast_track_cap_ok is True
    assert report.fast_track_gate_skip_ok is True
    assert report.ops_feedback_events == 1
    assert report.feedback_action_events == 1


def test_guard_ignores_unrelated_legacy_bridge_schema_lines(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    unrelated_legacy = {
        "ts_utc": "not-a-valid-bridge-timestamp",
        "payload": {"event_type": "wake_request"},
    }
    _write_events(events_path, [
        unrelated_legacy,
        _event(payload=_feedback(feedback_id="fb-001")),
    ])

    report = verify_operator_feedback_wiring_guard(events_path)

    assert report.ok is True
    assert report.checked_bridge_events == 1
    assert report.ops_feedback_events == 1
    assert report.issues == ()


def test_guard_rejects_free_string_operator_id(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _event(payload=_feedback(
            feedback_id="fb-001",
            operator_id="operator:jkh",
        )),
    ])

    report = verify_operator_feedback_wiring_guard(events_path)

    assert report.ok is False
    assert report.operator_identity_ok is False
    assert [issue.code for issue in report.issues] == [
        "operator_id_not_verified_bridge_identity",
    ]


def test_guard_rejects_per_operator_cap_excess_from_durable_log(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events: list[dict] = []
    for index in range(3):
        minute = index * 5
        feedback_id = f"fb-{index:03d}"
        events.append(_event(
            payload=_feedback(
                feedback_id=feedback_id,
                submitted_at_utc=f"2026-06-06T01:{minute:02d}:00Z",
            ),
            ts_utc=f"2026-06-06T01:{minute:02d}:00Z",
        ))
        events.append(_event(
            payload=_action(
                feedback_id=feedback_id,
                scheduled_for_utc=f"2026-06-06T01:{minute + 1:02d}:00Z",
            ),
            ts_utc=f"2026-06-06T01:{minute + 1:02d}:00Z",
        ))
    _write_events(events_path, events)

    report = verify_operator_feedback_wiring_guard(
        events_path,
        per_operator_fast_track_per_hour_max=2,
        global_fast_track_per_hour_max=10,
    )

    assert report.ok is False
    assert report.durable_rate_limit_ok is False
    assert any(
        issue.code == "per_operator_fast_track_cap_exceeded"
        and issue.feedback_id == "fb-002"
        for issue in report.issues
    )


def test_guard_rejects_global_fast_track_cap(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events = [
        _event(payload=_feedback(feedback_id="fb-a")),
        _event(
            payload=_action(feedback_id="fb-a"),
            ts_utc="2026-06-06T01:01:00Z",
        ),
        _event(
            payload=_feedback(
                feedback_id="fb-b",
                operator_id=f"bridge:operator:{UUID_B}",
                submitted_at_utc="2026-06-06T01:02:00Z",
            ),
            agent_uuid=UUID_B,
            ts_utc="2026-06-06T01:02:00Z",
        ),
        _event(
            payload=_action(
                feedback_id="fb-b",
                operator_id=f"bridge:operator:{UUID_B}",
                scheduled_for_utc="2026-06-06T01:03:00Z",
            ),
            agent_uuid=UUID_B,
            ts_utc="2026-06-06T01:03:00Z",
        ),
    ]
    _write_events(events_path, events)

    report = verify_operator_feedback_wiring_guard(
        events_path,
        global_fast_track_per_hour_max=1,
    )

    assert report.ok is False
    assert report.global_fast_track_cap_ok is False
    assert any(issue.code == "global_fast_track_cap_exceeded" for issue in report.issues)


def test_guard_rejects_fast_track_gate_skip_flags(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _event(payload=_feedback(feedback_id="fb-001")),
        _event(
            payload=_action(
                feedback_id="fb-001",
                extra={
                    "adversarial_gate_skipped": True,
                    "gate_decision": "skip",
                },
            ),
            ts_utc="2026-06-06T01:15:00Z",
        ),
    ])

    report = verify_operator_feedback_wiring_guard(events_path)

    assert report.ok is False
    assert report.fast_track_gate_skip_ok is False
    assert {issue.code for issue in report.issues} >= {
        "fast_track_authority_grant",
        "fast_track_gate_skip",
    }


def test_cli_prints_json_and_returns_one_on_violation(
    tmp_path: Path,
    capsys,
) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        _event(payload=_feedback(
            feedback_id="fb-001",
            operator_id="operator:jkh",
        )),
    ])

    exit_code = main(["--events", str(events_path)])
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert out["ok"] is False
    assert out["issues"][0]["code"] == "operator_id_not_verified_bridge_identity"

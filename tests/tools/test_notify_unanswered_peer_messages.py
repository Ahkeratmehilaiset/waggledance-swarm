from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.notify_unanswered_peer_messages import (
    PeerNotificationError,
    main,
    surface_unanswered_peer_messages,
)


NOW = "2026-05-21T18:30:00Z"


def _status_query(
    *,
    ts_utc: str = "2026-05-21T18:13:42.0000000Z",
    task_id: str = "status-query-taxonomy",
) -> dict[str, object]:
    return {
        "ts_utc": ts_utc,
        "agent": "codex",
        "to": "claude",
        "type": "message",
        "task_id": task_id,
        "status": "status_query",
        "message": "what are you doing?",
    }


def _peer_event(
    *,
    agent: str = "claude",
    event_type: str = "message",
    status: str = "answered",
    ts_utc: str = "2026-05-21T18:16:04.0000000Z",
    task_id: str = "status-query-taxonomy",
) -> dict[str, object]:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "to": "codex",
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "message": "peer event",
    }


def test_dry_run_surfaces_unanswered_status_query_without_writing(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "inbox" / "claude"
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            {
                "ts_utc": "2026-05-21T18:13:42Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "claude-status-query-2026-05-21",
                "status": "status_query",
                "message": "what are you doing now?",
            }
        ],
        out_dir=out_dir,
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1
    assert report["markers"][0]["task_id"] == "claude-status-query-2026-05-21"
    assert not out_dir.exists()


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("ownership_proposal", "open"),
        ("solver_gap", "architecture_proposal"),
        ("review_thread", "request"),
    ],
)
def test_dry_run_surfaces_directed_polymorphic_open_event(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    request = _status_query(task_id="polymorphic-open-request")
    request.update({"type": event_type, "status": status})

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[request],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1
    assert report["markers"][0]["task_id"] == "polymorphic-open-request"


def test_apply_writes_idempotent_marker_for_fresh_rco_request(tmp_path: Path) -> None:
    out_dir = tmp_path / "inbox" / "claude"
    events = [
        {
            "ts_utc": "2026-05-21T18:00:42Z",
            "agent": "codex",
            "to": "claude,operator",
            "type": "handoff",
            "task_id": "pr537-review",
            "status": "rco_requested",
            "message": "please RCO review PR #537",
        }
    ]

    first = surface_unanswered_peer_messages(
        agent="claude",
        events=events,
        out_dir=out_dir,
        now_utc=_now(),
        apply=True,
    )
    second = surface_unanswered_peer_messages(
        agent="claude",
        events=events,
        out_dir=out_dir,
        now_utc=_now(),
        apply=True,
    )

    marker_path = Path(first["markers"][0]["path"])
    assert first["marker_count"] == 1
    assert second["markers"][0]["path"] == str(marker_path)
    assert marker_path.exists()
    content = marker_path.read_text(encoding="utf-8")
    assert "task_id: pr537-review" in content
    assert "please RCO review PR #537" in content


def test_answered_request_does_not_create_marker(tmp_path: Path) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            {
                "ts_utc": "2026-05-21T18:13:42Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "status-query",
                "status": "status_query",
                "message": "what are you doing?",
            },
            {
                "ts_utc": "2026-05-21T18:16:04Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": "status-query",
                "status": "late_status_response",
                "message": "answer",
            },
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=True,
    )

    assert report["marker_count"] == 0
    assert not (tmp_path / "inbox" / "claude").exists()


def test_passive_ack_message_does_not_clear_peer_marker(tmp_path: Path) -> None:
    for status in ("acknowledged", "received", "seen"):
        report = surface_unanswered_peer_messages(
            agent="claude",
            events=[
                {
                    "ts_utc": "2026-05-21T18:13:42Z",
                    "agent": "codex",
                    "to": "claude",
                    "type": "message",
                    "task_id": f"status-query-{status}",
                    "status": "status_query",
                    "message": "what are you doing?",
                },
                {
                    "ts_utc": "2026-05-21T18:16:04Z",
                    "agent": "claude",
                    "to": "codex",
                    "type": "message",
                    "task_id": f"status-query-{status}",
                    "status": status,
                    "message": f"{status} message/status_query from codex",
                },
            ],
            out_dir=tmp_path / "inbox" / "claude",
            now_utc=_now(),
            apply=False,
        )

        assert report["marker_count"] == 1
        assert report["markers"][0]["task_id"] == f"status-query-{status}"


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("message", "ack"),
        ("done", "ack"),
        ("message", "wake_ack"),
        ("message", "wake_acknowledged"),
        ("message", "wake_ack_corrected_review_already_posted"),
        ("message", "received_with_context"),
        ("done", "done_received"),
    ],
)
def test_ack_status_token_does_not_clear_peer_marker(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[_status_query(), _peer_event(event_type=event_type, status=status)],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1


@pytest.mark.parametrize(
    "status",
    [
        "status_query_ack",
        "ack_status_query",
        "status_query_received_with_context",
        "wake_ack_status_query",
        "status_query_acknowledged",
        "seen_status_query",
    ],
)
def test_ack_status_token_does_not_enter_peer_request_fallback(
    tmp_path: Path,
    status: str,
) -> None:
    request = _status_query()
    request["status"] = status

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[request],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 0


@pytest.mark.parametrize(
    "status",
    [
        "status_query_unacknowledged",
        "status_query_foreseen",
        "status_query_prereceived",
    ],
)
def test_ack_substring_lookalike_remains_peer_request(
    tmp_path: Path,
    status: str,
) -> None:
    request = _status_query()
    request["status"] = status

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[request],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1


@pytest.mark.parametrize("event_type", ["done", "decision"])
def test_target_follow_up_request_does_not_clear_peer_marker(
    tmp_path: Path,
    event_type: str,
) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            _status_query(),
            _peer_event(event_type=event_type, status="request"),
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1


def test_third_party_done_merged_does_not_clear_peer_marker(tmp_path: Path) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            _status_query(),
            _peer_event(agent="fable", event_type="done", status="merged"),
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1


@pytest.mark.parametrize(
    ("request_identity", "closure_identity", "expected_markers"),
    [
        ({}, {}, 0),
        (
            {"agent_uuid": "11111111-2222-3333-4444-555555555555", "session_id": "current"},
            {"agent_uuid": "11111111-2222-3333-4444-555555555555", "session_id": "current"},
            0,
        ),
        (
            {"agent_uuid": "11111111-2222-3333-4444-555555555555", "session_id": "current"},
            {"agent_uuid": "11111111-2222-3333-4444-555555555555", "session_id": "stale"},
            1,
        ),
        (
            {"agent_uuid": "11111111-2222-3333-4444-555555555555", "session_id": "current"},
            {},
            1,
        ),
    ],
)
def test_requester_terminal_closure_binds_identity(
    tmp_path: Path,
    request_identity: dict[str, str],
    closure_identity: dict[str, str],
    expected_markers: int,
) -> None:
    request = _status_query()
    request.update(request_identity)
    closure = _peer_event(agent="codex", event_type="status", status="superseded")
    closure.update(closure_identity)

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[request, closure],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == expected_markers


def test_equal_timestamp_target_answer_uses_append_order(tmp_path: Path) -> None:
    timestamp = "2026-05-21T18:13:42.0000001Z"
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            _status_query(ts_utc=timestamp),
            _peer_event(event_type="decision", status="answered", ts_utc=timestamp),
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 0


def test_mixed_precision_later_request_reopens_task(tmp_path: Path) -> None:
    first = _status_query(ts_utc="2026-05-21T18:13:42.0000000Z")
    answer = _peer_event(
        event_type="decision",
        status="answered",
        ts_utc="2026-05-21T18:14:00Z",
    )
    reopened = _status_query(ts_utc="2026-05-21T18:14:00.1000000Z")
    reopened["message"] = "new status query"

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[first, answer, reopened],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1
    assert report["markers"][0]["ts_utc"] == reopened["ts_utc"]


def test_invalid_answer_timestamp_fails_closed(tmp_path: Path) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            _status_query(),
            _peer_event(event_type="decision", status="answered", ts_utc="invalid"),
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1


def test_serialized_append_index_cannot_spoof_equal_timestamp_order(
    tmp_path: Path,
) -> None:
    timestamp = "2026-05-21T18:13:42.0000001Z"
    answer = _peer_event(
        event_type="decision",
        status="answered",
        ts_utc=timestamp,
    )
    answer["_bridge_append_index"] = 999
    request = _status_query(ts_utc=timestamp)
    request["_bridge_append_index"] = 0

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[answer, request],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1


def test_originator_done_closes_obsolete_rco_request(tmp_path: Path) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            {
                "ts_utc": "2026-05-21T18:00:42Z",
                "agent": "codex",
                "to": "claude",
                "type": "handoff",
                "task_id": "pr537-review",
                "status": "rco_requested",
                "message": "please review PR #537",
            },
            {
                "ts_utc": "2026-05-21T18:11:45Z",
                "agent": "codex",
                "to": "claude,operator",
                "type": "done",
                "task_id": "pr537-review",
                "status": "merged_postmerge_validated",
                "message": "PR #537 merged and validated",
            },
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=True,
    )

    assert report["marker_count"] == 0
    assert not (tmp_path / "inbox" / "claude").exists()


def test_self_addressed_request_is_not_surfaced_as_peer_marker(
    tmp_path: Path,
) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            {
                "ts_utc": "2026-05-21T18:13:42Z",
                "agent": "claude",
                "to": "claude,codex",
                "type": "message",
                "task_id": "self-query",
                "status": "status_query",
                "message": "do not notify myself",
            }
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=True,
    )

    assert report["marker_count"] == 0
    assert not (tmp_path / "inbox" / "claude").exists()


def test_marker_paths_are_collision_safe_after_sanitizing(tmp_path: Path) -> None:
    out_dir = tmp_path / "inbox" / "claude"
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            {
                "ts_utc": "2026-05-21T18:13:42Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "same?task",
                "status": "status_query",
                "message": "first",
            },
            {
                "ts_utc": "2026-05-21T18:14:42Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "same/task",
                "status": "status_query",
                "message": "second",
            },
        ],
        out_dir=out_dir,
        now_utc=_now(),
        apply=True,
    )

    paths = [Path(marker["path"]) for marker in report["markers"]]
    assert report["marker_count"] == 2
    assert len(set(paths)) == 2
    assert all(path.exists() for path in paths)


def test_stale_request_is_reported_but_not_written_by_default(tmp_path: Path) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            {
                "ts_utc": "2026-05-20T00:00:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "old-query",
                "status": "status_query",
                "message": "old",
            }
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=True,
    )

    assert report["marker_count"] == 0
    assert report["stale_request_count"] == 1


def test_private_marker_refuses_to_write(tmp_path: Path) -> None:
    out_dir = tmp_path / "inbox" / "claude"

    try:
        surface_unanswered_peer_messages(
            agent="claude",
            events=[
                {
                    "ts_utc": "2026-05-21T18:13:42Z",
                    "agent": "codex",
                    "to": "claude",
                    "type": "message",
                    "task_id": "private-query",
                    "status": "status_query",
                    "message": "PRIVATE_MARKER must not be copied",
                }
            ],
            out_dir=out_dir,
            now_utc=_now(),
            apply=True,
        )
    except PeerNotificationError as exc:
        assert exc.report["decision"] == "notify_unanswered_peer_messages_refused"
    else:
        raise AssertionError("private marker should fail closed")

    assert not out_dir.exists()


def test_cli_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    bridge = tmp_path / "runtime" / ".agent-bridge"
    events_path = bridge / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        json.dumps(
            {
                "ts_utc": "2026-05-21T18:13:42Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "runtime-root-status-query",
                "status": "status_query",
                "message": "runtime root default should be used",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge))

    exit_code = main(["--agent", "claude", "--apply", "--now", NOW, "--json"])

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["marker_count"] == 1
    marker_path = Path(report["markers"][0]["path"])
    assert marker_path.parent == bridge / "inbox" / "claude"
    assert marker_path.exists()


def test_cli_fails_closed_on_non_object_selected_event(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = bridge / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text("[]\n", encoding="utf-8")

    exit_code = main(
        [
            "--agent",
            "claude",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--json",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "notify_unanswered_peer_messages_error"
    assert "JSON object" in report["errors"][0]


def _now():
    from tools.bridge_next_action import _parse_utc

    parsed = _parse_utc(NOW)
    assert parsed is not None
    return parsed

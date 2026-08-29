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


@pytest.mark.parametrize("answer_ts", ["2099-05-21T18:16:04Z", "not-a-timestamp"])
def test_prior_answer_does_not_close_later_appended_request(
    tmp_path: Path,
    answer_ts: str,
) -> None:
    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[
            {
                "ts_utc": answer_ts,
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": "append-order-status-query",
                "status": "late_status_response",
                "message": "answer was appended before the request",
            },
            {
                "ts_utc": "2026-05-21T18:13:42Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "append-order-status-query",
                "status": "status_query",
                "message": "what are you doing?",
            },
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1
    assert not (tmp_path / "inbox" / "claude").exists()


@pytest.mark.parametrize("progress_ts", ["2099-05-21T18:16:04Z", "not-a-timestamp"])
def test_prior_idle_progress_does_not_close_later_peer_marker(
    tmp_path: Path,
    progress_ts: str,
) -> None:
    progress = {
        "ts_utc": progress_ts,
        "agent": "claude",
        "to": "codex",
        "type": "message",
        "task_id": "idle-progress",
        "status": "idle_counter_proposal",
        "message": "progress appended before the proposal",
        "payload": {
            "protocol_version": "idle-protocol.v1",
            "proposal_id": "counter-1",
            "responds_to": "proposal-1",
        },
    }
    proposal = {
        "ts_utc": "2026-05-21T18:13:42Z",
        "agent": "codex",
        "to": "claude",
        "type": "message",
        "task_id": "idle-proposal",
        "status": "idle_proposal",
        "message": "proposal appended later",
        "payload": {
            "protocol_version": "idle-protocol.v1",
            "proposal_id": "proposal-1",
        },
    }

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[progress, proposal],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1
    assert not (tmp_path / "inbox" / "claude").exists()


@pytest.mark.parametrize("progress_ts", ["2026-05-21T18:20:00Z", "not-a-timestamp"])
def test_later_idle_progress_closes_peer_marker_by_append_order(
    tmp_path: Path,
    progress_ts: str,
) -> None:
    proposal = {
        "ts_utc": "2026-05-21T18:13:42Z",
        "agent": "codex",
        "to": "claude",
        "type": "message",
        "task_id": "idle-proposal",
        "status": "idle_proposal",
        "message": "proposal",
        "payload": {
            "protocol_version": "idle-protocol.v1",
            "proposal_id": "proposal-1",
        },
    }
    progress = {
        "ts_utc": progress_ts,
        "agent": "claude",
        "to": "codex",
        "type": "message",
        "task_id": "idle-progress",
        "status": "idle_counter_proposal",
        "message": "progress appended later",
        "payload": {
            "protocol_version": "idle-protocol.v1",
            "proposal_id": "counter-1",
            "responds_to": "proposal-1",
        },
    }

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[proposal, progress],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 0
    assert not (tmp_path / "inbox" / "claude").exists()


def test_tail_replayed_stale_idle_progress_keeps_peer_marker(
    tmp_path: Path,
) -> None:
    proposal = {
        "ts_utc": "2026-05-21T18:13:42Z",
        "agent": "codex",
        "to": "claude",
        "type": "message",
        "task_id": "idle-proposal",
        "status": "idle_proposal",
        "message": "proposal",
        "payload": {
            "protocol_version": "idle-protocol.v1",
            "proposal_id": "proposal-1",
        },
    }
    replayed_progress = {
        "ts_utc": "2026-05-21T18:00:00Z",
        "agent": "claude",
        "to": "codex",
        "type": "message",
        "task_id": "idle-progress",
        "status": "idle_counter_proposal",
        "message": "stale progress replayed onto the log tail",
        "payload": {
            "protocol_version": "idle-protocol.v1",
            "proposal_id": "counter-1",
            "responds_to": "proposal-1",
        },
    }

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[proposal, replayed_progress],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1


def test_delayed_replay_answer_does_not_clear_renewed_peer_request(
    tmp_path: Path,
) -> None:
    request = {
        "ts_utc": "2026-05-21T18:00:00Z",
        "agent": "codex",
        "to": "claude",
        "type": "message",
        "task_id": "replay-renewal-peer",
        "status": "status_query",
        "message": "first ask",
    }
    renewal = {
        "ts_utc": "2026-05-21T18:02:00Z",
        "agent": "codex",
        "to": "claude",
        "type": "message",
        "task_id": "replay-renewal-peer",
        "status": "status_query",
        "message": "asked again after the first answer",
    }
    replayed_answer = {
        "ts_utc": "2026-05-21T18:01:00Z",
        "agent": "claude",
        "to": "codex",
        "type": "message",
        "task_id": "replay-renewal-peer",
        "status": "status_report",
        "message": "stale answer replayed onto the log tail",
    }

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[request, renewal, replayed_answer],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 1
    assert report["markers"][0]["task_id"] == "replay-renewal-peer"

    settled = surface_unanswered_peer_messages(
        agent="claude",
        events=[request, replayed_answer],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert settled["marker_count"] == 0


def test_requester_resolved_decision_clears_peer_marker(tmp_path: Path) -> None:
    request = {
        "ts_utc": "2026-05-21T18:00:00Z",
        "agent": "codex",
        "to": "claude",
        "type": "message",
        "task_id": "resolved-parity-peer",
        "status": "status_query",
        "message": "please advise",
    }
    resolved = {
        "ts_utc": "2026-05-21T18:05:00Z",
        "agent": "codex",
        "to": "claude,operator",
        "type": "decision",
        "task_id": "resolved-parity-peer",
        "status": "resolved",
        "message": "requester resolved its own request",
    }

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[request, resolved],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == 0


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
                "status": "postmerge_validated",
                "message": "PR #537 merged and validated",
            },
        ],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=True,
    )

    assert report["marker_count"] == 0
    assert not (tmp_path / "inbox" / "claude").exists()


@pytest.mark.parametrize(
    ("closer_agent", "closer_session", "expected_markers"),
    [
        ("merge-driver", "session-a", 1),
        ("codex", "stale-session", 1),
        ("codex", "session-a", 0),
    ],
)
def test_terminal_receipt_closes_only_matching_requester_identity(
    tmp_path: Path,
    closer_agent: str,
    closer_session: str,
    expected_markers: int,
) -> None:
    request = {
        "ts_utc": "2026-05-21T18:00:42Z",
        "agent": "codex",
        "agent_uuid": "uuid-a",
        "session_id": "session-a",
        "to": "claude",
        "type": "handoff",
        "task_id": "identity-bound-review",
        "status": "rco_requested",
        "message": "please review",
    }
    receipt = {
        "ts_utc": "2026-05-21T18:11:45Z",
        "agent": closer_agent,
        "agent_uuid": "uuid-a",
        "session_id": closer_session,
        "to": "operator",
        "type": "decision",
        "task_id": "identity-bound-review",
        "status": "autonomous_merge_receipt",
        "message": "receipt",
    }

    report = surface_unanswered_peer_messages(
        agent="claude",
        events=[request, receipt],
        out_dir=tmp_path / "inbox" / "claude",
        now_utc=_now(),
        apply=False,
    )

    assert report["marker_count"] == expected_markers


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

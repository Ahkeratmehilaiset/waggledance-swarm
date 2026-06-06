from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.bridge_next_action import (
    BridgeNextActionError,
    main,
    read_events,
    recommend_next_action,
)
from waggledance.core.work_queue import Claim, claim_task


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    events_path = path / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_recommends_continuing_own_claim_before_incoming_request(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="codex",
        task_id="owned-task",
        summary="already started",
        mode="write",
        write_scope=["tools/x.py"],
        bridge_root=bridge,
    )
    events = [
        {
            "ts_utc": "2026-05-18T10:00:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "incoming-task",
            "status": "request",
            "message": "please review",
        }
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[claim])

    assert report["action"] == "continue_claim"
    assert report["task_id"] == "owned-task"
    assert report["safe_mode"] == "write"
    assert report["open_incoming_count"] == 1


def test_recommends_answering_latest_unanswered_incoming_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:00:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "decision",
            "task_id": "old-task",
            "status": "request_changes",
            "message": "old request",
        },
        {
            "ts_utc": "2026-05-18T10:05:00Z",
            "agent": "codex",
            "type": "decision",
            "task_id": "old-task",
            "status": "pass",
            "message": "answered",
        },
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "new-task",
            "status": "request",
            "message": "new request",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "new-task"
    assert report["safe_mode"] == "read-only"
    assert report["incoming"]["agent"] == "claude"
    assert report["open_incoming_count"] == 1
    assert report["stale_incoming_count"] == 0


def test_ignores_stale_incoming_request_when_bridge_has_moved_on() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "old-task",
            "status": "request",
            "message": "old request",
        },
        {
            "ts_utc": "2026-05-20T10:30:00Z",
            "agent": "claude",
            "type": "heartbeat",
            "task_id": "heartbeat",
            "status": "active",
            "message": "bridge moved on",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1
    assert report["stale_incoming_task_ids"] == ["old-task"]


def test_ignores_stale_incoming_request_with_powershell_fractional_timestamp() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00.1234567Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "old-task",
            "status": "request",
            "message": "old request",
        },
        {
            "ts_utc": "2026-05-20T10:30:00.7654321Z",
            "agent": "claude",
            "type": "heartbeat",
            "task_id": "heartbeat",
            "status": "active",
            "message": "bridge moved on",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1
    assert report["stale_incoming_task_ids"] == ["old-task"]


def test_deduplicates_stale_incoming_task_ids_but_preserves_event_count() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "repeat-task",
            "status": "request",
            "message": "old request",
        },
        {
            "ts_utc": "2026-05-18T10:11:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "repeat-task",
            "status": "request",
            "message": "same task repeated",
        },
        {
            "ts_utc": "2026-05-20T10:30:00Z",
            "agent": "claude",
            "type": "heartbeat",
            "task_id": "heartbeat",
            "status": "active",
            "message": "bridge moved on",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["stale_incoming_count"] == 1
    assert report["stale_incoming_task_ids"] == ["repeat-task"]
    assert report["stale_incoming_event_count"] == 2


def test_archives_very_old_stale_incoming_without_changing_next_action() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "historical-task",
            "status": "request",
            "message": "old request",
        }
    ]

    report = recommend_next_action(
        agent="codex",
        events=events,
        claims=[],
        now_utc=datetime(2026, 5, 22, 10, 30, tzinfo=timezone.utc),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0
    assert "stale_incoming_task_ids" not in report
    assert report["archived_stale_incoming_count"] == 1
    assert report["archived_stale_incoming_task_ids"] == ["historical-task"]


def test_cli_rejects_non_finite_stale_report_max_age(
    tmp_path: Path, capsys
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-18T10:10:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": "old-task",
                "status": "request",
                "message": "old request",
            }
        ],
    )

    for value in ("nan", "inf"):
        exit_code = main(
            [
                "--agent",
                "codex",
                "--bridge-root",
                str(bridge),
                "--events",
                str(events_path),
                "--stale-report-max-age-hours",
                value,
                "--json",
            ]
        )

        assert exit_code == 2
        report = json.loads(capsys.readouterr().out)
        assert report["decision"] == "bridge_next_action_error"
        assert report["errors"] == ["stale_report_max_age_hours must be positive"]


def test_fresh_incoming_request_wins_over_stale_backlog() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "old-task",
            "status": "request",
            "message": "old request",
        },
        {
            "ts_utc": "2026-05-20T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "fresh-task",
            "status": "request",
            "message": "fresh request",
        },
        {
            "ts_utc": "2026-05-20T10:30:00Z",
            "agent": "claude",
            "type": "heartbeat",
            "task_id": "heartbeat",
            "status": "active",
            "message": "bridge still active",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "fresh-task"
    assert report["open_incoming_count"] == 1
    assert report["stale_incoming_count"] == 1


def test_rco_requested_status_is_open_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "rco-task",
            "status": "rco_requested",
            "message": "please review",
        }
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "rco-task"


def test_done_postmerge_validated_closes_incoming_handoff() -> None:
    events = [
        {
            "ts_utc": "2026-05-21T11:56:07Z",
            "agent": "claude",
            "to": "codex",
            "type": "handoff",
            "task_id": "idle-convergence-precedence-over-invalid-2026-05-21",
            "status": "rco_requested",
            "message": "please review PR #524",
        },
        {
            "ts_utc": "2026-05-21T12:09:22Z",
            "agent": "codex",
            "type": "done",
            "task_id": "idle-convergence-precedence-over-invalid-2026-05-21",
            "status": "postmerge_validated",
            "message": "PR #524 merged to main and postmerge validated.",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_done_verified_closes_incoming_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "smoke-request",
            "status": "request",
            "message": "please run the smoke check",
        },
        {
            "ts_utc": "2026-05-18T10:12:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": "smoke-request",
            "status": "verified",
            "message": "smoke check passed",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_done_with_domain_status_closes_incoming_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-25T06:53:01Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1,claude-rco-1",
            "type": "message",
            "task_id": "gpu-runtime-handoff-2026-05-25",
            "status": "request",
            "message": "record this runtime handoff",
        },
        {
            "ts_utc": "2026-05-25T06:56:40Z",
            "agent": "codex-tools-1",
            "to": "codex-lead-1,claude-rco-1,operator",
            "type": "done",
            "task_id": "gpu-runtime-handoff-2026-05-25",
            "status": "gpu_runtime_noted",
            "message": "GPU runtime note recorded in memory",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


@pytest.mark.parametrize("status", ["acknowledged", "received", "seen"])
def test_ack_message_statuses_do_not_close_incoming_request(status: str) -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "ack-request",
            "status": "request",
            "message": "please acknowledge",
        },
        {
            "ts_utc": "2026-05-18T10:12:00Z",
            "agent": "codex",
            "to": "claude",
            "type": "message",
            "task_id": "ack-request",
            "status": status,
            "message": f"{status} message/request from claude",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "ack-request"
    assert report["open_incoming_count"] == 1
    assert report["stale_incoming_count"] == 0


def test_requester_done_close_closes_incoming_request_for_target() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "finding",
            "task_id": "requester-closed-task",
            "status": "open",
            "message": "please inspect this finding",
        },
        {
            "ts_utc": "2026-05-18T10:12:00Z",
            "agent": "claude",
            "to": "codex,operator",
            "type": "done",
            "task_id": "requester-closed-task",
            "status": "closed_current_main_reconciled",
            "message": "requester closeout after current main reconciled it",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_comma_separated_recipient_is_incoming_for_target_agent() -> None:
    events = [
        {
            "ts_utc": "2026-05-22T08:52:04Z",
            "agent": "claude",
            "to": "codex,operator",
            "type": "handoff",
            "task_id": "multi-target-help",
            "status": "help_requested",
            "message": "codex and operator should see this",
        }
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "multi-target-help"
    assert report["open_incoming_count"] == 1


def test_reported_handoff_closes_round_two_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-22T03:43:51Z",
            "agent": "claude",
            "to": "codex",
            "type": "handoff",
            "task_id": "idle-protocol-d4",
            "status": "round_2_requested",
            "message": "please answer round 2",
        },
        {
            "ts_utc": "2026-05-22T08:55:06Z",
            "agent": "codex",
            "to": "claude,operator",
            "type": "handoff",
            "task_id": "idle-protocol-d4",
            "status": "round_2_reported",
            "message": "round 2 reported",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_approved_decision_closes_review_requested_handoff() -> None:
    events = [
        {
            "ts_utc": "2026-05-22T09:19:29Z",
            "agent": "claude",
            "to": "codex",
            "type": "handoff",
            "task_id": "bridge-loop-root-parity-fixes-2026-05-22",
            "status": "review_requested",
            "message": "requesting RCO",
        },
        {
            "ts_utc": "2026-05-22T09:30:53Z",
            "agent": "codex",
            "to": "claude",
            "type": "decision",
            "task_id": "bridge-loop-root-parity-fixes-2026-05-22",
            "status": "approved",
            "message": "RCO approved",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_review_feedback_block_closes_rco_requested_handoff() -> None:
    events = [
        {
            "ts_utc": "2026-05-23T07:17:19Z",
            "agent": "claude",
            "to": "codex",
            "type": "handoff",
            "task_id": "rival-axis-hardening-pr-607",
            "status": "rco_requested",
            "message": "please RCO PR #607",
        },
        {
            "ts_utc": "2026-05-23T07:21:20Z",
            "agent": "codex",
            "to": "claude,operator",
            "type": "finding",
            "task_id": "rival-axis-hardening-pr-607",
            "status": "review_feedback_block",
            "message": "RCO BLOCK with required fix",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_rco_block_decision_closes_rco_requested_handoff() -> None:
    events = [
        {
            "ts_utc": "2026-05-23T07:17:19Z",
            "agent": "claude",
            "to": "codex",
            "type": "handoff",
            "task_id": "rival-axis-hardening-pr-607",
            "status": "rco_requested",
            "message": "please RCO PR #607",
        },
        {
            "ts_utc": "2026-05-23T07:21:20Z",
            "agent": "codex",
            "to": "claude",
            "type": "decision",
            "task_id": "rival-axis-hardening-pr-607",
            "status": "rco_block",
            "message": "RCO BLOCK with required fix",
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_ack_status_with_already_substring_is_not_open_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-17T08:27:29Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "wake-ack-task",
            "status": "wake_ack_corrected_rco_pass_already_posted_clear_to_merge",
            "message": "already answered",
        }
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_recommends_parallel_read_only_when_foreign_write_claim_exists(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="claude",
        task_id="foreign-write",
        summary="other agent writes",
        mode="write",
        write_scope=["waggledance/core/work_queue.py"],
        bridge_root=bridge,
    )

    report = recommend_next_action(agent="codex", events=[], claims=[claim])

    assert report["action"] == "parallel_read_only"
    assert report["task_id"] == "bridge-review-or-scout"
    assert report["safe_mode"] == "read-only"
    assert report["foreign_write_claim_count"] == 1


def test_report_surfaces_agent_profile_and_claim_role_metadata() -> None:
    claim = Claim(
        agent="claude-rco-1",
        task_id="foreign-write",
        summary="other agent writes",
        mode="write",
        write_scope=("tools/bridge_next_action.py",),
        run_id="claude-run",
        claimed_at_utc="2026-05-23T15:00:00Z",
        last_heartbeat_utc="2026-05-23T15:05:00Z",
        lease_seconds=900,
        claim_lease_expires_utc="2026-05-23T15:20:00Z",
        role="rco",
        agent_uuid="11111111-2222-3333-4444-555555555555",
        capabilities=("bridge_review", "rco"),
    )
    events = [
        {
            "ts_utc": "2026-05-23T15:00:00Z",
            "agent": "codex-impl-1",
            "type": "heartbeat",
            "task_id": "heartbeat",
            "status": "active",
            "message": "background heartbeat",
            "role": "impl",
            "agent_uuid": "22222222-3333-4444-5555-666666666666",
            "session_id": "codex-session",
            "capabilities": ["bridge_event", "work_queue"],
        }
    ]

    report = recommend_next_action(
        agent="codex-impl-1",
        events=events,
        claims=[claim],
    )

    assert report["action"] == "parallel_read_only"
    assert report["agent_profile"] == {
        "role": "impl",
        "agent_uuid": "22222222-3333-4444-5555-666666666666",
        "session_id": "codex-session",
        "capabilities": ["bridge_event", "work_queue"],
    }
    assert report["claim_snapshot"]["own"] == []
    foreign = report["claim_snapshot"]["foreign_write"][0]
    assert foreign["agent"] == "claude-rco-1"
    assert foreign["role"] == "rco"
    assert foreign["agent_uuid"] == "11111111-2222-3333-4444-555555555555"
    assert foreign["capabilities"] == ["bridge_review", "rco"]
    assert foreign["claim_lease_expires_utc"] == "2026-05-23T15:20:00Z"


def test_incoming_report_surfaces_requester_role_metadata() -> None:
    events = [
        {
            "ts_utc": "2026-05-23T15:00:00Z",
            "agent": "claude-rco-1",
            "to": "codex-impl-1",
            "type": "message",
            "task_id": "review-request",
            "status": "request",
            "message": "please review",
            "role": "rco",
            "agent_uuid": "11111111-2222-3333-4444-555555555555",
            "capabilities": ["code_review", "rco"],
        }
    ]

    report = recommend_next_action(
        agent="codex-impl-1",
        events=events,
        claims=[],
    )

    assert report["action"] == "answer_incoming"
    assert report["incoming"]["agent"] == "claude-rco-1"
    assert report["incoming"]["role"] == "rco"
    assert report["incoming"]["agent_uuid"] == "11111111-2222-3333-4444-555555555555"
    assert report["incoming"]["capabilities"] == ["code_review", "rco"]


def test_recommends_claiming_unblocked_work_when_bridge_is_clear() -> None:
    report = recommend_next_action(agent="codex", events=[], claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["task_id"] == "next-unclaimed-scout-or-implementation"
    assert report["safe_mode"] == "write-or-read-only"


def test_reports_heartbeat_only_peer_production_liveness_gap() -> None:
    events = [
        {
            "ts_utc": "2026-06-06T10:00:00Z",
            "agent": "codex-lead-1",
            "type": "status",
            "task_id": "lead-slice",
            "status": "working",
            "message": "coding",
        },
        {
            "ts_utc": "2026-06-06T10:08:00Z",
            "agent": "codex-lead-1",
            "type": "heartbeat",
            "task_id": "lead-heartbeat",
            "status": "active",
            "message": "background heartbeat",
        },
        {
            "ts_utc": "2026-06-06T10:13:00Z",
            "agent": "codex-lead-1",
            "type": "heartbeat",
            "task_id": "lead-heartbeat",
            "status": "active",
            "message": "background heartbeat",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 6, 10, 14, tzinfo=timezone.utc),
        production_idle_warn_minutes=12.0,
    )

    assert report["action"] == "claim_unblocked_work"
    liveness = report["production_liveness"]
    assert liveness["stalled_agent_count"] == 1
    stalled = liveness["stalled_agents"][0]
    assert stalled["agent"] == "codex-lead-1"
    assert stalled["reason"] == "heartbeat_only_since_activity"
    assert stalled["last_activity_task_id"] == "lead-slice"
    assert stalled["last_activity_ts_utc"] == "2026-06-06T10:00:00Z"
    assert stalled["last_heartbeat_ts_utc"] == "2026-06-06T10:13:00Z"
    assert stalled["idle_minutes"] == 14.0
    assert stalled["heartbeat_only_since_activity"] is True


def test_recent_peer_production_activity_does_not_report_liveness_gap() -> None:
    events = [
        {
            "ts_utc": "2026-06-06T10:00:00Z",
            "agent": "codex-lead-1",
            "type": "status",
            "task_id": "lead-slice",
            "status": "working",
            "message": "coding",
        },
        {
            "ts_utc": "2026-06-06T10:08:00Z",
            "agent": "codex-lead-1",
            "type": "status",
            "task_id": "lead-slice",
            "status": "tests_running",
            "message": "tests running",
        },
        {
            "ts_utc": "2026-06-06T10:13:00Z",
            "agent": "codex-lead-1",
            "type": "heartbeat",
            "task_id": "lead-heartbeat",
            "status": "active",
            "message": "background heartbeat",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 6, 10, 14, tzinfo=timezone.utc),
        production_idle_warn_minutes=12.0,
    )

    assert "production_liveness" not in report


def test_idle_protocol_counter_is_closed_by_later_consensus_target() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T06:57:22Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "idle-protocol-round4",
            "status": "idle_counter_proposal",
            "message": "round 4 counter",
            "payload": {
                "protocol_version": "idle-protocol.v1",
                "event_type": "idle_counter_proposal",
                "proposal_id": "idle-counter-round4",
            },
        },
        {
            "ts_utc": "2026-05-18T07:16:40Z",
            "agent": "codex",
            "to": "claude",
            "type": "message",
            "task_id": "idle-protocol-round5",
            "status": "idle_consensus_reached",
            "message": "round 5 consensus",
            "payload": {
                "protocol_version": "idle-protocol.v1",
                "event_type": "idle_consensus_reached",
                "proposal_id": "idle-consensus-round5",
                "consensus_target_proposal_id": "idle-counter-round4",
            },
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_idle_protocol_proposal_is_closed_by_later_response() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T05:53:31Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "idle-protocol-round1",
            "status": "idle_proposal",
            "message": "round 1 proposal",
            "payload": {
                "protocol_version": "idle-protocol.v1",
                "event_type": "idle_proposal",
                "proposal_id": "idle-proposal-round1",
            },
        },
        {
            "ts_utc": "2026-05-18T06:34:58Z",
            "agent": "codex",
            "to": "claude",
            "type": "message",
            "task_id": "idle-protocol-round2",
            "status": "idle_counter_proposal",
            "message": "round 2 response",
            "payload": {
                "protocol_version": "idle-protocol.v1",
                "event_type": "idle_counter_proposal",
                "proposal_id": "idle-counter-round2",
                "responds_to": "idle-proposal-round1",
            },
        },
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_read_events_honors_tail_before_validation(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "one"}),
                "{not-json",
                json.dumps({"task_id": "two"}),
                json.dumps({"task_id": "three"}),
            ]
        ),
        encoding="utf-8",
    )

    events = read_events(events_path, tail=2)

    assert [event["task_id"] for event in events] == ["two", "three"]


def test_read_events_fails_closed_on_malformed_selected_line(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "one"}),
                "{not-json",
                json.dumps({"task_id": "two"}),
            ]
        ),
        encoding="utf-8",
    )

    try:
        read_events(events_path, tail=3)
    except BridgeNextActionError as exc:
        assert exc.report["decision"] == "bridge_next_action_error"
        assert "line 2" in exc.report["errors"][0]
    else:
        raise AssertionError("malformed selected bridge event should fail closed")


def test_read_events_fails_closed_on_non_object_selected_line(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "one"}),
                json.dumps(["not-object"]),
                json.dumps({"task_id": "two"}),
            ]
        ),
        encoding="utf-8",
    )

    try:
        read_events(events_path, tail=3)
    except BridgeNextActionError as exc:
        assert exc.report["decision"] == "bridge_next_action_error"
        assert "line 2" in exc.report["errors"][0]
        assert "JSON object" in exc.report["errors"][0]
    else:
        raise AssertionError("non-object selected bridge event should fail closed")


def test_cli_outputs_json_recommendation(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-18T10:10:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": "new-task",
                "status": "request",
                "message": "new request",
            }
        ],
    )

    exit_code = main(
        [
            "--agent",
            "codex",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--now",
            "2026-05-18T10:15:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "bridge_next_action"
    assert report["action"] == "answer_incoming"


def test_cli_fails_closed_on_malformed_events_file(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = bridge / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text("[]\n", encoding="utf-8")

    exit_code = main(
        [
            "--agent",
            "codex",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--json",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "bridge_next_action_error"
    assert "JSON object" in report["errors"][0]


def test_cli_human_output_reports_stale_incoming(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-18T10:10:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": "old-task",
                "status": "request",
                "message": "old request",
            }
        ],
    )

    exit_code = main(
        [
            "--agent",
            "codex",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--now",
            "2026-05-20T10:15:00Z",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "action: claim_unblocked_work" in out
    assert "stale_incoming_count: 1" in out
    assert "stale_incoming_task_ids: old-task" in out


def test_cli_rejects_non_finite_open_request_max_age(
    tmp_path: Path, capsys
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-05-18T10:10:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": "old-task",
                "status": "request",
                "message": "old request",
            }
        ],
    )

    for value in ("nan", "inf"):
        exit_code = main(
            [
                "--agent",
                "codex",
                "--bridge-root",
                str(bridge),
                "--events",
                str(events_path),
                "--open-request-max-age-hours",
                value,
                "--json",
            ]
        )

        assert exit_code == 2
        report = json.loads(capsys.readouterr().out)
        assert report["decision"] == "bridge_next_action_error"
        assert report["errors"] == ["open_request_max_age_hours must be positive"]


def test_cli_rejects_non_finite_production_idle_warn_minutes(
    tmp_path: Path, capsys
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-06-06T10:00:00Z",
                "agent": "codex-lead-1",
                "type": "heartbeat",
                "task_id": "lead-heartbeat",
                "status": "active",
                "message": "background heartbeat",
            }
        ],
    )

    for value in ("nan", "inf"):
        exit_code = main(
            [
                "--agent",
                "codex-tools-1",
                "--bridge-root",
                str(bridge),
                "--events",
                str(events_path),
                "--production-idle-warn-minutes",
                value,
                "--json",
            ]
        )

        assert exit_code == 2
        report = json.loads(capsys.readouterr().out)
        assert report["decision"] == "bridge_next_action_error"
        assert report["errors"] == ["production_idle_warn_minutes must be positive"]


def test_private_marker_in_selected_output_is_refused() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "secret-task",
            "status": "request",
            "message": "contains PRIVATE_MARKER",
        }
    ]

    try:
        recommend_next_action(agent="codex", events=events, claims=[])
    except Exception as exc:
        assert "private marker" in str(exc)
    else:
        raise AssertionError("private marker should refuse selected output")


def test_private_marker_in_role_metadata_is_refused() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:10:00Z",
            "agent": "codex-impl-1",
            "type": "heartbeat",
            "task_id": "heartbeat",
            "status": "active",
            "message": "background heartbeat",
            "role": "PRIVATE_MARKER",
        }
    ]

    try:
        recommend_next_action(agent="codex-impl-1", events=events, claims=[])
    except BridgeNextActionError as exc:
        assert exc.report["decision"] == "bridge_next_action_refused"
        assert "private marker" in exc.report["errors"][0]
    else:
        raise AssertionError("private marker in metadata should refuse output")

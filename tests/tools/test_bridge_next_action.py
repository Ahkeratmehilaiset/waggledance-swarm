from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import tools.bridge_next_action as bridge_next_action
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


def test_suppressed_unavailable_agent_does_not_claim_operator_follow_nudge() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T11:00:00Z",
            "agent": "operator",
            "to": "fable-5",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260613",
            "status": "open",
            "severity": "medium",
            "message": (
                "jatka: read the bridge and answer open requests. "
                "classification=rco_wake_requested openIncoming=7"
            ),
        }
    ]

    report = recommend_next_action(
        agent="fable-5",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 13, 11, 5, tzinfo=timezone.utc),
        production_liveness_suppressed_agents={
            "fable-5": "operator reported fable lane unavailable"
        },
    )

    assert report["action"] == "agent_suppressed_unavailable"
    assert report["task_id"] == "agent-suppressed-unavailable"
    assert report["safe_mode"] == "read-only"
    assert report["open_incoming_count"] == 1
    assert report["suppression_reason"] == "operator reported fable lane unavailable"


def test_merge_blocking_incoming_request_interrupts_own_claim(
    tmp_path: Path,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="codex-tools-1",
        task_id="owned-tools-task",
        summary="already started",
        mode="write",
        write_scope=["tools/x.py"],
        bridge_root=bridge,
    )
    events = [
        {
            "ts_utc": "2026-06-08T06:06:43Z",
            "agent": "claude-rco-1",
            "to": "codex-tools-1",
            "type": "message",
            "task_id": "codex-lead-1/v12-solver-growth-heldout-dispatch-wave2-20260608",
            "status": "rco_request_tools_build_consensus_985",
            "message": (
                "TOOLS: #985 is RCO_PASS'd + CI 6/6 + CLEAN; needs your "
                "cross-review build_consensus_pass with payload.head."
            ),
            "payload": {
                "head": "6af2dcf1e1cea2fbc43349ff9419488f2355adbf",
            },
        }
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[claim],
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == (
        "codex-lead-1/v12-solver-growth-heldout-dispatch-wave2-20260608"
    )
    assert report["safe_mode"] == "read-only"
    assert report["incoming"]["agent"] == "claude-rco-1"
    assert report["claim_snapshot"]["own"][0]["task_id"] == "owned-tools-task"
    assert report["open_incoming_count"] == 1


@pytest.mark.parametrize(
    "status",
    [
        "rco_task_id_reemit_required",
        "rco_exact_head_reemit_required",
        "rco_reemit_required",
    ],
)
def test_rco_reemit_request_interrupts_own_claim(
    tmp_path: Path,
    status: str,
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="codex-lead-1",
        task_id="owned-lead-task",
        summary="already started",
        mode="write",
        write_scope=["tools/x.py"],
        bridge_root=bridge,
    )
    events = [
        {
            "ts_utc": "2026-06-13T15:45:09Z",
            "agent": "claude-rco-1",
            "to": "codex-lead-1",
            "type": "wake_request",
            "task_id": "codex-tools-1/agent-next-task-runtime-root-env-20260613",
            "status": status,
            "message": "Re-emit the review signal on the accepted exact-head task id.",
        }
    ]

    report = recommend_next_action(
        agent="codex-lead-1",
        events=events,
        claims=[claim],
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "codex-tools-1/agent-next-task-runtime-root-env-20260613"
    assert report["safe_mode"] == "read-only"
    assert report["incoming"]["status"] == status
    assert report["claim_snapshot"]["own"][0]["task_id"] == "owned-lead-task"
    assert report["open_incoming_count"] == 1


def test_free_text_build_consensus_request_does_not_interrupt_own_claim(
    tmp_path: Path,
) -> None:
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
            "ts_utc": "2026-06-08T06:10:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "message",
            "task_id": "ordinary-api-coordination",
            "status": "request",
            "message": "Please help build consensus on the API naming.",
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


def test_open_request_closure_scan_is_indexed(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    for index in range(200):
        events.append(
            {
                "ts_utc": f"2026-05-18T10:{index // 60:02d}:{index % 60:02d}Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "task_id": f"request-{index}",
                "status": "request",
                "message": "please review",
            }
        )
    for index in range(200):
        events.append(
            {
                "ts_utc": f"2026-05-18T11:{index // 60:02d}:{index % 60:02d}Z",
                "agent": "codex",
                "type": "done",
                "task_id": f"request-{index}",
                "status": "done",
                "message": "answered",
            }
        )

    answer_like_calls = 0
    original = bridge_next_action._is_answer_like

    def counting_is_answer_like(event):
        nonlocal answer_like_calls
        answer_like_calls += 1
        return original(event)

    monkeypatch.setattr(
        bridge_next_action,
        "_is_answer_like",
        counting_is_answer_like,
    )

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert answer_like_calls == len(events)


def test_request_changes_status_remains_open_request() -> None:
    events = [
        {
            "ts_utc": "2026-05-18T10:00:00Z",
            "agent": "claude",
            "to": "codex",
            "type": "decision",
            "task_id": "review-task",
            "status": "request_changes",
            "message": "please fix this",
        }
    ]

    report = recommend_next_action(agent="codex", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "review-task"
    assert report["open_incoming_count"] == 1


def test_answered_status_with_request_token_is_not_open_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T07:13:22Z",
            "agent": "codex-tools-1",
            "to": "codex-lead-1,driver,operator",
            "type": "decision",
            "task_id": "",
            "status": "review_request_answered_by_prior_build_consensus",
            "message": "already answered by build consensus",
        }
    ]

    report = recommend_next_action(
        agent="codex-lead-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T07:14:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_pass_requested_status_remains_open_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-14T02:52:16Z",
            "agent": "codex-lead-1",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "codex-lead-1/agent-next-task-wake-escalation-20260614",
            "status": "rco_exact_head_pass_requested_after_lead_consensus",
            "message": "RCO pass requested at exact head after lead consensus.",
        }
    ]

    report = recommend_next_action(
        agent="claude-rco-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-14T02:53:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "codex-lead-1/agent-next-task-wake-escalation-20260614"
    assert report["incoming"]["status"] == (
        "rco_exact_head_pass_requested_after_lead_consensus"
    )


def test_observed_status_with_request_token_is_not_open_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T08:42:24Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1,claude-rco-1",
            "type": "finding",
            "task_id": "pr1116-stale-review-finding",
            "status": "stale_review_request_after_rebase_observed",
            "message": "observed stale review request after rebase",
        }
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T08:43:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_resolved_status_with_requested_token_is_not_open_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-11T09:30:42Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1,operator",
            "type": "message",
            "task_id": "duplicate-pr-deconflict",
            "status": "changes_requested_resolved",
            "message": "duplicate PR flow has been resolved",
        }
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_empty_task_pr_request_closes_when_target_answers_same_pr() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T07:33:18Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "",
            "status": "review_requested",
            "message": "Review PR #1114",
            "payload": {"pr": 1114},
        },
        {
            "ts_utc": "2026-06-13T07:34:33Z",
            "agent": "codex-tools-1",
            "to": "codex-lead-1,operator",
            "type": "decision",
            "task_id": "codex-lead/bridge-liveness-suppressed-unavailable-lanes",
            "status": "build_consensus_pass",
            "message": "Tools build consensus for PR #1114",
            "payload": {"pr": 1114},
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T07:35:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_empty_task_pr_request_is_not_closed_by_different_pr_answer() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T07:33:18Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "",
            "status": "review_requested",
            "message": "Review PR #1114",
            "payload": {"pr": 1114},
        },
        {
            "ts_utc": "2026-06-13T07:34:33Z",
            "agent": "codex-tools-1",
            "to": "codex-lead-1,operator",
            "type": "decision",
            "task_id": "other-review",
            "status": "build_consensus_pass",
            "message": "Tools build consensus for PR #1115",
            "payload": {"pr": 1115},
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T07:35:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


def test_same_pr_requester_build_pass_does_not_close_rco_review_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T09:00:00Z",
            "agent": "codex-lead-1",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "pr999-rco-review",
            "status": "rco_review_requested",
            "message": "RCO review requested for PR #999",
            "payload": {"pr": 999},
        },
        {
            "ts_utc": "2026-06-13T09:01:00Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1,operator",
            "type": "decision",
            "task_id": "pr999-build-consensus",
            "status": "build_consensus_pass",
            "message": "Lead build consensus for PR #999",
            "payload": {"pr": 999},
        },
    ]

    report = recommend_next_action(
        agent="claude-rco-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T09:02:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "pr999-rco-review"
    assert report["open_incoming_count"] == 1


def test_same_pr_requester_terminal_close_closes_rco_review_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T09:00:00Z",
            "agent": "codex-lead-1",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "pr999-rco-review",
            "status": "rco_review_requested",
            "message": "RCO review requested for PR #999",
            "payload": {"pr": 999},
        },
        {
            "ts_utc": "2026-06-13T09:01:00Z",
            "agent": "codex-lead-1",
            "to": "claude-rco-1,operator",
            "type": "decision",
            "task_id": "pr999-closure",
            "status": "superseded",
            "message": "PR #999 review request superseded",
            "payload": {"pr": 999},
        },
    ]

    report = recommend_next_action(
        agent="claude-rco-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T09:02:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


@pytest.mark.parametrize(
    "status",
    [
        "changes_requested_NOT_resolved",
        "blocked_NOT_closed",
    ],
)
def test_negated_terminal_words_do_not_close_request_statuses(status: str) -> None:
    events = [
        {
            "ts_utc": "2026-06-11T09:30:42Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1",
            "type": "message",
            "task_id": "still-open-task",
            "status": status,
            "message": "this request remains unresolved",
        }
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "still-open-task"
    assert report["open_incoming_count"] == 1


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


def test_target_stale_sweep_finding_suppresses_reported_stale_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-11T17:34:52Z",
            "agent": "operator",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260611",
            "status": "open",
            "message": "historical wake request",
        },
        {
            "ts_utc": "2026-06-13T05:24:41Z",
            "agent": "codex-tools-1",
            "to": "operator",
            "type": "finding",
            "task_id": "operational-scout-bridge-stale-incoming-sweep-2026-06-13",
            "status": "stale_incoming_sweep_complete",
            "message": "classified the stale backlog as historical noise",
            "payload": {
                "stale_task_ids": ["bridge-follow-nudge-20260611"],
            },
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 13, 6, 0, tzinfo=timezone.utc),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0
    assert "stale_incoming_task_ids" not in report


def test_stale_sweep_finding_before_request_does_not_suppress_new_stale_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-11T17:00:00Z",
            "agent": "codex-tools-1",
            "to": "operator",
            "type": "finding",
            "task_id": "operational-scout-bridge-stale-incoming-sweep-20260611",
            "status": "stale_incoming_sweep_complete",
            "message": "prior stale sweep",
            "payload": {
                "stale_task_ids": ["bridge-follow-nudge-20260611"],
            },
        },
        {
            "ts_utc": "2026-06-11T17:34:52Z",
            "agent": "operator",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260611",
            "status": "open",
            "message": "new wake request after the prior sweep",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 13, 6, 0, tzinfo=timezone.utc),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1
    assert report["stale_incoming_task_ids"] == ["bridge-follow-nudge-20260611"]


def test_other_agent_stale_sweep_finding_does_not_suppress_target_stale_request() -> None:
    events = [
        {
            "ts_utc": "2026-06-11T17:34:52Z",
            "agent": "operator",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260611",
            "status": "open",
            "message": "historical wake request",
        },
        {
            "ts_utc": "2026-06-13T05:24:41Z",
            "agent": "codex-lead-1",
            "to": "operator",
            "type": "finding",
            "task_id": "operational-scout-bridge-stale-incoming-sweep-20260613",
            "status": "stale_incoming_sweep_complete",
            "message": "a different agent cannot close tools stale backlog",
            "payload": {
                "stale_task_ids": ["bridge-follow-nudge-20260611"],
            },
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 13, 6, 0, tzinfo=timezone.utc),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1
    assert report["stale_incoming_task_ids"] == ["bridge-follow-nudge-20260611"]


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
def test_ack_message_statuses_close_incoming_request(status: str) -> None:
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

    assert report["action"] == "claim_unblocked_work"
    assert report["task_id"] == "next-unclaimed-scout-or-implementation"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_empty_task_request_closes_when_target_answers_requester() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T07:10:00Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "",
            "status": "review_requested",
            "message": "please review the current PR",
        },
        {
            "ts_utc": "2026-06-13T07:12:00Z",
            "agent": "codex-tools-1",
            "to": "codex-lead-1,driver,operator",
            "type": "decision",
            "task_id": "",
            "status": "review_request_answered_by_prior_build_consensus",
            "message": "already answered by build consensus",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T07:13:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_empty_task_request_closes_when_requester_retracts_for_target() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T07:10:00Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "",
            "status": "review_requested",
            "message": "please review the current PR",
        },
        {
            "ts_utc": "2026-06-13T07:12:00Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1,operator",
            "type": "decision",
            "task_id": "",
            "status": "closed",
            "message": "withdrawing this empty-task request",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T07:13:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_empty_task_request_is_not_closed_by_unrelated_agent_answer() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T07:10:00Z",
            "agent": "codex-lead-1",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "",
            "status": "review_requested",
            "message": "please review the current PR",
        },
        {
            "ts_utc": "2026-06-13T07:12:00Z",
            "agent": "claude-rco-1",
            "to": "codex-lead-1,operator",
            "type": "decision",
            "task_id": "",
            "status": "answered",
            "message": "unrelated empty-task answer",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T07:13:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["incoming"]["message"] == "please review the current PR"
    assert report["open_incoming_count"] == 1


def test_requester_retraction_closes_incoming_finding_for_target() -> None:
    events = [
        {
            "ts_utc": "2026-06-12T05:11:00Z",
            "agent": "claude-rco-2",
            "to": "fable-5,codex-lead-1",
            "type": "finding",
            "task_id": "fable-5/failover-refuse-path-tests-20260612",
            "status": "changes_requested",
            "message": "blocking finding",
        },
        {
            "ts_utc": "2026-06-12T05:13:00Z",
            "agent": "claude-rco-2",
            "to": "fable-5,codex-lead-1",
            "type": "decision",
            "task_id": "fable-5/failover-refuse-path-tests-20260612",
            "status": "rco_finding_withdrawn",
            "message": "withdrawing the prior finding",
        },
    ]

    report = recommend_next_action(agent="fable-5", events=events, claims=[])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_unrelated_retraction_does_not_close_incoming_finding() -> None:
    events = [
        {
            "ts_utc": "2026-06-12T05:11:00Z",
            "agent": "claude-rco-2",
            "to": "fable-5,codex-lead-1",
            "type": "finding",
            "task_id": "fable-5/failover-refuse-path-tests-20260612",
            "status": "changes_requested",
            "message": "blocking finding",
        },
        {
            "ts_utc": "2026-06-12T05:13:00Z",
            "agent": "codex-tools-1",
            "to": "fable-5,codex-lead-1",
            "type": "decision",
            "task_id": "fable-5/failover-refuse-path-tests-20260612",
            "status": "rco_finding_withdrawn",
            "message": "third-party note; not the requester or target",
        },
    ]

    report = recommend_next_action(agent="fable-5", events=events, claims=[])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "fable-5/failover-refuse-path-tests-20260612"
    assert report["open_incoming_count"] == 1


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


def test_operator_wake_request_is_incoming_for_target_agent() -> None:
    events = [
        {
            "ts_utc": "2026-06-12T18:53:02Z",
            "agent": "operator",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260612",
            "status": "open",
            "severity": "medium",
            "message": (
                "jatka: read the bridge and answer open requests. "
                "classification=rco_wake_requested openIncoming=1"
            ),
        }
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-12T18:55:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "bridge-follow-nudge-20260612"
    assert report["incoming"]["type"] == "wake_request"
    assert report["incoming"]["status"] == "open"
    assert report["open_incoming_count"] == 1


def test_operator_wake_request_ages_out_before_selection() -> None:
    events = [
        {
            "ts_utc": "2026-06-12T12:00:00Z",
            "agent": "operator",
            "to": "codex-tools-1",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260612",
            "status": "open",
            "message": "old operator wake",
        }
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T03:00:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1
    assert report["stale_incoming_task_ids"] == ["bridge-follow-nudge-20260612"]


def test_repeated_wake_request_rows_count_as_one_actionable_incoming() -> None:
    events = [
        {
            "ts_utc": "2026-06-12T18:53:02Z",
            "agent": "operator",
            "to": "codex-lead-1",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260612",
            "status": "open",
            "message": "first wake",
        },
        {
            "ts_utc": "2026-06-12T18:54:02Z",
            "agent": "operator",
            "to": "codex-lead-1",
            "type": "wake_request",
            "task_id": "bridge-follow-nudge-20260612",
            "status": "open",
            "message": "latest wake",
        },
    ]

    report = recommend_next_action(
        agent="codex-lead-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-12T18:55:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "bridge-follow-nudge-20260612"
    assert report["incoming"]["message"] == "latest wake"
    assert report["open_incoming_count"] == 1
    assert report["open_incoming_event_count"] == 2
    assert report["open_incoming_duplicate_count"] == 1


def test_rco_pass_required_wake_request_remains_actionable_after_pass_token() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T10:21:01Z",
            "agent": "codex-tools-1",
            "to": "claude-rco-1,claude-rco-2",
            "type": "handoff",
            "task_id": "codex-tools-1-operator-feedback-preflight-cli-20260613",
            "status": "lead_patch_pushed_ci_pending",
            "message": "PR #1119 patch pushed; CI pending.",
        },
        {
            "ts_utc": "2026-06-13T10:34:37Z",
            "agent": "codex-lead-1",
            "to": "claude-rco-1,claude-rco-2,operator",
            "type": "wake_request",
            "task_id": "codex-tools-1-operator-feedback-preflight-cli-20260613",
            "status": "rco_pass_required_after_ci_green",
            "message": "PR #1119 is CI green; RCO pass required.",
        },
    ]

    report = recommend_next_action(
        agent="claude-rco-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T10:35:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "codex-tools-1-operator-feedback-preflight-cli-20260613"
    assert report["incoming"]["type"] == "wake_request"
    assert report["incoming"]["status"] == "rco_pass_required_after_ci_green"
    assert report["open_incoming_count"] == 2


def test_rco_pass_decision_remains_response_only() -> None:
    events = [
        {
            "ts_utc": "2026-06-13T10:34:37Z",
            "agent": "claude-rco-1",
            "to": "codex-lead-1,codex-tools-1",
            "type": "decision",
            "task_id": "codex-tools-1-operator-feedback-preflight-cli-20260613",
            "status": "rco_pass",
            "message": "RCO_PASS PR #1119 at exact head.",
        },
    ]

    report = recommend_next_action(
        agent="codex-lead-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-13T10:35:00+00:00"),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_duplicate_non_wake_requests_remain_separate_incoming_rows() -> None:
    events = [
        {
            "ts_utc": "2026-06-12T18:53:02Z",
            "agent": "claude-rco-1",
            "to": "codex-tools-1",
            "type": "message",
            "task_id": "build-consensus-needed",
            "status": "build_consensus_requested",
            "message": "first review request",
        },
        {
            "ts_utc": "2026-06-12T18:54:02Z",
            "agent": "claude-rco-1",
            "to": "codex-tools-1",
            "type": "message",
            "task_id": "build-consensus-needed",
            "status": "build_consensus_requested",
            "message": "second review request",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime.fromisoformat("2026-06-12T18:55:00+00:00"),
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 2
    assert "open_incoming_event_count" not in report


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


def test_ignores_expired_foreign_write_claim_when_recommending() -> None:
    claim = Claim(
        agent="claude",
        task_id="expired-foreign-write",
        summary="other agent stopped heartbeating",
        mode="write",
        write_scope=("tools/bridge_next_action.py",),
        run_id="claude-run",
        claimed_at_utc="2026-06-07T18:00:00Z",
        last_heartbeat_utc="2026-06-07T18:00:00Z",
        lease_seconds=300,
        claim_lease_expires_utc="2026-06-07T18:05:00Z",
    )

    report = recommend_next_action(
        agent="codex",
        events=[],
        claims=[claim],
        now_utc=datetime(2026, 6, 7, 18, 6, tzinfo=timezone.utc),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["active_claim_count"] == 0
    assert report["foreign_write_claim_count"] == 0
    assert "claim_snapshot" not in report
    assert report["ignored_stale_claim_count"] == 1
    assert report["ignored_stale_claims"][0]["task_id"] == "expired-foreign-write"


def test_keeps_foreign_write_claim_until_explicit_expiry() -> None:
    claim = Claim(
        agent="claude",
        task_id="extended-foreign-write",
        summary="other agent extended lease",
        mode="write",
        write_scope=("tools/bridge_next_action.py",),
        run_id="claude-run",
        claimed_at_utc="2026-06-07T18:00:00Z",
        last_heartbeat_utc="2026-06-07T18:00:00Z",
        lease_seconds=300,
        claim_lease_expires_utc="2026-06-07T18:10:00Z",
    )

    report = recommend_next_action(
        agent="codex",
        events=[],
        claims=[claim],
        now_utc=datetime(2026, 6, 7, 18, 6, tzinfo=timezone.utc),
    )

    assert report["action"] == "parallel_read_only"
    assert report["active_claim_count"] == 1
    assert report["foreign_write_claim_count"] == 1
    assert "ignored_stale_claim_count" not in report


def test_ignores_expired_own_claim_when_recommending() -> None:
    claim = Claim(
        agent="codex",
        task_id="expired-own-write",
        summary="own claim stopped heartbeating",
        mode="write",
        write_scope=("tools/bridge_next_action.py",),
        run_id="codex-run",
        claimed_at_utc="2026-06-07T18:00:00Z",
        last_heartbeat_utc="2026-06-07T18:00:00Z",
        lease_seconds=300,
        claim_lease_expires_utc="2026-06-07T18:05:00Z",
    )

    report = recommend_next_action(
        agent="codex",
        events=[],
        claims=[claim],
        now_utc=datetime(2026, 6, 7, 18, 6, tzinfo=timezone.utc),
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["active_claim_count"] == 0
    assert report["ignored_stale_claim_count"] == 1
    assert report["ignored_stale_claims"][0]["task_id"] == "expired-own-write"


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


def test_repeated_wake_delivery_gap_is_reported_in_production_liveness(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    bridge_root.mkdir()
    (bridge_root / "wake_claude-rco-1").write_text(
        "2026-06-06T10:05:00Z",
        encoding="utf-8",
    )
    events = [
        {
            "ts_utc": "2026-06-06T09:58:00Z",
            "agent": "operator",
            "to": "driver",
            "type": "wake_request",
            "task_id": "merge-needed",
            "status": "merge_requested",
            "message": "please merge",
        },
        {
            "ts_utc": "2026-06-06T09:59:00Z",
            "agent": "operator",
            "to": "driver",
            "type": "wake_request",
            "task_id": "merge-needed",
            "status": "merge_requested",
            "message": "please merge again",
        },
        {
            "ts_utc": "2026-06-06T10:00:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge",
        },
        {
            "ts_utc": "2026-06-06T10:05:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge again",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        bridge_root=bridge_root,
        now_utc=datetime(2026, 6, 6, 10, 20, tzinfo=timezone.utc),
        production_idle_warn_minutes=12.0,
    )

    assert report["action"] == "escalate_wake_delivery_stall"
    assert report["task_id"] == "bridge-wake-delivery-stalled"
    assert report["safe_mode"] == "read-only"
    assert report["operator_action_required"] is True
    assert report["operator_action"] == (
        "restart_or_verify_target_agent_bridge_session_watcher"
    )
    assert report["operator_action_reason"] == (
        "wake_request_visible_but_no_later_target_bridge_activity"
    )
    assert report["operator_action_target_agents"] == ["claude-rco-1"]
    liveness = report["production_liveness"]
    assert liveness["stalled_agent_count"] == 0
    delivery = liveness["wake_delivery"]
    assert delivery["decision"] == "wake_delivery_stalled"
    assert delivery["stalled_wake_count"] == 1
    assert delivery["by_agent"] == {"claude-rco-1": 1}
    assert delivery["delivery_escalation"] == {
        "required": True,
        "target_agents": ["claude-rco-1"],
        "do_not_emit_additional_wake_requests": True,
        "safe_next_action": "restart_or_verify_target_agent_bridge_session_watcher",
        "operator_action_required": True,
        "reason": "wake_request_visible_but_no_later_target_bridge_activity",
    }
    wake = delivery["stalled_wakes"][0]
    assert wake["target_agent"] == "claude-rco-1"
    assert wake["task_id"] == "rco-needed"
    assert wake["wake_request_count"] == 2
    assert wake["wake_file_checked"] is True
    assert wake["wake_file_present"] is True
    assert wake["age_minutes"] == 20.0
    assert wake["latest_wake_age_minutes"] == 15.0
    assert wake["safe_next_action"].startswith("restart or verify")


def test_target_activity_clears_wake_delivery_gap() -> None:
    events = [
        {
            "ts_utc": "2026-06-06T10:00:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge",
        },
        {
            "ts_utc": "2026-06-06T10:05:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge again",
        },
        {
            "ts_utc": "2026-06-06T10:06:00Z",
            "agent": "claude-rco-1",
            "type": "decision",
            "task_id": "rco-needed",
            "status": "rco_pass",
            "message": "reviewed",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 6, 10, 10, tzinfo=timezone.utc),
        production_idle_warn_minutes=12.0,
    )

    assert "production_liveness" not in report


def test_target_heartbeat_does_not_clear_wake_delivery_gap() -> None:
    events = [
        {
            "ts_utc": "2026-06-06T10:00:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge",
        },
        {
            "ts_utc": "2026-06-06T10:05:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge again",
        },
        {
            "ts_utc": "2026-06-06T10:06:00Z",
            "agent": "claude-rco-1",
            "type": "heartbeat",
            "task_id": "rco-heartbeat",
            "status": "active",
            "message": "background heartbeat",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 6, 10, 20, tzinfo=timezone.utc),
        production_idle_warn_minutes=12.0,
    )

    delivery = report["production_liveness"]["wake_delivery"]
    assert delivery["decision"] == "wake_delivery_stalled"
    assert delivery["by_agent"] == {"claude-rco-1": 1}
    assert delivery["delivery_escalation"]["do_not_emit_additional_wake_requests"] is True
    assert delivery["delivery_escalation"]["safe_next_action"] == (
        "restart_or_verify_target_agent_bridge_session_watcher"
    )
    wake = delivery["stalled_wakes"][0]
    assert wake["target_agent"] == "claude-rco-1"
    assert wake["wake_request_count"] == 2
    assert wake["latest_wake_age_minutes"] == 15.0


def test_wake_delivery_gap_does_not_interrupt_active_own_claim(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    claim = claim_task(
        agent="codex-tools-1",
        task_id="owned-tools-task",
        summary="already coding",
        mode="write",
        write_scope=["tools/x.py"],
        bridge_root=bridge_root,
    )
    events = [
        {
            "ts_utc": "2026-06-06T10:00:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge",
        },
        {
            "ts_utc": "2026-06-06T10:05:00Z",
            "agent": "operator",
            "to": "claude-rco-1",
            "type": "wake_request",
            "task_id": "rco-needed",
            "status": "open",
            "message": "please read bridge again",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[claim],
        bridge_root=bridge_root,
        now_utc=datetime(2026, 6, 6, 10, 20, tzinfo=timezone.utc),
        production_idle_warn_minutes=12.0,
    )

    assert report["action"] == "continue_claim"
    assert report["task_id"] == "owned-tools-task"
    assert report["operator_action_required"] is True
    assert report["operator_action_target_agents"] == ["claude-rco-1"]


def test_suppressed_liveness_lane_is_not_counted_as_actionable_stall() -> None:
    events = [
        {
            "ts_utc": "2026-06-06T10:00:00Z",
            "agent": "fable-5",
            "type": "status",
            "task_id": "fable-bootstrap",
            "status": "active",
            "message": "producer lane bootstrap",
        },
        {
            "ts_utc": "2026-06-06T10:01:00Z",
            "agent": "codex-lead-1",
            "type": "status",
            "task_id": "lead-slice",
            "status": "working",
            "message": "coding",
        },
    ]

    report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=datetime(2026, 6, 6, 10, 20, tzinfo=timezone.utc),
        production_idle_warn_minutes=12.0,
        production_liveness_suppressed_agents={
            "fable-5": "model access disabled by operator runtime config"
        },
    )

    liveness = report["production_liveness"]
    assert liveness["stalled_agent_count"] == 1
    assert [item["agent"] for item in liveness["stalled_agents"]] == [
        "codex-lead-1"
    ]
    assert liveness["suppressed_stalled_agent_count"] == 1
    suppressed = liveness["suppressed_stalled_agents"][0]
    assert suppressed["agent"] == "fable-5"
    assert (
        suppressed["suppressed_reason"]
        == "model access disabled by operator runtime config"
    )


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


def test_cli_defaults_to_runtime_bridge_root_env_for_claims(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    _events_file(runtime_bridge, [])
    claim_task(
        agent="codex",
        task_id="runtime-owned-task",
        summary="runtime claim",
        mode="write",
        write_scope=["tools/runtime.py"],
        bridge_root=runtime_bridge,
    )

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    exit_code = main(["--agent", "codex", "--json"])

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["action"] == "continue_claim"
    assert report["task_id"] == "runtime-owned-task"
    assert report["active_claim_count"] == 1
    assert report["claim_snapshot"]["own"][0]["task_id"] == "runtime-owned-task"


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


def test_cli_loads_liveness_suppression_config(tmp_path: Path, capsys) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-06-06T10:00:00Z",
                "agent": "grok-scout-1",
                "type": "blocked",
                "task_id": "grok-budget",
                "status": "redteam_blocked",
                "message": "budget unavailable",
            }
        ],
    )
    suppression_config = tmp_path / "bridge_liveness_suppression.json"
    suppression_config.write_text(
        json.dumps(
            {
                "version": 1,
                "suppressed_agents": {
                    "grok-scout-1": {
                        "reason": "budget unavailable until reset"
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--agent",
            "codex-lead-1",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--now",
            "2026-06-06T10:20:00Z",
            "--production-liveness-suppression-config",
            str(suppression_config),
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    liveness = report["production_liveness"]
    assert liveness["stalled_agent_count"] == 0
    assert liveness["suppressed_stalled_agent_count"] == 1
    assert liveness["suppressed_stalled_agents"][0]["agent"] == "grok-scout-1"


def test_cli_loads_default_runtime_liveness_suppression_config(
    tmp_path: Path, capsys
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-06-06T10:00:00Z",
                "agent": "fable-5",
                "type": "status",
                "task_id": "fable-bootstrap",
                "status": "active",
                "message": "producer lane bootstrap",
            }
        ],
    )
    suppression_config = bridge / "shared" / "production_liveness_suppression.json"
    suppression_config.write_text(
        json.dumps(
            {
                "version": 1,
                "suppressed_agents": {
                    "fable-5": {
                        "reason": "runtime policy says model lane unavailable"
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--agent",
            "codex-lead-1",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--now",
            "2026-06-06T10:20:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    liveness = report["production_liveness"]
    assert liveness["stalled_agent_count"] == 0
    assert liveness["suppressed_stalled_agent_count"] == 1
    assert liveness["suppressed_stalled_agents"][0]["agent"] == "fable-5"


def test_cli_missing_default_liveness_suppression_config_keeps_stall_actionable(
    tmp_path: Path, capsys
) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-06-06T10:00:00Z",
                "agent": "fable-5",
                "type": "status",
                "task_id": "fable-bootstrap",
                "status": "active",
                "message": "producer lane bootstrap",
            }
        ],
    )

    exit_code = main(
        [
            "--agent",
            "codex-lead-1",
            "--bridge-root",
            str(bridge),
            "--events",
            str(events_path),
            "--now",
            "2026-06-06T10:20:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    liveness = report["production_liveness"]
    assert liveness["stalled_agent_count"] == 1
    assert "suppressed_stalled_agents" not in liveness


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

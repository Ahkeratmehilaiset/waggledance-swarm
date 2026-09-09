# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

import tools.rco_readiness_report as rco_readiness_module
from tools.rco_readiness_report import (
    CLAIM_GATES,
    RcoReadinessError,
    build_rco_readiness_report,
    main,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "rco_readiness_report.py"


def _request(
    *,
    ts: str = "2026-06-14T12:00:00Z",
    task_id: str = "pr1208-rco-pass",
    status: str = "rco_pass_required_after_ci_green",
    agent: str = "codex-tools-1",
    to: str = "claude-rco-2",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": to,
        "type": "wake_request",
        "task_id": task_id,
        "status": status,
        "message": "PR #1208 is CI green; pass/block requested at exact head.",
        "payload": {"pr": 1208, "head": "c" * 40},
    }


def _general_review() -> dict[str, object]:
    return {
        "ts_utc": "2026-06-14T12:05:00Z",
        "agent": "codex-lead-1",
        "to": "claude-rco-2",
        "type": "message",
        "task_id": "general-review",
        "status": "review_requested",
        "message": "Please review a general proposal.",
    }


def _now() -> datetime:
    return datetime(2026, 6, 14, 12, 10, tzinfo=timezone.utc)


def _events_file(bridge_root: Path, events: list[dict[str, object]]) -> Path:
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_reports_direct_rco_pass_request_ahead_of_general_review(tmp_path: Path) -> None:
    (tmp_path / "wake_claude-rco-2").write_text(
        "2026-06-14T12:04:00Z",
        encoding="utf-8",
    )

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[_request(), _general_review()],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "direct_rco_pass_block_request_ready"
    assert report["wake_file"]["present"] is True
    assert report["wake_consumed"] is False
    assert report["direct_pass_block_request_count"] == 1
    assert report["highest_priority_request"]["task_id"] == "pr1208-rco-pass"
    assert report["highest_priority_request"]["priority"] == "direct_rco_pass_block"
    assert report["bridge_next_action"]["open_incoming_count"] == 2
    assert "Test-BridgeWake.ps1 -Agent claude-rco-2" in report["wake_consume_step0_command"]


@pytest.mark.parametrize("bad_ts", ["2099-06-14T12:00:00Z", "not-a-timestamp"])
@pytest.mark.parametrize("bad_first", [False, True])
@pytest.mark.parametrize("explicit_now", [False, True])
def test_future_or_invalid_direct_request_cannot_poison_default_clock_or_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_ts: str,
    bad_first: bool,
    explicit_now: bool,
) -> None:
    fixed_now = _now()

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(rco_readiness_module, "datetime", FixedDatetime)
    valid = _request(ts="2026-06-14T12:00:00Z", agent="codex-lead-1")
    bad = _request(ts=bad_ts, agent="codex-tools-1")
    events = [bad, valid] if bad_first else [valid, bad]
    kwargs = {"now_utc": fixed_now} if explicit_now else {}

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=events,
        bridge_root=tmp_path,
        claims=[],
        **kwargs,
    )

    assert report["decision"] == "direct_rco_pass_block_request_ready"
    assert report["direct_pass_block_request_count"] == 1
    assert report["highest_priority_request"]["ts_utc"] == (
        "2026-06-14T12:00:00Z"
    )
    assert report["highest_priority_request"]["requester"] == "codex-lead-1"
    assert report["bridge_next_action"]["open_incoming_count"] == 1
    assert report["bridge_next_action"]["incoming"]["ts_utc"] == (
        "2026-06-14T12:00:00Z"
    )


@pytest.mark.parametrize(
    "closure_ts",
    ["2026-06-14T12:05:00Z", "2099-06-14T12:05:00Z", "not-a-timestamp"],
)
@pytest.mark.parametrize("closer", ["target", "requester"])
def test_later_direct_closure_uses_append_order_not_event_clock(
    tmp_path: Path,
    closure_ts: str,
    closer: str,
) -> None:
    request = _request(agent="codex-lead-1")
    request.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    if closer == "target":
        closure = {
            "ts_utc": closure_ts,
            "agent": "claude-rco-2",
            "to": "codex-lead-1",
            "type": "decision",
            "task_id": "pr1208-rco-pass",
            "status": "rco_pass",
            "message": "substantive target response appended later",
            "payload": {"pr": 1208, "head": "c" * 40},
        }
    else:
        closure = {
            "ts_utc": closure_ts,
            "agent": "codex-lead-1",
            "agent_uuid": "uuid-a",
            "session_id": "session-a",
            "to": "claude-rco-2",
            "type": "done",
            "task_id": "pr1208-rco-pass",
            "status": "done",
            "message": "requester closed the request",
            "payload": {"pr": 1208, "head": "c" * 40},
        }

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[request, closure],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 0
    assert report["bridge_next_action"]["open_incoming_count"] == 0


@pytest.mark.parametrize("closer", ["target", "requester"])
def test_tail_replayed_stale_direct_closure_keeps_request_open(
    tmp_path: Path,
    closer: str,
) -> None:
    request = _request(agent="codex-lead-1")
    request.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    if closer == "target":
        closure = {
            "ts_utc": "2026-06-14T11:00:00Z",
            "agent": "claude-rco-2",
            "to": "codex-lead-1",
            "type": "decision",
            "task_id": "pr1208-rco-pass",
            "status": "rco_pass",
            "message": "stale target response replayed onto the log tail",
            "payload": {"pr": 1208, "head": "c" * 40},
        }
    else:
        closure = {
            "ts_utc": "2026-06-14T11:00:00Z",
            "agent": "codex-lead-1",
            "agent_uuid": "uuid-a",
            "session_id": "session-a",
            "to": "claude-rco-2",
            "type": "done",
            "task_id": "pr1208-rco-pass",
            "status": "done",
            "message": "stale requester closure replayed onto the log tail",
            "payload": {"pr": 1208, "head": "c" * 40},
        }

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[request, closure],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 1
    assert report["bridge_next_action"]["open_incoming_count"] == 1


def test_prior_future_direct_closure_does_not_close_later_request(
    tmp_path: Path,
) -> None:
    closure = {
        "ts_utc": "2099-06-14T12:05:00Z",
        "agent": "claude-rco-2",
        "to": "codex-lead-1",
        "type": "decision",
        "task_id": "pr1208-rco-pass",
        "status": "rco_pass",
        "message": "response was appended before the request",
        "payload": {"pr": 1208, "head": "c" * 40},
    }
    request = _request(agent="codex-lead-1")

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[closure, request],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 1
    assert report["bridge_next_action"]["open_incoming_count"] == 1


@pytest.mark.parametrize(
    ("closure_uuid", "closure_session"),
    [("uuid-b", "session-a"), ("uuid-a", "session-b")],
)
def test_requester_direct_closure_requires_matching_identity(
    tmp_path: Path,
    closure_uuid: str,
    closure_session: str,
) -> None:
    request = _request(agent="codex-lead-1")
    request.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    closure = {
        "ts_utc": "2026-06-14T12:05:00Z",
        "agent": "codex-lead-1",
        "agent_uuid": closure_uuid,
        "session_id": closure_session,
        "to": "claude-rco-2",
        "type": "done",
        "task_id": "pr1208-rco-pass",
        "status": "done",
        "message": "stale requester identity must not close",
        "payload": {"pr": 1208, "head": "c" * 40},
    }

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[request, closure],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 1
    assert report["bridge_next_action"]["open_incoming_count"] == 1


def test_direct_requests_from_distinct_sessions_remain_independent(
    tmp_path: Path,
) -> None:
    first = _request(ts="2026-06-14T12:00:00Z", agent="codex-lead-1")
    first.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    second = _request(ts="2026-06-14T12:01:00Z", agent="codex-lead-1")
    second.update({"agent_uuid": "uuid-a", "session_id": "session-b"})
    close_first = {
        "ts_utc": "2026-06-14T12:05:00Z",
        "agent": "codex-lead-1",
        "agent_uuid": "uuid-a",
        "session_id": "session-a",
        "to": "claude-rco-2",
        "type": "done",
        "task_id": "pr1208-rco-pass",
        "status": "done",
        "message": "close only session A",
        "payload": {"pr": 1208, "head": "c" * 40},
    }
    target_response = {
        "ts_utc": "2026-06-14T12:06:00Z",
        "agent": "claude-rco-2",
        "to": "codex-lead-1",
        "type": "decision",
        "task_id": "pr1208-rco-pass",
        "status": "rco_pass",
        "message": "target response closes every matching request",
        "payload": {"pr": 1208, "head": "c" * 40},
    }

    both_open = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[first, second],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )
    one_open = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[first, second, close_first],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )
    none_open = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[first, second, target_response],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert both_open["direct_pass_block_request_count"] == 2
    assert both_open["bridge_next_action"]["open_incoming_count"] == 2
    assert one_open["direct_pass_block_request_count"] == 1
    assert one_open["bridge_next_action"]["open_incoming_count"] == 1
    assert none_open["direct_pass_block_request_count"] == 0
    assert none_open["bridge_next_action"]["open_incoming_count"] == 0


def test_backdated_repeat_does_not_expire_current_direct_request(
    tmp_path: Path,
) -> None:
    current = _request(ts="2026-06-14T12:00:00Z", agent="codex-lead-1")
    current.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    backdated = _request(ts="2026-06-13T00:00:00Z", agent="codex-lead-1")
    backdated.update({"agent_uuid": "uuid-a", "session_id": "session-a"})

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[current, backdated],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 1
    assert report["highest_priority_request"]["ts_utc"] == (
        "2026-06-14T12:00:00Z"
    )
    assert report["bridge_next_action"]["open_incoming_count"] == 1


@pytest.mark.parametrize("malformed_pr", [True, 1208.5])
def test_malformed_pr_does_not_create_cross_task_direct_closure(
    tmp_path: Path,
    malformed_pr: object,
) -> None:
    request = _request(task_id="request-task", agent="codex-lead-1")
    request["payload"] = {"pr": malformed_pr, "head": "c" * 40}
    closure = {
        "ts_utc": "2026-06-14T12:05:00Z",
        "agent": "claude-rco-2",
        "to": "codex-lead-1",
        "type": "decision",
        "task_id": "other-task",
        "status": "rco_pass",
        "message": "malformed PR must not match across tasks",
        "payload": {"pr": malformed_pr, "head": "c" * 40},
    }

    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[request, closure],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 1
    assert report["bridge_next_action"]["open_incoming_count"] == 1


def test_wake_ack_does_not_close_underlying_direct_request(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            _request(),
            {
                "ts_utc": "2026-06-14T12:02:00Z",
                "agent": "claude-rco-2",
                "to": "codex-tools-1",
                "type": "message",
                "task_id": "pr1208-rco-pass",
                "status": "wake_ack_corrected_rco_pass_already_posted_clear_to_merge",
                "message": "Wake bit consumed; not a new RCO_PASS.",
                "payload": {"pr": 1208, "head": "c" * 40},
            },
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "direct_rco_pass_block_request_ready"
    assert report["direct_pass_block_request_count"] == 1
    assert report["request_closure_rule"].startswith("wake acknowledgements")


def test_real_rco_pass_closes_direct_request(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            _request(),
            {
                "ts_utc": "2026-06-14T12:03:00Z",
                "agent": "claude-rco-2",
                "to": "codex-tools-1,codex-lead-1",
                "type": "decision",
                "task_id": "pr1208-rco-pass",
                "status": "rco_pass",
                "message": "RCO_PASS PR #1208 at exact head.",
                "payload": {"pr": 1208, "head": "c" * 40},
            },
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "rco_ready_no_direct_pass_block_request"
    assert report["direct_pass_block_request_count"] == 0
    assert report["highest_priority_request"] == {}


def test_pass_words_in_done_or_decision_do_not_create_direct_requests(
    tmp_path: Path,
) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            {
                "ts_utc": "2026-06-14T12:00:00Z",
                "agent": "codex-tools-1",
                "to": "claude-rco-2",
                "type": "done",
                "task_id": "old-merged",
                "status": "merged_observed",
                "message": "RCO_PASS was present before merge.",
            },
            {
                "ts_utc": "2026-06-14T12:01:00Z",
                "agent": "codex-tools-1",
                "to": "claude-rco-2",
                "type": "decision",
                "task_id": "tools-pass",
                "status": "build_consensus_pass",
                "message": "Needs no RCO action from this event.",
            },
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["decision"] == "rco_ready_no_direct_pass_block_request"
    assert report["direct_pass_block_request_count"] == 0


def test_stale_direct_requests_are_filtered_by_default(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-2",
        events=[
            _request(ts="2026-06-13T00:00:00Z", task_id="stale-pass"),
            _request(ts="2026-06-14T12:00:00Z", task_id="fresh-pass"),
        ],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert report["direct_pass_block_request_count"] == 1
    assert report["highest_priority_request"]["task_id"] == "fresh-pass"
    assert report["max_age_hours"] == 12.0


def test_invalid_non_rco_agent_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RcoReadinessError) as excinfo:
        build_rco_readiness_report(
            agent="codex-tools-1",
            events=[],
            bridge_root=tmp_path,
            claims=[],
            now_utc=_now(),
        )

    assert "agent must be an RCO lane" in excinfo.value.report["errors"]


def test_authority_gates_are_false(tmp_path: Path) -> None:
    report = build_rco_readiness_report(
        agent="claude-rco-1",
        events=[],
        bridge_root=tmp_path,
        claims=[],
        now_utc=_now(),
    )

    assert CLAIM_GATES
    for gate in CLAIM_GATES:
        assert report[gate] is False
    assert report["authority_boundary"]["bridge_append_allowed"] is False
    assert report["authority_boundary"]["wake_file_consume_allowed"] is False
    assert report["authority_boundary"]["rco_pass_emit_allowed"] is False


def test_cli_json_uses_runtime_bridge_without_leaking_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge_root = tmp_path / "runtime-bridge"
    _events_file(bridge_root, [_request()])
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--agent",
            "claude-rco-2",
            "--now",
            "2026-06-14T12:10:00Z",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["decision"] == "direct_rco_pass_block_request_ready"
    assert report["local_paths_recorded"] is False
    assert str(bridge_root) not in serialized
    assert "events.jsonl" not in serialized


def test_cli_returns_error_for_non_rco_agent(tmp_path: Path, capsys) -> None:
    _events_file(tmp_path, [])

    rc = main(
        [
            "--agent",
            "codex-tools-1",
            "--bridge-root",
            str(tmp_path),
            "--json",
        ]
    )

    assert rc == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "rco_readiness_report_error"

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "report_unanswered_bridge_requests.py"

sys.path.insert(0, str(ROOT))

import tools.report_unanswered_bridge_requests as unanswered_report_module  # noqa: E402
from tools.report_unanswered_bridge_requests import (  # noqa: E402
    UnansweredRequestError,
    report_unanswered_requests,
)


def _request(
    *,
    task_id: str = "task-1",
    ts: str = "2026-06-13T12:00:00Z",
    agent: str = "codex-tools-1",
    to: str = "claude-rco-1",
    status: str = "rco_requested",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": to,
        "type": "wake_request",
        "task_id": task_id,
        "status": status,
        "message": "Please answer this visible request",
        "payload": {"head": "a" * 40, "pr": 1122},
    }


def _answer(
    *,
    task_id: str = "task-1",
    ts: str = "2026-06-13T12:08:00Z",
    agent: str = "claude-rco-1",
    status: str = "rco_pass",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": "codex-tools-1",
        "type": "decision",
        "task_id": task_id,
        "status": status,
        "message": "RCO_PASS at exact head " + ("a" * 40),
    }


def _now() -> datetime:
    return datetime(2026, 6, 13, 12, 20, tzinfo=timezone.utc)


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    events_path = path / "events.jsonl"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_reports_visible_request_until_target_answers() -> None:
    report = report_unanswered_requests(
        events=[_request()],
        now_utc=_now(),
        min_age_minutes=12,
    )

    assert report["ok"] is True
    assert report["unanswered_count"] == 1
    assert report["by_agent"] == {"claude-rco-1": 1}
    row = report["requests"][0]
    assert row["target_agent"] == "claude-rco-1"
    assert row["requester"] == "codex-tools-1"
    assert row["task_id"] == "task-1"
    assert row["bridge_visible"] is True
    assert row["response_expected_from"] == "claude-rco-1"
    assert row["first_ts_utc"] == "2026-06-13T12:00:00Z"
    assert row["ts_utc"] == "2026-06-13T12:00:00Z"
    assert row["age_minutes"] == 20.0
    assert row["latest_request_age_minutes"] == 20.0
    assert row["request_count"] == 1
    assert row["head"] == "a" * 40
    assert row["pr"] == "1122"
    assert report["pressure"] == {
        "oldest_age_minutes": 20.0,
        "newest_age_minutes": 20.0,
        "target_agent_count": 1,
        "bridge_visible_request_count": 1,
        "requester_counts": {"codex-tools-1": 1},
        "status_counts": {"rco_requested": 1},
        "by_agent_oldest_age_minutes": {"claude-rco-1": 20.0},
        "oldest_request": {
            "target_agent": "claude-rco-1",
            "requester": "codex-tools-1",
            "task_id": "task-1",
            "status": "rco_requested",
            "first_ts_utc": "2026-06-13T12:00:00Z",
            "ts_utc": "2026-06-13T12:00:00Z",
            "age_minutes": 20.0,
            "latest_request_age_minutes": 20.0,
            "request_count": 1,
            "head": "a" * 40,
            "pr": "1122",
        },
    }


def test_pressure_summary_groups_visible_stalls_without_paths() -> None:
    report = report_unanswered_requests(
        events=[
            _request(
                task_id="old-rco",
                ts="2026-06-13T12:00:00Z",
                to="claude-rco-1",
                status="rco_requested",
            ),
            _request(
                task_id="new-rco",
                ts="2026-06-13T12:05:00Z",
                agent="codex-lead-1",
                to="claude-rco-2",
                status="exact_head_review_requested",
            ),
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    pressure = report["pressure"]
    assert pressure["oldest_age_minutes"] == 20.0
    assert pressure["newest_age_minutes"] == 15.0
    assert pressure["target_agent_count"] == 2
    assert pressure["bridge_visible_request_count"] == 2
    assert pressure["requester_counts"] == {"codex-lead-1": 1, "codex-tools-1": 1}
    assert pressure["status_counts"] == {
        "exact_head_review_requested": 1,
        "rco_requested": 1,
    }
    assert pressure["by_agent_oldest_age_minutes"] == {
        "claude-rco-1": 20.0,
        "claude-rco-2": 15.0,
    }
    assert pressure["oldest_request"] == {
        "target_agent": "claude-rco-1",
        "requester": "codex-tools-1",
        "task_id": "old-rco",
        "status": "rco_requested",
        "first_ts_utc": "2026-06-13T12:00:00Z",
        "ts_utc": "2026-06-13T12:00:00Z",
        "age_minutes": 20.0,
        "latest_request_age_minutes": 20.0,
        "request_count": 1,
        "head": "a" * 40,
        "pr": "1122",
    }


def test_target_answer_closes_request() -> None:
    report = report_unanswered_requests(
        events=[_request(), _answer()],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 0
    assert report["requests"] == []


def test_requester_terminal_event_closes_request() -> None:
    report = report_unanswered_requests(
        events=[
            _request(),
            _answer(agent="codex-tools-1", status="done"),
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 0


def test_third_party_terminal_same_task_event_does_not_close_request() -> None:
    report = report_unanswered_requests(
        events=[
            _request(
                agent="codex-tools-1",
                to="operator",
                task_id="codex-tools-1-bridge-unanswered-request-diagnostics-20260613",
                status="pr_open_ci_pending",
            ),
            _answer(
                agent="codex-lead-1",
                task_id="codex-tools-1-bridge-unanswered-request-diagnostics-20260613",
                status="merged",
            )
            | {"type": "done"},
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1


def test_third_party_terminal_same_pr_event_does_not_close_request() -> None:
    terminal = _answer(
        agent="codex-lead-1",
        task_id="different-closeout-task",
        status="merged",
    )
    terminal["type"] = "done"
    terminal["payload"] = {"pr": 1122}

    report = report_unanswered_requests(
        events=[
            _request(
                agent="codex-tools-1",
                to="claude-rco-2",
                task_id="codex-tools-1-bridge-suppressed-lane-nudge-hygiene-20260613",
                status="pr_open_ci_pending",
            ),
            terminal,
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1


def test_third_party_autonomous_merge_receipt_does_not_close_request() -> None:
    merge_receipt = _answer(
        agent="claude-rco-1",
        task_id="codex-tools-1/bridge-session-titlemap-hint-20260615",
        status="autonomous_merge_receipt",
    )
    merge_receipt["payload"] = {"pr": 1233}

    report = report_unanswered_requests(
        events=[
            _request(
                agent="codex-tools-1",
                to="codex-lead-1",
                task_id="codex-tools-1/bridge-session-titlemap-hint-20260615",
                status="full_consensus_driver_ready",
            )
            | {"type": "handoff", "payload": {"pr": 1233}},
            merge_receipt,
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1


def test_prior_third_party_receipt_does_not_suppress_later_request() -> None:
    merge_receipt = _answer(
        agent="claude-rco-1",
        task_id="codex-tools-1/bridge-session-titlemap-hint-20260615",
        ts="2026-06-13T12:07:00Z",
        status="autonomous_merge_receipt",
    )
    merge_receipt["payload"] = {"pr": 1233}

    report = report_unanswered_requests(
        events=[
            merge_receipt,
            _request(
                agent="codex-tools-1",
                to="codex-lead-1",
                task_id="codex-tools-1/bridge-session-titlemap-hint-20260615",
                ts="2026-06-13T12:08:00Z",
                status="full_consensus_driver_ready",
            )
            | {"type": "handoff", "payload": {"pr": 1233}},
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1


def test_target_autonomous_merge_receipt_closes_request() -> None:
    merge_receipt = _answer(
        agent="codex-lead-1",
        task_id="codex-tools-1/bridge-session-titlemap-hint-20260615",
        status="autonomous_merge_receipt",
    )
    merge_receipt["payload"] = {"pr": 1233}

    report = report_unanswered_requests(
        events=[
            _request(
                agent="codex-tools-1",
                to="codex-lead-1",
                task_id="codex-tools-1/bridge-session-titlemap-hint-20260615",
                status="full_consensus_driver_ready",
            )
            | {"type": "handoff", "payload": {"pr": 1233}},
            merge_receipt,
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 0


@pytest.mark.parametrize(
    ("request_identity", "closure_identity", "closed"),
    [
        (
            {"agent_uuid": "UUID-A", "session_id": "session-a"},
            {"agent_uuid": "uuid-a", "session_id": "session-a"},
            True,
        ),
        (
            {"agent_uuid": "uuid-a", "session_id": "session-a"},
            {"agent_uuid": "uuid-a", "session_id": "session-b"},
            False,
        ),
        (
            {"agent_uuid": "uuid-a", "session_id": "session-a"},
            {"agent_uuid": "uuid-b", "session_id": "session-a"},
            False,
        ),
        (
            {"agent_uuid": "uuid-a", "session_id": "session-a"},
            {},
            False,
        ),
        ({}, {"agent_uuid": "uuid-a", "session_id": "session-a"}, True),
    ],
)
def test_requester_terminal_close_is_bound_to_request_identity(
    request_identity: dict[str, str],
    closure_identity: dict[str, str],
    closed: bool,
) -> None:
    request = _request(agent="codex-tools-1", to="claude-rco-1")
    request.update(request_identity)
    closure = _answer(agent="codex-tools-1", status="done")
    closure.update(closure_identity)

    report = report_unanswered_requests(
        events=[request, closure],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == (0 if closed else 1)


@pytest.mark.parametrize(
    ("closure_identity", "closed"),
    [
        ({"agent_uuid": "uuid-a", "session_id": "session-a"}, True),
        ({"agent_uuid": "uuid-a", "session_id": "session-b"}, False),
    ],
)
def test_requester_merge_receipt_is_bound_to_request_identity(
    closure_identity: dict[str, str],
    closed: bool,
) -> None:
    request = _request(agent="codex-tools-1", to="claude-rco-1")
    request.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    receipt = _answer(agent="codex-tools-1", status="autonomous_merge_receipt")
    receipt.update(closure_identity)

    report = report_unanswered_requests(
        events=[request, receipt],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == (0 if closed else 1)


def test_same_task_requests_from_distinct_sessions_remain_independent() -> None:
    first = _request(agent="codex-tools-1", to="claude-rco-1")
    first.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    second = _request(
        agent="codex-tools-1",
        to="claude-rco-1",
        ts="2026-06-13T12:01:00Z",
    )
    second.update({"agent_uuid": "uuid-a", "session_id": "session-b"})
    close_second = _answer(
        agent="codex-tools-1",
        ts="2026-06-13T12:02:00Z",
        status="superseded",
    )
    close_second.update({"agent_uuid": "uuid-a", "session_id": "session-b"})

    report = report_unanswered_requests(
        events=[first, second, close_second],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["ts_utc"] == "2026-06-13T12:00:00Z"


def test_closing_one_legacy_requester_keeps_other_requester_open() -> None:
    first = _request(agent="codex-tools-1", to="claude-rco-1")
    second = _request(
        agent="codex-lead-1",
        to="claude-rco-1",
        ts="2026-06-13T12:01:00Z",
    )
    close_second = _answer(
        agent="codex-lead-1",
        ts="2026-06-13T12:02:00Z",
        status="superseded",
    )

    report = report_unanswered_requests(
        events=[first, second, close_second],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["requester"] == "codex-tools-1"


@pytest.mark.parametrize("identity_mode", ["different_agent", "different_session"])
def test_future_dated_identity_does_not_hide_current_open_request(
    identity_mode: str,
) -> None:
    future = _request(
        agent="codex-tools-1",
        to="claude-rco-1",
        ts="2099-06-13T12:00:00Z",
    )
    current_agent = (
        "codex-lead-1" if identity_mode == "different_agent" else "codex-tools-1"
    )
    current = _request(
        agent=current_agent,
        to="claude-rco-1",
        ts="2026-06-13T12:01:00Z",
    )
    if identity_mode == "different_session":
        future.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
        current.update({"agent_uuid": "uuid-a", "session_id": "session-b"})

    report = report_unanswered_requests(
        events=[future, current],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["ts_utc"] == "2026-06-13T12:01:00Z"
    assert report["requests"][0]["request_count"] == 1


@pytest.mark.parametrize(
    "identity_mode",
    ["different_agent", "different_session", "same_identity"],
)
def test_default_wall_clock_rejects_future_request_without_hiding_current(
    monkeypatch: pytest.MonkeyPatch,
    identity_mode: str,
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:
            value = _now()
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(unanswered_report_module, "datetime", FixedDatetime)
    future = _request(
        agent="codex-tools-1",
        to="claude-rco-1",
        ts="2099-06-13T12:00:00Z",
    )
    current_agent = (
        "codex-lead-1" if identity_mode == "different_agent" else "codex-tools-1"
    )
    current = _request(
        agent=current_agent,
        to="claude-rco-1",
        ts="2026-06-13T12:01:00Z",
    )
    if identity_mode == "different_session":
        future.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
        current.update({"agent_uuid": "uuid-a", "session_id": "session-b"})

    report = report_unanswered_requests(
        events=[future, current],
        min_age_minutes=12,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["requester"] == current["agent"]
    assert report["requests"][0]["ts_utc"] == "2026-06-13T12:01:00Z"
    assert report["requests"][0]["request_count"] == 1


def test_backdated_repeat_cannot_expire_current_identity() -> None:
    current = _request(ts="2026-06-13T12:10:00Z")
    backdated = _request(ts="2026-06-12T12:00:00Z")

    report = report_unanswered_requests(
        events=[current, backdated],
        now_utc=_now(),
        min_age_minutes=0,
        max_age_hours=12,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["ts_utc"] == "2026-06-13T12:10:00Z"
    assert report["requests"][0]["latest_request_age_minutes"] == 10.0
    assert report["requests"][0]["request_count"] == 2


@pytest.mark.parametrize("closer", ["requester", "target"])
def test_prior_future_dated_closure_does_not_close_later_appended_request(
    closer: str,
) -> None:
    request = _request(agent="codex-tools-1", to="claude-rco-1")
    request.update({"agent_uuid": "uuid-a", "session_id": "session-a"})
    closure = _answer(
        agent="codex-tools-1" if closer == "requester" else "claude-rco-1",
        ts="2099-06-13T12:08:00Z",
        status="superseded" if closer == "requester" else "answered",
    )
    if closer == "requester":
        closure.update({"agent_uuid": "uuid-a", "session_id": "session-a"})

    report = report_unanswered_requests(
        events=[closure, request],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1


def test_tail_replayed_closure_with_older_timestamp_keeps_request_open() -> None:
    request = _request(
        agent="codex-tools-1",
        to="claude-rco-1",
        ts="2026-06-13T12:08:00Z",
    )
    answer = _answer(
        agent="claude-rco-1",
        ts="2026-06-13T12:00:00Z",
        status="answered",
    )

    report = report_unanswered_requests(
        events=[request, answer],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1


def test_later_appended_closure_with_later_timestamp_closes_request() -> None:
    request = _request(
        agent="codex-tools-1",
        to="claude-rco-1",
        ts="2026-06-13T12:08:00Z",
    )
    answer = _answer(
        agent="claude-rco-1",
        ts="2026-06-13T12:09:00Z",
        status="answered",
    )

    report = report_unanswered_requests(
        events=[request, answer],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 0


def test_delayed_replay_receipt_does_not_close_renewed_request() -> None:
    request = _request(
        agent="codex-tools-1",
        to="claude-rco-1",
        ts="2026-06-13T12:00:00Z",
    )
    renewal = _request(
        agent="codex-tools-1",
        to="claude-rco-1",
        ts="2026-06-13T12:02:00Z",
    )
    replayed_answer = _answer(
        agent="claude-rco-1",
        ts="2026-06-13T12:01:00Z",
        status="answered",
    )

    report = report_unanswered_requests(
        events=[request, renewal, replayed_answer],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1

    settled = report_unanswered_requests(
        events=[request, replayed_answer],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert settled["unanswered_count"] == 0


@pytest.mark.parametrize("invalid_pr", [True, 1.5])
def test_malformed_pr_does_not_create_cross_task_closure(invalid_pr: object) -> None:
    request = _request(agent="codex-tools-1", to="claude-rco-1")
    request["task_id"] = "request-task"
    request["payload"] = {"pr": invalid_pr}
    answer = _answer(agent="claude-rco-1", status="answered")
    answer["task_id"] = "different-task"
    answer["payload"] = {"pr": invalid_pr}

    report = report_unanswered_requests(
        events=[request, answer],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1


def test_prior_closure_does_not_suppress_later_non_pr_wake_request() -> None:
    answered = _answer(
        task_id="operator-wake-request-20260615",
        ts="2026-06-13T12:05:00Z",
        agent="codex-lead-1",
        status="answered",
    )

    report = report_unanswered_requests(
        events=[
            _request(
                agent="operator",
                to="codex-lead-1",
                task_id="operator-wake-request-20260615",
                ts="2026-06-13T12:00:00Z",
                status="open",
            )
            | {"payload": {}},
            answered,
            _request(
                agent="operator",
                to="codex-lead-1",
                task_id="operator-wake-request-20260615",
                ts="2026-06-13T12:10:00Z",
                status="open",
            )
            | {"payload": {}},
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["task_id"] == "operator-wake-request-20260615"
    assert report["requests"][0]["age_minutes"] == 10.0


def test_bridge_follow_nudge_is_not_unanswered_pressure() -> None:
    report = report_unanswered_requests(
        events=[
            _request(
                agent="operator",
                to="codex-lead-1",
                task_id="bridge-follow-nudge-20260615",
                ts="2026-06-13T12:00:00Z",
                status="open",
            )
            | {"payload": {}},
            _request(
                agent="operator",
                to="codex-lead-1",
                task_id="bridge-follow-nudge-20260615",
                ts="2026-06-13T12:10:00Z",
                status="open",
            )
            | {"payload": {}},
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 0
    assert report["by_agent"] == {}
    assert report["requests"] == []


def test_non_terminal_third_party_event_does_not_close_request() -> None:
    report = report_unanswered_requests(
        events=[
            _request(
                agent="codex-tools-1",
                to="claude-rco-1",
                task_id="task-needs-rco",
                status="rco_requested",
            ),
            _answer(
                agent="codex-lead-1",
                task_id="task-needs-rco",
                status="build_consensus_pass",
            ),
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["response_expected_from"] == "claude-rco-1"


def test_min_age_filters_young_request() -> None:
    report = report_unanswered_requests(
        events=[_request(ts="2026-06-13T12:15:00Z")],
        now_utc=_now(),
        min_age_minutes=12,
    )

    assert report["unanswered_count"] == 0


def test_default_max_age_filters_historical_noise() -> None:
    report = report_unanswered_requests(
        events=[_request(ts="2026-05-23T12:00:00Z")],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 0
    assert report["max_age_hours"] == 12.0


def test_max_age_can_include_full_tail() -> None:
    report = report_unanswered_requests(
        events=[_request(ts="2026-05-23T12:00:00Z")],
        now_utc=_now(),
        min_age_minutes=0,
        max_age_hours=0,
    )

    assert report["unanswered_count"] == 1
    assert report["max_age_hours"] is None


def test_repeated_wake_request_preserves_first_open_age() -> None:
    report = report_unanswered_requests(
        events=[
            _request(ts="2026-06-13T12:00:00Z"),
            _request(ts="2026-06-13T12:10:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["age_minutes"] == 20.0
    assert report["requests"][0]["latest_request_age_minutes"] == 10.0
    assert report["requests"][0]["first_ts_utc"] == "2026-06-13T12:00:00Z"
    assert report["requests"][0]["ts_utc"] == "2026-06-13T12:10:00Z"
    assert report["requests"][0]["request_count"] == 2


def test_repeated_wake_request_min_age_uses_first_open_age() -> None:
    report = report_unanswered_requests(
        events=[
            _request(ts="2026-06-13T12:00:00Z"),
            _request(ts="2026-06-13T12:10:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=12,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["age_minutes"] == 20.0
    assert report["requests"][0]["latest_request_age_minutes"] == 10.0


def test_repeated_task_from_different_requester_uses_latest_request() -> None:
    report = report_unanswered_requests(
        events=[
            _request(
                agent="codex-lead-1",
                ts="2026-06-13T12:00:00Z",
                task_id="codex-tools-1-task",
            ),
            _request(
                agent="codex-tools-1",
                ts="2026-06-13T12:10:00Z",
                task_id="codex-tools-1-task",
            ),
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["requester"] == "codex-tools-1"
    assert report["requests"][0]["age_minutes"] == 20.0
    assert report["requests"][0]["latest_request_age_minutes"] == 10.0
    assert report["requests"][0]["first_ts_utc"] == "2026-06-13T12:00:00Z"
    assert report["requests"][0]["request_count"] == 2


def test_slash_task_alias_answer_closes_hyphen_request() -> None:
    report = report_unanswered_requests(
        events=[
            _request(task_id="codex-tools-1-bridge-task"),
            _answer(task_id="codex-tools-1/bridge-task"),
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 0


def test_agent_filter_limits_targets() -> None:
    report = report_unanswered_requests(
        events=[
            _request(task_id="rco", to="claude-rco-1"),
            _request(task_id="lead", to="codex-lead-1", status="build_consensus_requested"),
        ],
        agents=["codex-lead-1"],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["target_agent"] == "codex-lead-1"
    assert report["requests"][0]["task_id"] == "lead"


def test_rejects_invalid_agent_filter() -> None:
    with pytest.raises(UnansweredRequestError) as excinfo:
        report_unanswered_requests(
            events=[],
            agents=["../codex"],
            now_utc=_now(),
        )

    assert excinfo.value.report["decision"] == "unanswered_bridge_requests_error"
    assert "agent must match" in excinfo.value.report["errors"][0]


def test_cli_json_reports_unanswered_request(tmp_path: Path) -> None:
    events_path = _events_file(tmp_path, [_request()])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--now",
            "2026-06-13T12:20:00Z",
            "--min-age-minutes",
            "12",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is True
    assert report["unanswered_count"] == 1
    assert report["requests"][0]["task_id"] == "task-1"
    assert report["events_path_recorded"] is False
    assert report["local_paths_recorded"] is False
    assert str(events_path) not in serialized
    assert "events.jsonl" not in serialized


def test_cli_defaults_to_runtime_bridge_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge_root = tmp_path / "runtime-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(json.dumps(_request()) + "\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--now",
            "2026-06-13T12:20:00Z",
            "--min-age-minutes",
            "12",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is True
    assert report["unanswered_count"] == 1
    assert report["requests"][0]["task_id"] == "task-1"
    assert str(bridge_root) not in serialized
    assert "events.jsonl" not in serialized


def test_cli_explicit_events_overrides_runtime_bridge_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_events = tmp_path / "runtime-bridge" / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    runtime_events.write_text(
        json.dumps(_request(task_id="runtime")) + "\n", encoding="utf-8"
    )
    explicit_root = tmp_path / "explicit"
    explicit_root.mkdir()
    explicit_events = _events_file(explicit_root, [_request(task_id="explicit")])
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_events.parents[1]))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(explicit_events),
            "--now",
            "2026-06-13T12:20:00Z",
            "--min-age-minutes",
            "12",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    assert report["unanswered_count"] == 1
    assert report["requests"][0]["task_id"] == "explicit"

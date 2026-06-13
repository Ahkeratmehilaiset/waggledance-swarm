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
    assert row["head"] == "a" * 40
    assert row["pr"] == "1122"


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


def test_repeated_wake_request_uses_latest_age() -> None:
    report = report_unanswered_requests(
        events=[
            _request(ts="2026-06-13T12:00:00Z"),
            _request(ts="2026-06-13T12:10:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=0,
    )

    assert report["unanswered_count"] == 1
    assert report["requests"][0]["age_minutes"] == 10.0
    assert report["requests"][0]["ts_utc"] == "2026-06-13T12:10:00Z"


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
    assert report["requests"][0]["age_minutes"] == 10.0


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

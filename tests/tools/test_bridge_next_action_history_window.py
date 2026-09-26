"""A row tail that outgrows the byte budget may shrink only to a window whose
timestamps prove it still covers every age window next-action decides on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import tools.bridge_next_action as bridge_next_action
from tools.bridge_next_action import BridgeNextActionError, main, read_events

NOW = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)
ROWS = 64
FITTING_ROWS = 16  # 64 -> 32 -> 16 under the budget set below


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _filler(ts: datetime, index: int) -> dict[str, object]:
    return {
        "ts_utc": _ts(ts),
        "agent": "claude",
        "to": "someone-else",
        "type": "message",
        "task_id": f"filler-{index:03d}",
        "status": "informational",
        "message": "x" * 120,
    }


def _request(ts: datetime) -> dict[str, object]:
    return {
        "ts_utc": _ts(ts),
        "agent": "claude",
        "to": "codex",
        "type": "message",
        "task_id": "new-task",
        "status": "request",
        "message": "new request",
    }


def _write_log(tmp_path: Path, spacing: timedelta, *, first_window_ts=None) -> Path:
    """ROWS rows ending with a fresh request to codex, one row per ``spacing``."""

    bridge = tmp_path / ".agent-bridge"
    events_path = bridge / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events = [
        _filler(NOW - spacing * (ROWS - index), index) for index in range(ROWS - 1)
    ]
    events.append(_request(NOW - timedelta(minutes=5)))
    if first_window_ts is not None:
        events[ROWS - FITTING_ROWS] = _filler(first_window_ts, 999)
    rows = [json.dumps(event, sort_keys=True) for event in events]
    events_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return events_path


def _budget_for_last_rows(events_path: Path, rows: int) -> int:
    lines = events_path.read_bytes().splitlines(keepends=True)
    # The reader needs the LF that ends the row before the window, too.
    return sum(len(line) for line in lines[-rows:]) + 1


def _run(events_path: Path, capsys) -> tuple[int, dict[str, object]]:
    exit_code = main(
        [
            "--agent",
            "codex",
            "--bridge-root",
            str(events_path.parent.parent),
            "--events",
            str(events_path),
            "--tail",
            str(ROWS),
            "--now",
            _ts(NOW),
            "--json",
        ]
    )
    return exit_code, json.loads(capsys.readouterr().out)


@pytest.fixture
def small_budget(monkeypatch):
    def apply(events_path: Path) -> None:
        monkeypatch.setattr(
            bridge_next_action,
            "MAX_MAX_BYTES",
            _budget_for_last_rows(events_path, FITTING_ROWS),
        )

    monkeypatch.setattr(bridge_next_action, "HISTORY_MIN_FALLBACK_ROWS", 4)
    monkeypatch.setattr(bridge_next_action, "HISTORY_COVERAGE_PROBE_ROWS", 3)
    return apply


def test_covering_window_recommends_and_reports_the_window(
    tmp_path: Path, small_budget, capsys
) -> None:
    # 16 rows ten hours apart reach back 160h; the tool needs 72h + 24h skew.
    events_path = _write_log(tmp_path, timedelta(hours=10))
    small_budget(events_path)

    exit_code, report = _run(events_path, capsys)

    assert exit_code == 0
    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "new-task"
    window = report["history_window"]
    assert window["mode"] == "time_covered"
    assert window["requested_rows"] == ROWS
    assert window["selected_rows"] == FITTING_ROWS
    assert window["required_start_utc"] == _ts(NOW - timedelta(hours=96))
    assert window["window_start_utc"] <= window["required_start_utc"]


def test_same_log_within_budget_is_an_ordinary_row_tail(
    tmp_path: Path, small_budget, capsys
) -> None:
    # Success twin of the test above: nothing shrinks, nothing is reported.
    events_path = _write_log(tmp_path, timedelta(hours=10))

    exit_code, report = _run(events_path, capsys)

    assert exit_code == 0
    assert report["action"] == "answer_incoming"
    assert "history_window" not in report


def test_window_too_short_fails_closed_without_a_recommendation(
    tmp_path: Path, small_budget, capsys
) -> None:
    # 16 rows one hour apart reach back only 16h: not proof of 96h.
    events_path = _write_log(tmp_path, timedelta(hours=1))
    small_budget(events_path)

    exit_code, report = _run(events_path, capsys)

    assert exit_code == 2
    assert report["ok"] is False
    assert report["decision"] == "bridge_next_action_error"
    assert "action" not in report
    (error,) = report["errors"]
    assert "tail_exceeds_max_bytes" in error
    assert f"after the required {_ts(NOW - timedelta(hours=96))}" in error


def test_one_old_row_at_the_window_start_cannot_fake_coverage(
    tmp_path: Path, small_budget, capsys
) -> None:
    # A late-appended old row (a spool restore) lands first in a short window.
    events_path = _write_log(
        tmp_path,
        timedelta(hours=1),
        first_window_ts=NOW - timedelta(days=400),
    )
    small_budget(events_path)

    exit_code, report = _run(events_path, capsys)

    assert exit_code == 2
    assert "tail_exceeds_max_bytes" in report["errors"][0]


def test_unparseable_window_timestamps_fail_closed(
    tmp_path: Path, small_budget, monkeypatch, capsys
) -> None:
    events_path = _write_log(tmp_path, timedelta(hours=10))
    small_budget(events_path)
    monkeypatch.setattr(bridge_next_action, "_parse_utc", lambda value: None)

    exit_code, report = _run(events_path, capsys)

    assert exit_code == 2
    assert "starts at an unknown time" in report["errors"][0]


def test_shrinking_stops_at_the_minimum_row_floor(
    tmp_path: Path, small_budget, monkeypatch, capsys
) -> None:
    events_path = _write_log(tmp_path, timedelta(hours=10))
    small_budget(events_path)
    monkeypatch.setattr(bridge_next_action, "HISTORY_MIN_FALLBACK_ROWS", ROWS)

    exit_code, report = _run(events_path, capsys)

    assert exit_code == 2
    assert report["errors"] == [
        "bridge event snapshot unavailable: tail_exceeds_max_bytes"
    ]


def test_library_read_events_keeps_failing_closed(
    tmp_path: Path, small_budget
) -> None:
    # read_events has no age windows to prove, so it never shrinks.
    events_path = _write_log(tmp_path, timedelta(hours=10))
    small_budget(events_path)

    with pytest.raises(BridgeNextActionError, match="tail_exceeds_max_bytes"):
        read_events(events_path, tail=ROWS)


def test_other_blocked_reasons_never_shrink(
    tmp_path: Path, small_budget, monkeypatch, capsys
) -> None:
    from waggledance.core.bridge_log_reader import (
        BridgeLineReadResult,
        BridgeReadStatus,
    )

    events_path = _write_log(tmp_path, timedelta(hours=10))
    requested: list[int] = []

    def blocked(path, rows):
        requested.append(rows)
        return BridgeLineReadResult(BridgeReadStatus.BLOCKED, "log_bom")

    monkeypatch.setattr(bridge_next_action, "_read_tail_snapshot", blocked)

    exit_code, report = _run(events_path, capsys)

    assert exit_code == 2
    assert report["errors"] == ["bridge event snapshot unavailable: log_bom"]
    assert requested == [ROWS]

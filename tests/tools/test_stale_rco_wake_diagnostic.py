# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "stale_rco_wake_diagnostic.py"

sys.path.insert(0, str(ROOT))

from tools.stale_rco_wake_diagnostic import (  # noqa: E402
    StaleRcoWakeError,
    report_stale_rco_wakes,
)


def _event(
    *,
    ts: str,
    agent: str = "claude-rco-2",
    event_type: str = "message",
    status: str = "active",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": f"{agent}-session",
        "status": status,
        "message": "activity",
    }


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    events_path = path / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events)
        + ("\n" if events else ""),
        encoding="utf-8",
    )
    return events_path


def _write_wake(path: Path, agent: str, mtime: datetime) -> Path:
    wake_path = path / f"wake_{agent}"
    wake_path.parent.mkdir(parents=True, exist_ok=True)
    wake_path.write_text("wake", encoding="utf-8")
    timestamp = mtime.timestamp()
    os.utime(wake_path, (timestamp, timestamp))
    return wake_path


def _now() -> datetime:
    return datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc)


def test_reports_old_rco_wake_without_later_activity(tmp_path: Path) -> None:
    _write_wake(
        tmp_path,
        "claude-rco-2",
        datetime(2026, 6, 14, 19, 30, tzinfo=timezone.utc),
    )

    report = report_stale_rco_wakes(
        events=[],
        bridge_root=tmp_path,
        agents=["claude-rco-2"],
        min_age_minutes=12,
        now_utc=_now(),
    )

    assert report["decision"] == "stale_rco_wake_detected"
    assert report["stale_count"] == 1
    assert report["by_agent"] == {"claude-rco-2": 1}
    assert report["authority_boundary"] == {
        "read_only": True,
        "does_not_consume_wake": True,
        "does_not_emit_rco_pass": True,
        "does_not_skip_gate": True,
    }
    escalation = report["delivery_escalation"]
    assert escalation["operator_action_required"] is True
    assert escalation["do_not_emit_additional_wake_requests"] is True
    row = report["stale_rco_wakes"][0]
    assert row["target_agent"] == "claude-rco-2"
    assert row["wake_file_present"] is True
    assert row["wake_age_minutes"] == 30.0
    assert row["stale"] is True
    assert "additional wake_request" in row["safe_next_action"]


def test_later_target_activity_clears_stale_wake(tmp_path: Path) -> None:
    _write_wake(
        tmp_path,
        "claude-rco-2",
        datetime(2026, 6, 14, 19, 30, tzinfo=timezone.utc),
    )

    report = report_stale_rco_wakes(
        events=[
            _event(ts="2026-06-14T19:45:00Z", agent="claude-rco-2"),
        ],
        bridge_root=tmp_path,
        agents=["claude-rco-2"],
        min_age_minutes=12,
        now_utc=_now(),
    )

    assert report["decision"] == "rco_wake_ok"
    assert report["stale_count"] == 0
    row = report["checked_rco_wakes"][0]
    assert row["wake_file_present"] is True
    assert row["stale"] is False
    assert row["last_target_activity_ts_utc"] == "2026-06-14T19:45:00Z"
    assert "after wake file mtime" in row["diagnosis"]


def test_heartbeat_after_wake_does_not_clear_stale_wake(tmp_path: Path) -> None:
    _write_wake(
        tmp_path,
        "claude-rco-2",
        datetime(2026, 6, 14, 19, 30, tzinfo=timezone.utc),
    )

    report = report_stale_rco_wakes(
        events=[
            _event(
                ts="2026-06-14T19:45:00Z",
                agent="claude-rco-2",
                event_type="heartbeat",
            ),
        ],
        bridge_root=tmp_path,
        agents=["claude-rco-2"],
        min_age_minutes=12,
        now_utc=_now(),
    )

    assert report["decision"] == "stale_rco_wake_detected"
    row = report["stale_rco_wakes"][0]
    assert row["last_target_activity_ts_utc"] == ""
    assert row["diagnosis"].startswith("stale wake file exists")


def test_absent_wake_file_is_ok(tmp_path: Path) -> None:
    report = report_stale_rco_wakes(
        events=[],
        bridge_root=tmp_path,
        agents=["claude-rco-1"],
        min_age_minutes=0,
        now_utc=_now(),
    )

    assert report["decision"] == "rco_wake_ok"
    assert report["stale_rco_wakes"] == []
    row = report["checked_rco_wakes"][0]
    assert row["wake_file_present"] is False
    assert row["diagnosis"] == "wake_file_absent"


def test_activity_gap_recommends_available_rco_failover(tmp_path: Path) -> None:
    report = report_stale_rco_wakes(
        events=[
            _event(ts="2026-06-14T19:55:00Z", agent="claude-rco-1"),
            _event(ts="2026-06-14T18:45:00Z", agent="claude-rco-2"),
        ],
        bridge_root=tmp_path,
        agents=["claude-rco-1", "claude-rco-2"],
        min_age_minutes=12,
        activity_gap_minutes=45,
        now_utc=_now(),
    )

    assert report["decision"] == "rco_wake_ok"
    assert report["activity_gap_count"] == 1
    recommendation = report["rco_failover_recommendation"]
    assert recommendation == {
        "required": True,
        "stale_activity_agents": ["claude-rco-2"],
        "available_activity_agents": ["claude-rco-1"],
        "operator_action_required": True,
        "safe_next_action": (
            "request_or_keep_review_with_available_recognized_rco_and_"
            "restart_stale_rco_bridge_session"
        ),
        "reason": "some_recognized_rco_lanes_have_activity_gap",
        "advisory_only": True,
        "no_authority_granted": True,
        "rco_absence_still_blocks_merge": True,
    }
    by_agent = {
        row["target_agent"]: row for row in report["checked_rco_wakes"]
    }
    assert by_agent["claude-rco-1"]["last_target_activity_age_minutes"] == 5.0
    assert by_agent["claude-rco-1"]["activity_gap_exceeded"] is False
    assert by_agent["claude-rco-2"]["last_target_activity_age_minutes"] == 75.0
    assert by_agent["claude-rco-2"]["activity_gap_exceeded"] is True


def test_activity_gap_all_rco_lanes_requires_restart_before_waiting(
    tmp_path: Path,
) -> None:
    report = report_stale_rco_wakes(
        events=[
            _event(ts="2026-06-14T18:40:00Z", agent="claude-rco-1"),
            _event(ts="2026-06-14T18:45:00Z", agent="claude-rco-2"),
        ],
        bridge_root=tmp_path,
        agents=["claude-rco-1", "claude-rco-2"],
        activity_gap_minutes=45,
        now_utc=_now(),
    )

    recommendation = report["rco_failover_recommendation"]
    assert recommendation["required"] is True
    assert recommendation["stale_activity_agents"] == [
        "claude-rco-1",
        "claude-rco-2",
    ]
    assert recommendation["available_activity_agents"] == []
    assert recommendation["safe_next_action"] == (
        "restart_or_verify_all_rco_bridge_session_watchers_before_"
        "waiting_for_review"
    )
    assert recommendation["reason"] == "all_checked_rco_lanes_have_activity_gap"
    assert recommendation["rco_absence_still_blocks_merge"] is True


def test_invalid_non_rco_agent_fails_closed(tmp_path: Path) -> None:
    try:
        report_stale_rco_wakes(
            events=[],
            bridge_root=tmp_path,
            agents=["codex-lead-1"],
        )
    except StaleRcoWakeError as exc:
        assert "agent must be an RCO lane" in exc.report["errors"][0]
    else:
        raise AssertionError("expected StaleRcoWakeError")


def test_invalid_activity_gap_fails_closed(tmp_path: Path) -> None:
    try:
        report_stale_rco_wakes(
            events=[],
            bridge_root=tmp_path,
            agents=["claude-rco-1"],
            activity_gap_minutes=-0.1,
        )
    except StaleRcoWakeError as exc:
        assert exc.report["errors"] == [
            "activity_gap_minutes must be non-negative"
        ]
    else:
        raise AssertionError("expected StaleRcoWakeError")


def test_cli_json_does_not_leak_wake_path_and_can_fail_on_stale(
    tmp_path: Path,
) -> None:
    _write_wake(
        tmp_path,
        "claude-rco-2",
        datetime(2026, 6, 14, 19, 30, tzinfo=timezone.utc),
    )
    events_path = _events_file(tmp_path, [])

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--bridge-root",
            str(tmp_path),
            "--agent",
            "claude-rco-2",
            "--now",
            "2026-06-14T20:00:00Z",
            "--fail-on-stale",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 3
    report = json.loads(result.stdout)
    assert report["decision"] == "stale_rco_wake_detected"
    assert report["stale_count"] == 1
    assert report["rco_failover_recommendation"]["advisory_only"] is True
    assert (
        report["rco_failover_recommendation"][
            "rco_absence_still_blocks_merge"
        ]
        is True
    )
    assert str(tmp_path) not in result.stdout
    assert "wake_claude-rco-2" not in result.stdout

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_bridge_wake_delivery.py"

sys.path.insert(0, str(ROOT))

from tools import check_bridge_wake_delivery as wake_delivery_module  # noqa: E402
from tools.check_bridge_wake_delivery import (  # noqa: E402
    WakeDeliveryError,
    check_wake_delivery,
)


def _wake(
    *,
    ts: str = "2026-06-13T12:00:00Z",
    agent: str = "operator",
    to: str = "claude-rco-2",
    task_id: str = "bridge-follow-nudge-20260613",
    status: str = "open",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": to,
        "type": "wake_request",
        "task_id": task_id,
        "status": status,
        "message": "read bridge and answer open requests",
    }


def _activity(
    *,
    ts: str = "2026-06-13T12:08:00Z",
    agent: str = "claude-rco-2",
    event_type: str = "liveness",
    status: str = "active",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": f"{agent}-session",
        "status": status,
        "message": "active",
    }


def _wake_send_failed(
    *,
    ts: str = "2026-06-13T12:06:00Z",
    target: str = "claude-rco-2",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": "operator",
        "type": "message",
        "task_id": "wd/ops/stall-rescue-watch",
        "status": "wake_send_failed",
        "message": (
            f"Keying '{target}' failed (tab not found / UIA error): "
            f"Tab for agent '{target}' not found. Tip: pass -TitleMap "
            f"'{target}=<exact-title-substring>'."
        ),
    }


def _now() -> datetime:
    return datetime(2026, 6, 13, 12, 30, tzinfo=timezone.utc)


def _set_mtime(path: Path, value: datetime) -> None:
    timestamp = value.timestamp()
    os.utime(path, (timestamp, timestamp))


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    shared = path / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_reports_repeated_wake_without_later_target_activity() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z"),
            _wake(ts="2026-06-13T12:10:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=12,
        min_repeats=2,
    )

    assert report["decision"] == "wake_delivery_stalled"
    assert report["stalled_count"] == 1
    assert report["by_agent"] == {"claude-rco-2": 1}
    escalation = report["delivery_escalation"]
    assert escalation == {
        "required": True,
        "target_agents": ["claude-rco-2"],
        "do_not_emit_additional_wake_requests": True,
        "safe_next_action": "restart_or_verify_target_agent_bridge_session_watcher",
        "operator_action_required": True,
        "reason": "wake_request_visible_but_no_later_target_bridge_activity",
        "diagnostic_next_action": (
            "run session_liveness_supervisor_report before writing more "
            "wake_requests"
        ),
        "diagnostic_commands": [
            {
                "target_agent": "claude-rco-2",
                "authority": "read_only_report_no_restart_no_gate_skip",
                "argv": [
                    "python",
                    "tools/session_liveness_supervisor_report.py",
                    "--agent",
                    "claude-rco-2",
                    "--json",
                ],
                "command": (
                    "python tools/session_liveness_supervisor_report.py "
                    "--agent claude-rco-2 --json"
                ),
            }
        ],
    }
    row = report["stalled_wakes"][0]
    assert row["target_agent"] == "claude-rco-2"
    assert row["wake_request_count"] == 3
    assert row["age_minutes"] == 30.0
    assert row["latest_wake_age_minutes"] == 20.0
    assert row["wake_file_checked"] is False
    assert "not be polling" in row["diagnosis"]


def test_target_activity_after_wake_clears_pending_group() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z"),
            _activity(ts="2026-06-13T12:06:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=0,
        min_repeats=1,
    )

    assert report["decision"] == "wake_delivery_ok"
    assert report["delivery_escalation"] == {
        "required": False,
        "target_agents": [],
        "do_not_emit_additional_wake_requests": False,
        "safe_next_action": "",
        "operator_action_required": False,
        "reason": "",
        "diagnostic_next_action": "",
        "diagnostic_commands": [],
    }
    assert report["stalled_wakes"] == []


def test_target_heartbeat_after_wake_does_not_clear_pending_group() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z"),
            _activity(
                ts="2026-06-13T12:06:00Z",
                event_type="heartbeat",
                status="active",
            ),
        ],
        now_utc=_now(),
        min_age_minutes=12,
        min_repeats=2,
    )

    assert report["decision"] == "wake_delivery_stalled"
    assert report["stalled_count"] == 1
    assert report["delivery_escalation"]["do_not_emit_additional_wake_requests"] is True
    assert report["delivery_escalation"]["safe_next_action"] == (
        "restart_or_verify_target_agent_bridge_session_watcher"
    )
    row = report["stalled_wakes"][0]
    assert row["target_agent"] == "claude-rco-2"
    assert row["wake_request_count"] == 2
    assert row["latest_wake_age_minutes"] == 25.0


def test_prior_target_self_liveness_does_not_suppress_restart_escalation() -> None:
    report = check_wake_delivery(
        events=[
            _activity(ts="2026-06-13T12:04:00Z", event_type="decision"),
            _wake(ts="2026-06-13T12:05:00Z"),
            _wake(ts="2026-06-13T12:10:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=12,
        min_repeats=2,
    )

    assert report["decision"] == "wake_delivery_stalled"
    assert report["stalled_count"] == 1
    assert report["delivery_escalation"]["required"] is True
    assert report["delivery_escalation"]["operator_action_required"] is True
    assert report["self_pacing_wake_count"] == 0
    row = report["stalled_wakes"][0]
    assert row["target_agent"] == "claude-rco-2"
    assert row["classification"] == "stalled_wake_delivery"
    assert row["wake_request_count"] == 2
    assert "last_self_activity_ts_utc" not in row
    assert "not be polling" in row["diagnosis"]


def test_later_wake_after_activity_starts_new_unresolved_window() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _activity(ts="2026-06-13T12:06:00Z"),
            _wake(ts="2026-06-13T12:10:00Z"),
            _wake(ts="2026-06-13T12:12:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=12,
        min_repeats=2,
        self_liveness_window_minutes=5,
    )

    assert report["stalled_count"] == 1
    row = report["stalled_wakes"][0]
    assert row["first_ts_utc"] == "2026-06-13T12:10:00Z"
    assert row["wake_request_count"] == 2
    assert row["age_minutes"] == 20.0
    assert row["latest_wake_age_minutes"] == 18.0


def test_frequent_repeated_wakes_still_age_from_unresolved_window() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:28:00Z"),
            _wake(ts="2026-06-13T12:29:30Z"),
        ],
        now_utc=_now(),
        min_age_minutes=12,
        min_repeats=2,
    )

    assert report["stalled_count"] == 1
    row = report["stalled_wakes"][0]
    assert row["age_minutes"] == 30.0
    assert row["latest_wake_age_minutes"] == 0.5


def test_min_repeats_filters_single_wake() -> None:
    report = check_wake_delivery(
        events=[_wake(ts="2026-06-13T12:00:00Z")],
        now_utc=_now(),
        min_age_minutes=0,
        min_repeats=2,
    )

    assert report["stalled_count"] == 0


def test_min_age_filters_young_wake_group() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:20:00Z"),
            _wake(ts="2026-06-13T12:22:00Z"),
        ],
        now_utc=_now(),
        min_age_minutes=12,
        min_repeats=2,
    )

    assert report["stalled_count"] == 0


def test_default_now_uses_wall_clock_not_latest_event(monkeypatch) -> None:
    monkeypatch.setattr(wake_delivery_module, "_utc_now", _now)

    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z"),
        ],
        min_age_minutes=12,
        min_repeats=2,
    )

    assert report["decision"] == "wake_delivery_stalled"
    assert report["stalled_count"] == 1
    row = report["stalled_wakes"][0]
    assert row["age_minutes"] == 30.0
    assert row["latest_wake_age_minutes"] == 25.0


def test_closed_wake_status_is_not_reported() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z", status="closed"),
        ],
        now_utc=_now(),
        min_age_minutes=0,
        min_repeats=1,
    )

    assert report["stalled_count"] == 0


def test_wake_file_presence_is_reported(tmp_path: Path) -> None:
    wake_file = tmp_path / "wake_claude-rco-2"
    wake_file.write_text("2026-06-13T12:11:00Z", encoding="utf-8")
    _set_mtime(wake_file, datetime(2026, 6, 13, 12, 6, tzinfo=timezone.utc))

    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z"),
        ],
        bridge_root=tmp_path,
        now_utc=_now(),
        min_age_minutes=0,
        min_repeats=2,
    )

    row = report["stalled_wakes"][0]
    assert row["wake_file_checked"] is True
    assert row["wake_file_present"] is True
    assert row["wake_file_mtime_utc"].endswith("Z")
    assert row["wake_file_fresh_after_last_wake"] is True
    assert row["wake_file_lag_seconds"] == 60.0
    assert row["wake_file_age_minutes"] == 24.0
    assert "wake file exists" in row["diagnosis"]
    command = report["delivery_escalation"]["diagnostic_commands"][0]
    assert command["target_agent"] == "claude-rco-2"
    assert command["authority"] == "read_only_report_no_restart_no_gate_skip"
    assert command["argv"] == [
        "python",
        "tools/session_liveness_supervisor_report.py",
        "--bridge-root",
        str(tmp_path),
        "--agent",
        "claude-rco-2",
        "--json",
    ]


def test_stale_wake_file_is_not_treated_as_delivery_proof(tmp_path: Path) -> None:
    wake_file = tmp_path / "wake_claude-rco-2"
    wake_file.write_text("older wake signal", encoding="utf-8")
    _set_mtime(wake_file, datetime(2026, 6, 13, 11, 59, tzinfo=timezone.utc))

    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z"),
        ],
        bridge_root=tmp_path,
        now_utc=_now(),
        min_age_minutes=0,
        min_repeats=2,
    )

    row = report["stalled_wakes"][0]
    assert row["wake_file_checked"] is True
    assert row["wake_file_present"] is True
    assert row["wake_file_fresh_after_last_wake"] is False
    assert row["wake_file_lag_seconds"] == -360.0
    assert row["wake_file_age_minutes"] == 31.0
    assert "stale relative to latest wake_request" in row["diagnosis"]
    assert row["safe_next_action"].startswith("restart or verify")


def test_wake_send_failure_is_attached_to_unresolved_group() -> None:
    report = check_wake_delivery(
        events=[
            _wake(ts="2026-06-13T12:00:00Z", to="codex-lead-1"),
            _wake_send_failed(ts="2026-06-13T12:06:00Z", target="codex-lead-1"),
            _wake(ts="2026-06-13T12:08:00Z", to="codex-lead-1"),
        ],
        now_utc=_now(),
        min_age_minutes=12,
        min_repeats=2,
    )

    assert report["decision"] == "wake_delivery_stalled"
    assert report["delivery_escalation"]["reason"] == (
        "operator_wake_send_failed_for_unresolved_wake"
    )
    assert report["delivery_escalation"]["safe_next_action"] == (
        "repair_operator_wake_routing_or_title_map"
    )
    row = report["stalled_wakes"][0]
    assert row["target_agent"] == "codex-lead-1"
    assert row["wake_send_failed_count"] == 1
    assert row["latest_wake_send_failed_ts_utc"] == "2026-06-13T12:06:00Z"
    assert "Tab for agent 'codex-lead-1' not found" in row[
        "latest_wake_send_failed_message"
    ]
    assert "operator wake send failed" in row["diagnosis"]
    assert "TitleMap" in row["safe_next_action"]


def test_agent_filter_limits_targets() -> None:
    report = check_wake_delivery(
        events=[
            _wake(to="claude-rco-2", task_id="rco2", ts="2026-06-13T12:00:00Z"),
            _wake(to="codex-lead-1", task_id="lead", ts="2026-06-13T12:00:00Z"),
            _wake(to="codex-lead-1", task_id="lead", ts="2026-06-13T12:01:00Z"),
        ],
        agents=["codex-lead-1"],
        now_utc=_now(),
        min_age_minutes=0,
        min_repeats=2,
    )

    assert report["by_agent"] == {"codex-lead-1": 1}
    assert report["stalled_wakes"][0]["task_id"] == "lead"


def test_invalid_thresholds_fail_closed() -> None:
    try:
        check_wake_delivery(events=[], min_repeats=0)
    except WakeDeliveryError as exc:
        assert "min_repeats must be positive" in exc.report["errors"]
    else:
        raise AssertionError("expected WakeDeliveryError")


def test_cli_json_and_fail_on_stalled(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _wake(ts="2026-06-13T12:00:00Z"),
            _wake(ts="2026-06-13T12:05:00Z"),
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--events",
            str(events_path),
            "--bridge-root",
            str(tmp_path),
            "--now",
            "2026-06-13T12:30:00Z",
            "--min-age-minutes",
            "0",
            "--fail-on-stalled",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 3
    report = json.loads(result.stdout)
    assert report["decision"] == "wake_delivery_stalled"
    assert report["stalled_count"] == 1

# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_bridge_wake_delivery.py"

sys.path.insert(0, str(ROOT))

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


def _now() -> datetime:
    return datetime(2026, 6, 13, 12, 30, tzinfo=timezone.utc)


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
    assert "wake file exists" in row["diagnosis"]


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

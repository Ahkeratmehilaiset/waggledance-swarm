# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_nudge_effectiveness_report.py"
sys.path.insert(0, str(ROOT))

from tools.bridge_nudge_effectiveness_report import (  # noqa: E402
    NudgeRow,
    read_nudge_csvs,
    report_nudge_effectiveness,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _event(
    *,
    agent: str = "claude-rco-1",
    ts: str = "2026-06-16T00:00:00Z",
    event_type: str = "message",
    status: str = "received",
) -> dict[str, object]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "status": status,
        "task_id": "bridge-read",
        "message": "received",
    }


def _row(
    *,
    agent: str = "claude-rco-1",
    ts: str = "2026-06-16T00:10:00Z",
    action: str = "nudged",
    classification: str = "rco_wake_requested",
) -> NudgeRow:
    return NudgeRow(
        ts_utc=_ts(ts),
        agent=agent,
        classification=classification,
        action=action,
        open_incoming=3,
    )


def test_reports_repeated_nudges_without_later_target_activity() -> None:
    report = report_nudge_effectiveness(
        events=[_event(ts="2026-06-16T00:00:00Z")],
        nudge_rows=[
            _row(ts="2026-06-16T00:10:00Z"),
            _row(ts="2026-06-16T00:15:00Z"),
        ],
        now_utc=_ts("2026-06-16T00:30:00Z"),
        min_actual_nudges=2,
        min_elapsed_minutes=5,
    )

    assert report["decision"] == "nudge_effectiveness_issue"
    assert report["issue_agents"] == ["claude-rco-1"]
    row = report["agents"][0]
    assert row["status"] == "actual_nudges_without_target_activity"
    assert row["actual_nudge_count"] == 2
    assert row["post_nudge_activity_observed"] is False
    assert row["first_actual_nudge_age_minutes"] == 20.0
    assert row["latest_actual_nudge_age_minutes"] == 15.0
    assert row["safe_next_action"] == "restart_or_verify_target_session_poll_loop"
    assert report["authority_boundary"]["process_restart_allowed"] is False


def test_target_read_after_latest_nudge_clears_issue() -> None:
    report = report_nudge_effectiveness(
        events=[_event(ts="2026-06-16T00:20:00Z")],
        nudge_rows=[
            _row(ts="2026-06-16T00:10:00Z"),
            _row(ts="2026-06-16T00:15:00Z"),
        ],
        now_utc=_ts("2026-06-16T00:30:00Z"),
    )

    assert report["decision"] == "nudge_effectiveness_ok"
    row = report["agents"][0]
    assert row["actual_nudge_count"] == 0
    assert row["last_target_activity_ts_utc"] == "2026-06-16T00:20:00Z"


def test_late_new_nudge_does_not_reset_ineffective_sequence_age() -> None:
    report = report_nudge_effectiveness(
        events=[_event(ts="2026-06-16T00:00:00Z")],
        nudge_rows=[
            _row(ts="2026-06-16T00:10:00Z"),
            _row(ts="2026-06-16T00:34:00Z"),
        ],
        now_utc=_ts("2026-06-16T00:35:00Z"),
        min_actual_nudges=2,
        min_elapsed_minutes=5,
    )

    assert report["decision"] == "nudge_effectiveness_issue"
    row = report["agents"][0]
    assert row["first_actual_nudge_age_minutes"] == 25.0
    assert row["latest_actual_nudge_age_minutes"] == 1.0


def test_heartbeat_after_nudge_does_not_clear_issue() -> None:
    report = report_nudge_effectiveness(
        events=[_event(ts="2026-06-16T00:20:00Z", event_type="heartbeat")],
        nudge_rows=[
            _row(ts="2026-06-16T00:10:00Z"),
            _row(ts="2026-06-16T00:15:00Z"),
        ],
        now_utc=_ts("2026-06-16T00:30:00Z"),
    )

    assert report["decision"] == "nudge_effectiveness_issue"
    assert report["agents"][0]["last_target_activity_ts_utc"] == ""


def test_window_targeting_failure_has_specific_decision() -> None:
    report = report_nudge_effectiveness(
        events=[],
        nudge_rows=[_row(action="no_window")],
        now_utc=_ts("2026-06-16T00:30:00Z"),
    )

    assert report["decision"] == "nudge_window_targeting_issue"
    row = report["agents"][0]
    assert row["status"] == "window_targeting_failed"
    assert row["no_window_count"] == 1
    assert row["safe_next_action"] == "fix_window_title_map_or_retitle_target_session"


def test_operator_guard_without_actual_nudge_is_not_issue() -> None:
    report = report_nudge_effectiveness(
        events=[],
        nudge_rows=[_row(action="skip_operator_active")],
        now_utc=_ts("2026-06-16T00:30:00Z"),
    )

    assert report["decision"] == "nudge_effectiveness_ok"
    row = report["agents"][0]
    assert row["status"] == "operator_guard_limited_nudges"
    assert row["issue"] is False


def test_reads_nudge_csv(tmp_path: Path) -> None:
    path = tmp_path / "nudges.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ts",
                "agent",
                "heartbeat_age",
                "read_age",
                "substantive_age",
                "open_incoming",
                "classification",
                "action",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "ts": "2026-06-16T00:10:00Z",
                "agent": "claude-rco-2",
                "heartbeat_age": "10",
                "read_age": "20",
                "substantive_age": "30",
                "open_incoming": "4",
                "classification": "rco_wake_requested",
                "action": "nudged",
            }
        )

    rows = read_nudge_csvs([path])

    assert rows == [
        NudgeRow(
            ts_utc=_ts("2026-06-16T00:10:00Z"),
            agent="claude-rco-2",
            classification="rco_wake_requested",
            action="nudged",
            open_incoming=4,
        )
    ]


def test_cli_fail_on_issue_returns_three(tmp_path: Path) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    shared = bridge_root / "shared"
    shared.mkdir(parents=True)
    events_path = shared / "events.jsonl"
    events_path.write_text(
        json.dumps(_event(ts="2026-06-16T00:00:00Z")) + "\n",
        encoding="utf-8",
    )
    nudge_csv = tmp_path / "nudges.csv"
    with nudge_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["ts", "agent", "classification", "action"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "ts": "2026-06-16T00:10:00Z",
                "agent": "claude-rco-1",
                "classification": "rco_wake_requested",
                "action": "nudged",
            }
        )
        writer.writerow(
            {
                "ts": "2026-06-16T00:15:00Z",
                "agent": "claude-rco-1",
                "classification": "rco_wake_requested",
                "action": "nudged",
            }
        )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bridge-root",
            str(bridge_root),
            "--nudge-csv",
            str(nudge_csv),
            "--now",
            "2026-06-16T00:30:00Z",
            "--fail-on-issue",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["decision"] == "nudge_effectiveness_issue"

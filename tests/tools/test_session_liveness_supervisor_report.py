# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "session_liveness_supervisor_report.py"

sys.path.insert(0, str(ROOT))

from tools.session_liveness_supervisor_report import (  # noqa: E402
    build_session_liveness_supervisor_report,
    main,
    read_screen_state_snapshot,
)


def _now() -> datetime:
    return datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _event(
    *,
    ts: str,
    agent: str,
    event_type: str = "message",
    status: str = "active",
    task_id: str | None = None,
    to: str = "",
    write_scope: list[str] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": task_id or f"{agent}-task",
        "status": status,
        "message": "event",
    }
    if to:
        event["to"] = to
    if write_scope is not None:
        event["write_scope"] = write_scope
    return event


def _wake(
    *,
    ts: str,
    to: str = "claude-rco-2",
    task_id: str = "wake-rco2",
) -> dict[str, object]:
    return _event(
        ts=ts,
        agent="operator",
        event_type="wake_request",
        status="open",
        task_id=task_id,
        to=to,
    )


def _screen(
    state: str,
    *,
    agent: str = "claude-rco-2",
    observed_at: str = "2026-06-15T11:58:00Z",
    cycle_age_minutes: float | None = None,
    context_budget_exceeded: bool = False,
) -> dict[str, object]:
    row: dict[str, object] = {
        "agent": agent,
        "state": state,
        "observed_at_utc": observed_at,
        "context_budget_exceeded": context_budget_exceeded,
    }
    if cycle_age_minutes is not None:
        row["cycle_age_minutes"] = cycle_age_minutes
    return row


def _process(command_line: str, pid: int = 1001) -> dict[str, object]:
    return {
        "ProcessId": pid,
        "ParentProcessId": 900,
        "CreationDate": "2026-06-15T11:00:00Z",
        "CommandLine": command_line,
    }


def test_visible_working_cycle_suppresses_restart_despite_activity_gap() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _event(
                ts="2026-06-15T10:00:00Z",
                agent="claude-rco-2",
                event_type="decision",
            )
        ],
        agents=["claude-rco-2"],
        screen_states={"claude-rco-2": _screen("working", cycle_age_minutes=30)},
        activity_gap_minutes=45,
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert report["decision"] == "session_liveness_ok"
    assert row["activity_gap_exceeded"] is True
    assert row["screen_working"] is True
    assert row["restart_recommended"] is False
    assert row["safe_next_action"].startswith("wait for the visible working cycle")


def test_recent_idle_prompt_does_not_recommend_restart() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _event(
                ts="2026-06-15T11:55:00Z",
                agent="codex-lead-1",
                event_type="liveness",
            )
        ],
        agents=["codex-lead-1"],
        screen_states={"codex-lead-1": _screen("idle_prompt", agent="codex-lead-1")},
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert row["status"] == "idle_no_restart"
    assert row["restart_recommended"] is False
    assert row["activity_gap_exceeded"] is False


def test_wake_delivery_stalled_recommends_restart_when_idle() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _wake(ts="2026-06-15T11:00:00Z"),
            _wake(ts="2026-06-15T11:05:00Z"),
        ],
        agents=["claude-rco-2"],
        screen_states={"claude-rco-2": _screen("idle_prompt")},
        wake_min_age_minutes=12,
        wake_min_repeats=2,
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert report["decision"] == "session_restart_recommended"
    assert row["restart_recommended"] is True
    assert row["wake_delivery_stalled"] is True
    assert row["restart_triggers"] == ["wake_delivery_stalled"]
    assert "target-origin bridge activity" in row["safe_next_action"]


def test_context_budget_exceeded_recommends_fresh_session() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _event(ts="2026-06-15T11:50:00Z", agent="claude-rco-2")
        ],
        agents=["claude-rco-2"],
        screen_states={
            "claude-rco-2": _screen(
                "idle_prompt",
                cycle_age_minutes=120,
                context_budget_exceeded=True,
            )
        },
        cycle_budget_minutes=90,
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert row["cycle_budget_exceeded"] is True
    assert row["restart_recommended"] is True
    assert row["restart_triggers"] == ["cycle_budget_exceeded"]
    assert "fresh handoff-bootstrapped session" in row["safe_next_action"]


def test_active_write_claim_blocks_restart_until_checkpoint() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _event(
                ts="2026-06-15T11:00:00Z",
                agent="codex-lead-1",
                event_type="claim",
                task_id="write-slice",
                write_scope=["tools/session_liveness_supervisor_report.py"],
            )
        ],
        agents=["codex-lead-1"],
        screen_states={
            "codex-lead-1": _screen(
                "idle_prompt",
                agent="codex-lead-1",
                cycle_age_minutes=120,
            )
        },
        cycle_budget_minutes=90,
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert report["decision"] == "session_restart_blocked_by_active_write_claim"
    assert row["restart_blocked"] is True
    assert row["restart_recommended"] is False
    assert row["restart_recommended_after_checkpoint"] is True
    assert row["active_write_claim_count"] == 1
    assert "release the active write claim" in row["safe_next_action"]


def test_handoff_clears_write_claim_before_restart_recommendation() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _event(
                ts="2026-06-15T10:30:00Z",
                agent="codex-lead-1",
                event_type="claim",
                task_id="write-slice",
                write_scope=["tools/a.py"],
            ),
            _event(
                ts="2026-06-15T11:00:00Z",
                agent="codex-lead-1",
                event_type="handoff",
                task_id="write-slice",
            ),
        ],
        agents=["codex-lead-1"],
        screen_states={
            "codex-lead-1": _screen(
                "idle_prompt",
                agent="codex-lead-1",
                cycle_age_minutes=120,
            )
        },
        cycle_budget_minutes=90,
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert row["active_write_claim_count"] == 0
    assert row["restart_recommended"] is True
    assert row["restart_blocked"] is False


def test_old_event_claim_is_not_treated_as_current_write_claim() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _event(
                ts="2026-06-10T11:00:00Z",
                agent="codex-lead-1",
                event_type="claim",
                task_id="stale-write-slice",
                write_scope=["tools/stale.py"],
            )
        ],
        agents=["codex-lead-1"],
        screen_states={
            "codex-lead-1": _screen(
                "idle_prompt",
                agent="codex-lead-1",
                cycle_age_minutes=120,
            )
        },
        active_claim_max_age_hours=24,
        cycle_budget_minutes=90,
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert row["active_write_claim_count"] == 0
    assert row["restart_recommended"] is True
    assert row["restart_blocked"] is False


def test_missing_watcher_recommends_watcher_repair() -> None:
    report = build_session_liveness_supervisor_report(
        events=[
            _event(ts="2026-06-15T11:00:00Z", agent="claude-rco-2")
        ],
        processes=[],
        process_snapshot_checked=True,
        agents=["claude-rco-2"],
        screen_states={"claude-rco-2": _screen("idle_prompt")},
        now_utc=_now(),
    )

    row = report["agents"][0]
    assert report["decision"] == "session_watcher_repair_recommended"
    assert row["watcher_repair_recommended"] is True
    assert row["watcher_missing"] is True
    assert row["restart_triggers"] == ["watcher_missing"]
    assert "watcher/heartbeat helper" in row["safe_next_action"]


def test_visible_watcher_keeps_report_read_only() -> None:
    watcher_command = (
        "powershell -File C:\\Python\\project2-master\\.agent-bridge\\bin\\"
        "Watch-Bridge.ps1 -Agent codex-lead-1"
    )
    report = build_session_liveness_supervisor_report(
        events=[
            _event(ts="2026-06-15T11:55:00Z", agent="codex-lead-1")
        ],
        processes=[_process(watcher_command)],
        process_snapshot_checked=True,
        agents=["codex-lead-1"],
        now_utc=_now(),
    )

    assert report["decision"] == "session_liveness_ok"
    assert report["authority_boundary"]["process_restart_allowed"] is False
    assert report["authority_boundary"]["keyboard_input_allowed"] is False
    assert report["authority_boundary"]["bridge_append_allowed"] is False


def test_read_screen_state_snapshot_accepts_agent_mapping(tmp_path: Path) -> None:
    path = tmp_path / "screen.json"
    path.write_text(
        json.dumps({"claude-rco-2": {"state": "idle_prompt"}}),
        encoding="utf-8",
    )

    screen = read_screen_state_snapshot(str(path))

    assert screen["claude-rco-2"]["agent"] == "claude-rco-2"
    assert screen["claude-rco-2"]["state"] == "idle_prompt"


def test_screen_state_non_finite_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "screen.json"
    path.write_text('[{"agent":"claude-rco-2","cycle_age_minutes":NaN}]', encoding="utf-8")

    assert main(["--events", str(_events_file(tmp_path, [])), "--screen-state-json", str(path), "--json"]) == 2


def test_cli_json_and_fail_on_restart_recommended(tmp_path: Path) -> None:
    events_path = _events_file(
        tmp_path,
        [
            _wake(ts="2026-06-15T11:00:00Z"),
            _wake(ts="2026-06-15T11:05:00Z"),
        ],
    )
    screen_path = tmp_path / "screen.json"
    screen_path.write_text(
        json.dumps([_screen("idle_prompt", agent="claude-rco-2")]),
        encoding="utf-8",
    )

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
            "--screen-state-json",
            str(screen_path),
            "--now",
            "2026-06-15T12:00:00Z",
            "--fail-on-restart-recommended",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["decision"] == "session_restart_recommended"
    assert payload["restart_recommended_agents"] == ["claude-rco-2"]


def _events_file(path: Path, events: list[dict[str, object]]) -> Path:
    events_path = path / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path

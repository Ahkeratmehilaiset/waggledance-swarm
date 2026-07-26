# SPDX-License-Identifier: BUSL-1.1
"""Task-keyed conformance tests for the canonical bridge nudger state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATE_HELPER = ROOT / ".agent-bridge" / "bin" / "BridgeNudgeState.ps1"
WATCHER = ROOT / ".agent-bridge" / "bin" / "Watch-AgentsBridgeNudge.ps1"
NOW = "2026-07-26T08:00:00Z"


def _powershells() -> list[str]:
    found: list[str] = []
    for candidate in ("powershell.exe", "powershell", "pwsh"):
        executable = shutil.which(candidate)
        if executable and executable.lower() not in {
            item.lower() for item in found
        }:
            found.append(executable)
    if not found:
        pytest.skip("PowerShell is required for bridge nudge state tests")
    return found


@pytest.fixture(params=_powershells(), ids=lambda path: Path(path).stem)
def powershell(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _event(
    ts_utc: str,
    *,
    agent: str = "operator",
    to: str = "codex-tools-1",
    event_type: str = "message",
    status: str = "open",
    task_id: str = "task-a",
) -> dict[str, str]:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "to": to,
        "type": event_type,
        "status": status,
        "task_id": task_id,
        "message": "",
    }


def _state(
    powershell: str,
    events: list[dict[str, str]],
    *,
    agent: str = "codex-tools-1",
    now: str = NOW,
    unanswered_seconds: int = 900,
    rco_trigger_agents: tuple[str, ...] = (
        "claude-rco-1",
        "claude-rco-2",
        "operator",
    ),
) -> dict[str, object]:
    script = (
        f". '{STATE_HELPER}'\n"
        "$payload = $input | ConvertFrom-Json\n"
        "$report = Get-BridgeNudgeAgentState "
        "-Events @($payload.events) "
        "-Agent ([string]$payload.agent) "
        "-NowUtc ([datetime]$payload.now) "
        "-UnansweredSeconds ([int]$payload.unanswered_seconds) "
        "-RcoTriggerAgents @($payload.rco_trigger_agents)\n"
        "$report | ConvertTo-Json -Depth 12 -Compress\n"
    )
    completed = subprocess.run(  # noqa: S603
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(
            {
                "events": events,
                "agent": agent,
                "now": now,
                "unanswered_seconds": unanswered_seconds,
                "rco_trigger_agents": list(rco_trigger_agents),
            }
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_duplicate_events_count_one_open_task(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z"),
            _event(
                "2026-07-26T07:01:00Z",
                event_type="wake_request",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_task_ids"] == ["task-a"]
    assert report["open_request_count"] == 2


def test_status_updates_do_not_reset_request_age(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z", status="open"),
            _event("2026-07-26T07:50:00Z", status="review_requested"),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_request_count"] == 1
    assert report["open_requests"][0]["age_seconds"] == 3600


def test_unrelated_target_output_does_not_clear_open_task(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z"),
            _event(
                "2026-07-26T07:10:00Z",
                agent="codex-tools-1",
                to="",
                event_type="test",
                status="pass",
                task_id="unrelated-task-b",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_task_ids"] == ["task-a"]


def test_matching_target_answer_closes_only_same_task(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z", task_id="task-a"),
            _event("2026-07-26T07:01:00Z", task_id="task-b"),
            _event(
                "2026-07-26T07:10:00Z",
                agent="codex-tools-1",
                to="operator",
                event_type="decision",
                status="answered",
                task_id="task-a",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_task_ids"] == ["task-b"]


def test_received_ack_does_not_close_request(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z"),
            _event(
                "2026-07-26T07:10:00Z",
                agent="codex-tools-1",
                to="operator",
                event_type="message",
                status="received",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1


def test_closed_wake_leaves_same_task_message_open(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z"),
            _event(
                "2026-07-26T07:01:00Z",
                event_type="wake_request",
            ),
            _event(
                "2026-07-26T07:02:00Z",
                event_type="wake_request",
                status="closed",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_request_count"] == 1
    assert report["open_requests"][0]["request_type"] == "message"
    assert report["fresh_rco_wake"] is False


def test_closed_wake_leaves_other_requesters_wake_open(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event(
                "2026-07-26T07:00:00Z",
                agent="claude-rco-1",
                event_type="wake_request",
            ),
            _event(
                "2026-07-26T07:01:00Z",
                agent="operator",
                event_type="wake_request",
            ),
            _event(
                "2026-07-26T07:02:00Z",
                agent="operator",
                event_type="wake_request",
                status="closed",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_request_count"] == 1
    assert report["open_requests"][0]["requester"] == "claude-rco-1"
    assert report["fresh_rco_wake"] is True


def test_recipient_matching_is_exact_and_supports_multi_target(
    powershell: str,
) -> None:
    report = _state(
        powershell,
        [
            _event(
                "2026-07-26T07:00:00Z",
                to="codex-tools-10",
                task_id="substring-collision",
            ),
            _event(
                "2026-07-26T07:01:00Z",
                to="fable-5,codex-tools-1",
                task_id="exact-multi-target",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_task_ids"] == ["exact-multi-target"]


def test_task_ids_are_case_sensitive(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z", task_id="Task-A"),
            _event("2026-07-26T07:01:00Z", task_id="task-a"),
        ],
    )

    assert report["open_incoming_count"] == 2
    assert report["open_task_count"] == 2
    assert set(report["open_task_ids"]) == {"Task-A", "task-a"}


def test_polymorphic_task_and_type_values_cannot_collide(
    powershell: str,
) -> None:
    report = _state(
        powershell,
        [
            _event(
                "2026-07-26T07:00:00Z",
                event_type="c",
                task_id="a|b",
            ),
            _event(
                "2026-07-26T07:01:00Z",
                event_type="b|c",
                task_id="a",
            ),
        ],
    )

    assert report["open_incoming_count"] == 2
    assert report["open_request_count"] == 2
    assert set(report["open_task_ids"]) == {"a", "a|b"}


def test_bridge_follow_nudge_never_feeds_watcher(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event(
                "2026-07-26T07:00:00Z",
                event_type="wake_request",
                task_id="bridge-follow-nudge-20260726",
            )
        ],
    )

    assert report["open_incoming_count"] == 0
    assert report["open_task_count"] == 0
    assert report["fresh_rco_wake"] is False


def test_unanswered_age_boundary_is_strict(powershell: str) -> None:
    events = [_event("2026-07-26T07:45:00Z")]

    at_boundary = _state(powershell, events, unanswered_seconds=900)
    past_boundary = _state(
        powershell,
        events,
        now="2026-07-26T08:00:01Z",
        unanswered_seconds=900,
    )

    assert at_boundary["open_incoming_count"] == 0
    assert past_boundary["open_incoming_count"] == 1


def test_later_request_reopens_task_after_answer(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z"),
            _event(
                "2026-07-26T07:10:00Z",
                agent="codex-tools-1",
                to="operator",
                event_type="decision",
                status="answered",
            ),
            _event("2026-07-26T07:20:00Z", status="review_requested"),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_request_count"] == 1
    assert report["open_requests"][0]["opened_utc"].startswith(
        "2026-07-26T07:20:00"
    )


def test_same_timestamp_answer_does_not_close_request(powershell: str) -> None:
    report = _state(
        powershell,
        [
            _event("2026-07-26T07:00:00Z"),
            _event(
                "2026-07-26T07:00:00Z",
                agent="codex-tools-1",
                to="operator",
                event_type="decision",
                status="answered",
            ),
        ],
    )

    assert report["open_incoming_count"] == 1
    assert report["open_task_ids"] == ["task-a"]


def test_canonical_watcher_uses_pure_state_helper() -> None:
    text = WATCHER.read_text(encoding="utf-8")
    helper = STATE_HELPER.read_text(encoding="utf-8")

    assert "BridgeNudgeState.ps1" in text
    assert "Get-BridgeNudgeAgentState" in text
    assert "$to.Contains($agent)" not in text
    assert "function Test-Actionable" not in text
    assert "$WriteEventScript = Join-Path $PSScriptRoot" in text
    assert "$env:AGENT_BRIDGE_RUNTIME_ROOT = $RuntimeRoot" in text
    assert "Join-Path (Join-Path $RuntimeRoot 'bin')" not in text
    assert "$openByTask" in helper
    assert "$openByKey" not in helper


def test_canonical_watcher_dry_run_uses_task_keyed_count(
    powershell: str,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / ".agent-bridge"
    events_path = runtime_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    events = [
        _event(
            (old - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            agent="codex-tools-1",
            to="",
            event_type="heartbeat",
            status="active",
            task_id="heartbeat",
        ),
        _event(old.isoformat().replace("+00:00", "Z")),
        _event(
            (old + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            event_type="wake_request",
        ),
    ]
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "nudger.log"
    csv_path = tmp_path / "nudger.csv"
    completed = subprocess.run(  # noqa: S603
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(WATCHER),
            "-TargetAgents",
            "codex-tools-1",
            "-RuntimeRoot",
            str(runtime_root),
            "-UnansweredSeconds",
            "0",
            "-OperatorIdleGuardSeconds",
            "0",
            "-LogPath",
            str(log_path),
            "-CsvPath",
            str(csv_path),
            "-MarkerPrefix",
            str(tmp_path / "marker-"),
            "-DryRun",
            "-OnceOnly",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    rows = csv_path.read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].endswith("open_incoming,classification,action")
    fields = rows[1].split(",")
    assert fields[1] == "codex-tools-1"
    assert fields[5] == "1"
    assert fields[6] == "rco_wake_requested"

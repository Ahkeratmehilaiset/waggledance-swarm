# SPDX-License-Identifier: BUSL-1.1
"""wake_request routing parity for the PowerShell bridge classifier.

The agents poll the bridge through `.agent-bridge/bin/Get-BridgeNextAction.ps1`,
which classifies events with `BridgeEventClassifier.ps1`. Before this fix the PS
classifier bucketed `wake_request` as infrastructure (alongside heartbeat /
liveness), so every directed nudge — operator "read the bridge" and peer
"review this PR" — was dropped instead of routing to `answer_incoming`. The
Python `bridge_next_action.py` already lists `wake_request` in `REQUEST_TYPES`
(#1101); these tests lock the PowerShell consumer to the same contract:

* `wake_request` with an open/request status is request-like (actionable),
* exact `wake_request/closed` is a requester closure but never a target answer,
* other `wake_request` rows never close another agent's open request,
* heartbeat / liveness remain pure infrastructure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = ROOT / ".agent-bridge" / "bin" / "BridgeEventClassifier.ps1"
NEXT_ACTION = ROOT / ".agent-bridge" / "bin" / "Get-BridgeNextAction.ps1"
STATUS = ROOT / ".agent-bridge" / "bin" / "Get-AgentBridgeStatus.ps1"
READER = ROOT / ".agent-bridge" / "bin" / "Read-AgentBridge.ps1"


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for the bridge classifier tests")
    return executable


def _classify(event: dict[str, str]) -> dict[str, bool]:
    """Dot-source the classifier and return its routing verdicts."""
    if not CLASSIFIER.is_file():
        pytest.skip(f"classifier not found at {CLASSIFIER}")
    script = (
        f". '{CLASSIFIER}'\n"
        "$e = $input | ConvertFrom-Json\n"
        "$r = [bool](Test-BridgeRequestLikeEvent -Event $e)\n"
        "$a = [bool](Test-BridgeAnswerEvent -Event $e)\n"
        "$c = [bool](Test-BridgeRequesterClosureEvent -Event $e)\n"
        "[pscustomobject]@{ request_like = $r; answer = $a; requester_closure = $c } "
        "| ConvertTo-Json -Compress\n"
    )
    completed = subprocess.run(  # noqa: S603
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip())


def _closes(request: dict[str, str], event: dict[str, str]) -> bool:
    """Return whether an event is a requester closeout for this request."""
    if not CLASSIFIER.is_file():
        pytest.skip(f"classifier not found at {CLASSIFIER}")
    script = (
        f". '{CLASSIFIER}'\n"
        "$rows = @($input | ConvertFrom-Json)\n"
        "$result = [bool](Test-BridgeRequesterClosureForRequest "
        "-Request $rows[0] -Event $rows[1])\n"
        "$result | ConvertTo-Json -Compress\n"
    )
    completed = subprocess.run(  # noqa: S603
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps([request, event]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip())


def _event(event_type: str, status: str) -> dict[str, str]:
    return {
        "type": event_type,
        "status": status,
        "task_id": "wake-parity-task",
        "to": "fable-5",
        "agent": "codex-lead-1",
        "ts_utc": "2026-06-13T00:00:00Z",
        "message": "",
    }


def _next_action(
    tmp_path: Path,
    events: list[dict[str, str]],
    *,
    agent: str = "fable-5",
    now: str = "2026-06-13T03:00:00Z",
    suppressed_agents: dict[str, str] | None = None,
) -> dict[str, object]:
    if not NEXT_ACTION.is_file():
        pytest.skip(f"next-action script not found at {NEXT_ACTION}")
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    if suppressed_agents:
        suppression_path = bridge_root / "shared" / "production_liveness_suppression.json"
        suppression_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "suppressed_agents": {
                        agent_id: {"reason": reason}
                        for agent_id, reason in suppressed_agents.items()
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    completed = subprocess.run(  # noqa: S603
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(NEXT_ACTION),
            "-Agent",
            agent,
            "-Now",
            now,
            "-Json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _status(tmp_path: Path, events: list[dict[str, str]]) -> dict[str, object]:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    completed = subprocess.run(  # noqa: S603
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(STATUS),
            "-Json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _read(tmp_path: Path, events: list[dict[str, str]]) -> str:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    completed = subprocess.run(  # noqa: S603
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(READER),
            "-Agent",
            "fable-5",
            "-NoAckReceived",
            "-ContinuityTail",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _mixed_same_task_requests() -> list[dict[str, str]]:
    message = _event("message", "open")
    message["agent"] = "operator"
    message["task_id"] = "same-task-different-request-type"
    message["ts_utc"] = "2026-06-13T02:30:00Z"
    wake = {
        **message,
        "type": "wake_request",
        "ts_utc": "2026-06-13T02:31:00Z",
    }
    closed = {
        **wake,
        "status": "closed",
        "ts_utc": "2026-06-13T02:32:00Z",
    }
    return [message, wake, closed]


def _two_requester_wakes() -> list[dict[str, str]]:
    first = _event("wake_request", "open")
    first["agent"] = "operator"
    first["task_id"] = "same-task-two-requesters"
    first["ts_utc"] = "2026-06-13T02:30:00Z"
    second = {
        **first,
        "agent": "codex-lead-1",
        "ts_utc": "2026-06-13T02:31:00Z",
    }
    closed = {
        **second,
        "status": "closed",
        "ts_utc": "2026-06-13T02:32:00Z",
    }
    return [first, second, closed]


@pytest.mark.parametrize(
    "status",
    ["open", "review_requested", "rco_requested", "changes_requested"],
)
def test_wake_request_is_actionable_incoming(status: str) -> None:
    verdict = _classify(_event("wake_request", status))
    assert verdict["request_like"] is True
    assert verdict["answer"] is False


def test_wake_request_ack_is_not_actionable() -> None:
    # An ack-status wake_request is a read receipt, not a fresh request.
    verdict = _classify(_event("wake_request", "received"))
    assert verdict["request_like"] is False
    assert verdict["answer"] is False
    assert verdict["requester_closure"] is False


def test_exact_closed_wake_request_is_requester_closure_not_answer() -> None:
    verdict = _classify(_event("wake_request", "closed"))
    assert verdict["request_like"] is False
    assert verdict["answer"] is False
    assert verdict["requester_closure"] is True


def test_closed_wake_closes_only_an_open_wake_request() -> None:
    closure = _event("wake_request", "closed")

    assert _closes(_event("wake_request", "open"), closure) is True
    assert _closes(_event("message", "open"), closure) is False


@pytest.mark.parametrize("status", ["done", "closed_after_recovery", "superseded"])
def test_other_terminal_wake_statuses_are_not_requester_closures(status: str) -> None:
    verdict = _classify(_event("wake_request", status))
    assert verdict["request_like"] is False
    assert verdict["answer"] is False
    assert verdict["requester_closure"] is False


@pytest.mark.parametrize("event_type", ["heartbeat", "liveness"])
def test_pure_infrastructure_events_are_neither(event_type: str) -> None:
    verdict = _classify(_event(event_type, "active"))
    assert verdict["request_like"] is False
    assert verdict["answer"] is False


def test_directed_message_request_still_routes() -> None:
    verdict = _classify(_event("message", "request"))
    assert verdict["request_like"] is True
    assert verdict["answer"] is False


@pytest.mark.parametrize(
    ("event_type", "status"),
    [("decision", "rco_pass"), ("done", "merged_observed")],
)
def test_closure_events_still_count_as_answers(
    event_type: str, status: str
) -> None:
    verdict = _classify(_event(event_type, status))
    assert verdict["request_like"] is False
    assert verdict["answer"] is True


def test_next_action_ages_out_old_operator_wake_request(tmp_path: Path) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "operator-wake-request-20260612"
    event["ts_utc"] = "2026-06-12T12:00:00Z"

    report = _next_action(tmp_path, [event])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1


def test_next_action_keeps_recent_wake_request_actionable(tmp_path: Path) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "operator-wake-request-20260613"
    event["ts_utc"] = "2026-06-13T02:30:00Z"

    report = _next_action(tmp_path, [event])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "operator-wake-request-20260613"
    assert report["open_incoming_count"] == 1
    assert report["stale_incoming_count"] == 0


def test_next_action_closed_wake_closes_same_task_open_wake(tmp_path: Path) -> None:
    opened = _event("wake_request", "open")
    opened["agent"] = "operator"
    opened["task_id"] = "operator-wake-request-closed"
    opened["ts_utc"] = "2026-06-13T02:30:00Z"
    closed = {**opened, "status": "closed", "ts_utc": "2026-06-13T02:31:00Z"}

    report = _next_action(tmp_path, [opened, closed])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_next_action_closed_wake_does_not_close_same_task_message(
    tmp_path: Path,
) -> None:
    report = _next_action(tmp_path, _mixed_same_task_requests())

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "same-task-different-request-type"
    assert report["open_incoming_count"] == 1


def test_next_action_stale_closed_wake_keeps_same_task_message(
    tmp_path: Path,
) -> None:
    events = _mixed_same_task_requests()
    for index, event in enumerate(events):
        event["ts_utc"] = f"2026-06-12T02:3{index}:00Z"

    report = _next_action(tmp_path, events)

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1


def test_status_keeps_message_when_same_task_wake_is_closed(tmp_path: Path) -> None:
    report = _status(tmp_path, _mixed_same_task_requests())

    unresolved = report["unresolved_requests"]
    assert len(unresolved) == 1
    assert unresolved[0]["task_id"] == "same-task-different-request-type"
    assert unresolved[0]["request"] == "message/open"


def test_reader_keeps_message_when_same_task_wake_is_closed(tmp_path: Path) -> None:
    output = _read(tmp_path, _mixed_same_task_requests())

    assert "OPEN same-task-different-request-type: message/open" in output
    assert (
        "closed-by-requester same-task-different-request-type: "
        "request wake_request/open -> wake_request/closed"
    ) in output


def test_status_keeps_other_requesters_same_task_wake(tmp_path: Path) -> None:
    report = _status(tmp_path, _two_requester_wakes())

    unresolved = report["unresolved_requests"]
    assert len(unresolved) == 1
    assert unresolved[0]["task_id"] == "same-task-two-requesters"
    assert unresolved[0]["from"] == "operator"


def test_reader_keeps_other_requesters_same_task_wake(tmp_path: Path) -> None:
    output = _read(tmp_path, _two_requester_wakes())

    assert "OPEN same-task-two-requesters: wake_request/open from operator" in output
    assert (
        "closed-by-requester same-task-two-requesters: "
        "request wake_request/open -> wake_request/closed"
    ) in output


def test_next_action_ignores_recent_bridge_follow_nudge(tmp_path: Path) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "bridge-follow-nudge-20260613"
    event["ts_utc"] = "2026-06-13T02:30:00Z"

    report = _next_action(tmp_path, [event])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_next_action_reports_suppressed_agent_instead_of_follow_nudge(
    tmp_path: Path,
) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "bridge-follow-nudge-20260613"
    event["ts_utc"] = "2026-06-13T02:30:00Z"

    report = _next_action(
        tmp_path,
        [event],
        suppressed_agents={"fable-5": "operator reported lane unavailable"},
    )

    assert report["action"] == "agent_suppressed_unavailable"
    assert report["task_id"] == "agent-suppressed-unavailable"
    assert report["open_incoming_count"] == 0
    assert report["suppression_reason"] == "operator reported lane unavailable"

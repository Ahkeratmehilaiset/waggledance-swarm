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
* `wake_request` is never an answer/closure event (a nudge must not mark another
  agent's open request as answered),
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
    """Dot-source the classifier and return its request-like / answer verdicts."""
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
    [
        ("decision", "rco_pass"),
        ("done", ""),
        ("done", "open"),
        ("done", "proposal"),
        ("done", "ready"),
        ("done", "blocked"),
        ("done", "pushed"),
        ("done", "merged_observed"),
        ("done", "completed_pr1324_pushed"),
        ("done", "completed_read_only_next_step_proposal"),
        ("done", "pr1366_merged_reauthor_not_needed"),
    ],
)
def test_closure_events_still_count_as_answers(
    event_type: str, status: str
) -> None:
    verdict = _classify(_event(event_type, status))
    assert verdict["request_like"] is False
    assert verdict["answer"] is True


@pytest.mark.parametrize(
    ("status", "request_like"),
    [
        ("not_done", False),
        ("done_not_done", False),
        ("closed_not_done", False),
        ("superseded_not_closed", False),
        ("not_abandoned", False),
        ("not_block", False),
        ("not_changes_requested", False),
        ("request", True),
    ],
)
def test_nonterminal_done_event_is_not_an_answer(
    status: str, request_like: bool
) -> None:
    verdict = _classify(_event("done", status))
    assert verdict["request_like"] is request_like
    assert verdict["answer"] is False
    assert verdict["requester_closure"] is False


def test_next_action_keeps_request_open_after_negated_done(tmp_path: Path) -> None:
    request = _event("message", "request")
    request["agent"] = "claude"
    request["to"] = "fable-5"
    request["task_id"] = "negated-done-task"
    request["ts_utc"] = "2026-06-13T02:30:00Z"

    nonterminal = _event("done", "not_done")
    nonterminal["agent"] = "fable-5"
    nonterminal["to"] = "claude"
    nonterminal["task_id"] = "negated-done-task"
    nonterminal["ts_utc"] = "2026-06-13T02:31:00Z"

    report = _next_action(tmp_path, [request, nonterminal])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "negated-done-task"
    assert report["open_incoming_count"] == 1


def test_requester_negated_done_prefix_does_not_close_request(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    request["agent"] = "claude"
    request["to"] = "fable-5"
    request["task_id"] = "requester-negated-done-task"
    request["ts_utc"] = "2026-06-13T02:30:00Z"

    nonterminal = _event("done", "done_not_done")
    nonterminal["agent"] = "claude"
    nonterminal["to"] = "operator"
    nonterminal["task_id"] = "requester-negated-done-task"
    nonterminal["ts_utc"] = "2026-06-13T02:31:00Z"

    report = _next_action(tmp_path, [request, nonterminal])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "requester-negated-done-task"
    assert report["open_incoming_count"] == 1


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

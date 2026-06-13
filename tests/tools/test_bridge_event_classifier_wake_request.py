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
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = ROOT / ".agent-bridge" / "bin" / "BridgeEventClassifier.ps1"


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
        "[pscustomobject]@{ request_like = $r; answer = $a } "
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
    [("decision", "rco_pass"), ("done", "merged_observed")],
)
def test_closure_events_still_count_as_answers(
    event_type: str, status: str
) -> None:
    verdict = _classify(_event(event_type, status))
    assert verdict["request_like"] is False
    assert verdict["answer"] is True

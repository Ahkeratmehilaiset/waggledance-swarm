"""Interim message receipts must not satisfy PowerShell request closure."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.bridge_next_action import _is_answer_like, _is_ack_or_infrastructure


@pytest.mark.parametrize("kind", ["triage_disposition", "consumer_tick"])
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("shell", list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")]))))
def test_coordination_records_are_not_answers_in_either_selector(kind, bound, shell):
    event = {"type": kind, "status": "recorded", "task_id": "fixture/task", "agent": "codex-tools-1", "to": "codex-lead-1",
             "payload": {"disposition": "ack_dispatch", "target_event_id": "event:1"}}
    if bound:
        event["in_reply_to_request_id"] = "request-1"
    script = f". '{CLASSIFIER}'\n$e = $input | ConvertFrom-Json\nTest-BridgeAnswerEvent $e"
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            input=json.dumps(event), text=True, capture_output=True, check=True)
    assert result.stdout.strip().lower() == "false"
    assert _is_answer_like(event) is False
    if kind == "consumer_tick":
        assert _is_ack_or_infrastructure(event) is True


CLASSIFIER = (
    Path(__file__).resolve().parents[2]
    / ".agent-bridge"
    / "bin"
    / "BridgeEventClassifier.ps1"
)


def _classify(status: str) -> dict[str, bool]:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is required")
    script = (
        f". '{CLASSIFIER}'\n"
        "$event = $input | ConvertFrom-Json\n"
        "[pscustomobject]@{ "
        "request_like = [bool](Test-BridgeRequestLikeEvent $event); "
        "answer = [bool](Test-BridgeAnswerEvent $event) "
        "} | ConvertTo-Json -Compress\n"
    )
    event = {
        "type": "message",
        "status": status,
        "task_id": "interim-parity",
        "agent": "codex-tools-1",
        "to": "codex-lead-1",
        "in_reply_to_request_id": "request-1",
    }
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "status",
    ["queued", "queue_accepted", "accepted", "pending", "in_progress"],
)
def test_interim_message_receipt_is_not_an_answer(status: str) -> None:
    assert _classify(status) == {"request_like": False, "answer": False}


@pytest.mark.parametrize("status", ["answered", "ready_for_controlled_replacement"])
def test_substantive_message_answer_still_closes(status: str) -> None:
    assert _classify(status) == {"request_like": False, "answer": True}

"""Interim message receipts must not satisfy PowerShell request closure."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


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

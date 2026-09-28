"""An immutable reply snapshot must respect canonical append order."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_bridge_request_contract import events


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / ".agent-bridge/bin/Get-BridgeReplySnapshot.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("no_cache", (False, True), ids=("cached", "no_cache"))
def test_snapshot_uses_first_request_append_position_and_exact_binding(
    tmp_path: Path, shell: str, no_cache: bool,
) -> None:
    _, request, reply = events()
    request.update(request_id="append-order-v1", request_digest="digest-v1")
    reply.update(
        in_reply_to_request_id="append-order-v1",
        in_reply_to_request_digest="digest-v1",
        in_reply_to_requester={
            key: request[key] for key in ("agent", "agent_uuid", "session_id", "run_id")
        },
    )
    shared = tmp_path / "shared"
    shared.mkdir()
    log = shared / "events.jsonl"
    # The first reply has a later timestamp but is physically before the request.
    log.write_text("".join(json.dumps(row) + "\n" for row in (reply, request)), encoding="utf-8")

    def snapshot() -> dict[str, object]:
        command = [shell, "-NoProfile", "-NonInteractive", "-File", str(SNAPSHOT),
                   "-RequestId", "append-order-v1"]
        if no_cache:
            command.append("-NoCache")
        process = subprocess.run(
            command, env=dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path)),
            capture_output=True, text=True, timeout=40,
        )
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)

    cold = snapshot()
    assert cold["results"][0]["state"] == "pending_at_snapshot"
    warm = snapshot()
    assert warm["results"][0]["state"] == "pending_at_snapshot"
    if not no_cache:
        assert warm["cache_status"] == "incremental"

    # An identical retry is one immutable request: its first append position wins.
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(request) + "\n")
    assert snapshot()["results"][0]["state"] == "pending_at_snapshot"

    wrong_nonce = dict(reply, payload={**reply["payload"], "nonce": "wrong"})
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(wrong_nonce) + "\n")
    assert snapshot()["results"][0]["state"] == "pending_at_snapshot"

    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(reply) + "\n")
    final = snapshot()
    assert final["results"][0]["state"] == "answered"
    assert final["results"][0]["answers"] == [reply]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("no_cache", (False, True), ids=("cached", "no_cache"))
def test_identical_request_retry_keeps_first_append_position(
    tmp_path: Path, shell: str, no_cache: bool,
) -> None:
    _, request, reply = events()
    request.update(request_id="retry-v1", request_digest="digest-v1")
    reply.update(
        in_reply_to_request_id="retry-v1",
        in_reply_to_request_digest="digest-v1",
        in_reply_to_requester={
            key: request[key] for key in ("agent", "agent_uuid", "session_id", "run_id")
        },
    )
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in (request, reply, request)),
        encoding="utf-8",
    )
    command = [shell, "-NoProfile", "-NonInteractive", "-File", str(SNAPSHOT),
               "-RequestId", "retry-v1"]
    if no_cache:
        command.append("-NoCache")
    process = subprocess.run(
        command, env=dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path)),
        capture_output=True, text=True, timeout=40,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["results"][0]["state"] == "answered"
    assert result["results"][0]["answers"] == [reply]

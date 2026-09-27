"""The continuity reader must not reorder a reply ahead of its request."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("before", [False, True])
def test_reader_keeps_canonical_order(tmp_path, shell, bound, before):
    request = dict(agent="lead", to="peer", type="wake_request", status="request",
                   task_id="lead/reader-order", message="fixture", payload={},
                   ts_utc="2026-09-27T18:30:00Z")
    answer = dict(request, agent="peer", to="lead", type="message", status="answered",
                  ts_utc="2026-09-27T18:31:00Z")
    if bound:
        request.update(request_id="reader-r1", request_digest="digest")
        answer.update(in_reply_to_request_id="reader-r1", in_reply_to_request_digest="digest",
                      in_reply_to_requester={"agent": "lead"})
    rows = [answer, request] if before else [request, answer]
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in rows), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(tmp_path)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File",
        str(ROOT / ".agent-bridge/bin/Read-AgentBridge.ps1"), "-Agent", "peer",
        "-NoAckReceived", "-Tail", "10"], env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert ("OPEN lead/reader-order" in result.stdout) == before, result.stdout

"""Real Windows bridge writer envelope against the narrow public-status filter.

All events and bridge files are synthetic. The writer is copied unchanged apart
from isolating its named mutex prefix, matching the bridge writer test fixture.
Non-Windows CI reports an explicit skip; a Windows execution is still required.
"""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from sandbox.mind.step2 import shared_capture_adapter as adapter


ROOT = Path(__file__).resolve().parents[2]
TASK = "public-task"
AGENT = "synthetic-1"
AGENT_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows bridge writer; requires separate Windows execution")


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def fixture_writer(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")
    assert shell, "PowerShell is required; test was NOT performed"
    fixture_root = tmp_path / "fixture"
    code = fixture_root / ".agent-bridge" / "bin"
    shutil.copytree(ROOT / ".agent-bridge" / "bin", code)
    prefix = "Local\\WdPublicStatusFixture-" + uuid.uuid4().hex + "-"
    original_mutex = "Global\\WaggleDanceBridge"
    for script in code.glob("*.ps1"):
        text = script.read_text(encoding="utf-8-sig")
        if original_mutex in text:
            script.write_text(text.replace(original_mutex, prefix), encoding="utf-8-sig")
    writer = code / "Write-AgentEvent.ps1"
    original = (ROOT / ".agent-bridge" / "bin" / writer.name).read_text(encoding="utf-8-sig")
    assert writer.read_text(encoding="utf-8-sig") == original.replace(original_mutex, prefix)
    runtime = tmp_path / "synthetic-bridge-runtime"
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("AGENT_BRIDGE_", "WD_BRIDGE_", "WD_AGENT_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return shell, writer, runtime, env


@pytest.mark.parametrize("variation", ["public", "targeted", "message", "extra_payload", "path"])
def test_actual_writer_envelope_only_admits_empty_public_marker(fixture_writer, variation):
    shell, writer, runtime, env = fixture_writer
    payload = {"public_status": True}
    options = []
    if variation == "targeted":
        options += ["-To", "synthetic-2"]
    elif variation == "message":
        options += ["-Message", "synthetic-text"]
    elif variation == "extra_payload":
        payload["detail"] = "synthetic-detail"
    elif variation == "path":
        options += ["-Paths", "synthetic/path.txt"]
    options += ["-Agent", AGENT, "-AgentUuid", AGENT_UUID, "-Type", "status", "-TaskId", TASK,
                "-Status", "in_progress", "-PayloadJson", json.dumps(payload, separators=(",", ":")),
                "-ReceiptJson"]
    command = "$ErrorActionPreference='Stop'; & " + ps_quote(writer) + " " + " ".join(
        item if item.startswith("-") else ps_quote(item) for item in options
    )
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    delivery = receipt["_bridge_delivery"]
    assert delivery["accepted"] is True and delivery["delivery_status"] == "canonical"
    lines = (runtime / "shared" / "events.jsonl").read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["agent"] == AGENT and event["agent_uuid"] == AGENT_UUID
    assert event["task_id"] == TASK and event["type"] == "status"
    assert event["payload"] == payload
    assert adapter._public_status_event(event, frozenset({TASK}), frozenset({"status"})) is (variation == "public")

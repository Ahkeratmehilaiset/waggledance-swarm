# SPDX-License-Identifier: BUSL-1.1
"""F11 Reply-ToRequest.ps1: answer one exact request by its id (RCO1 2026-09-30, Lead request d79f933d).

The wrapper runs from a COPY of the bin with the real pinned reader and a fixture Write-BridgeTaskReply.ps1
that only records what it was handed. Every runtime root is a tmp_path, and the calling lane's bridge
identity and runtime variables are removed from the child environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / ".agent-bridge" / "bin" / "Reply-ToRequest.ps1"
SHELLS = [shell for shell in (shutil.which("powershell.exe"), shutil.which("pwsh")) if shell] if os.name == "nt" else []
REQUEST = {"ts_utc": "2026-09-30T18:00:00.0000000Z", "agent": "codex-lead-1", "type": "wake_request", "task_id": "t/1",
           "status": "request", "to": "claude-rco-1,claude-rco-2", "message": "do it", "request_id": "req-1",
           "session_id": "s-1", "agent_uuid": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101", "payload": {"nonce": "n1"}}
NOISE = {"ts_utc": "2026-09-30T18:00:01.0000000Z", "agent": "fable-5", "type": "message", "task_id": "t/2",
         "status": "informational", "to": "claude-rco-1", "message": "fyi"}

CAPTURE_WRITER = r"""
[CmdletBinding()]
param([string]$Agent, [string]$RequestEventJson, [string]$ResultJson, [string]$Message, [string]$Status,
      [switch]$ReceiptJson)
$record = [ordered]@{ agent=$Agent; request=$RequestEventJson; result=$ResultJson; message=$Message; status=$Status }
$path = Join-Path $env:AGENT_BRIDGE_RUNTIME_ROOT 'reply-capture.json'
[IO.File]::WriteAllText($path, ($record | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
'CAPTURED'
"""


def _fixture(tmp_path: Path, events: list) -> tuple[Path, Path]:
    code = tmp_path / "fixture" / ".agent-bridge" / "bin"
    shutil.copytree(ROOT / ".agent-bridge" / "bin", code)
    (code / "Write-BridgeTaskReply.ps1").write_text(CAPTURE_WRITER, encoding="utf-8")
    runtime = tmp_path / "runtime"
    (runtime / "shared").mkdir(parents=True)
    (runtime / "shared" / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events),
                                                     encoding="utf-8")
    (runtime / "result.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")
    return code / "Reply-ToRequest.ps1", runtime


def _run(shell: str, script: Path, runtime: Path, agent: str = "claude-rco-1",
         request_id: str = "req-1") -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    command = (f"try {{ & '{script}' -Agent '{agent}' -RequestId '{request_id}' "
               f"-ResultJson (Get-Content -Raw -LiteralPath '{runtime / 'result.json'}') }} "
               "catch { Write-Output ('REFUSED:' + $_.Exception.Message) }")
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                          capture_output=True, text=True, timeout=120, env=env, cwd=runtime)


@pytest.mark.skipif(not SHELLS, reason="Windows PowerShell or pwsh is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_the_exact_request_is_found_and_handed_to_the_task_reply_writer(tmp_path, shell):
    script, runtime = _fixture(tmp_path, [NOISE, REQUEST, dict(REQUEST)])   # a replayed identical copy is one request
    done = _run(shell, script, runtime)
    assert "CAPTURED" in done.stdout, done.stdout + done.stderr
    captured = json.loads((runtime / "reply-capture.json").read_text(encoding="utf-8-sig"))
    request = json.loads(captured["request"])
    keys = ("request_id", "agent", "session_id", "agent_uuid", "to", "task_id", "type", "status", "ts_utc")
    assert {key: request[key] for key in keys} == {key: REQUEST[key] for key in keys}
    assert request["payload"] == {"nonce": "n1"} and json.loads(captured["result"]) == {"status": "done"}
    assert (captured["agent"], captured["status"]) == ("claude-rco-1", "answered")


@pytest.mark.skipif(not SHELLS, reason="Windows PowerShell or pwsh is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("events,agent,request_id,fragment", [
    ([NOISE], "claude-rco-1", "req-1", "no event carries request id req-1"),
    ([REQUEST, dict(REQUEST, to="claude-rco-1", payload={"nonce": "forged"})], "claude-rco-1", "req-1",
     "2 different events carry request id req-1"),
    ([REQUEST], "fable-5", "req-1", "not addressed to fable-5"),
    ([dict(REQUEST, in_reply_to_request_id="req-0")], "claude-rco-1", "req-1", "is itself a reply"),
    ([REQUEST], "Claude-rco-1", "req-1", "agent id malformed"),
    ([REQUEST], "claude-rco-1", "req 1", "request id malformed"),
    ([{key: value for key, value in dict(REQUEST, REQUEST_ID="req-1").items() if key != "request_id"}],
     "claude-rco-1", "req-1", "no event carries request id req-1"),
], ids=["absent", "forged_second_copy", "foreign_addressee", "reply_not_request", "agent_case", "request_id_space",
        "key_case"])
def test_absent_ambiguous_foreign_or_malformed_requests_are_refused_before_any_write(tmp_path, shell, events, agent,
                                                                                     request_id, fragment):
    script, runtime = _fixture(tmp_path, events)
    refused = _run(shell, script, runtime, agent=agent, request_id=request_id)
    assert "REFUSED:" in refused.stdout and fragment in refused.stdout, refused.stdout + refused.stderr
    assert not (runtime / "reply-capture.json").exists()


def _nest(levels: int) -> object:
    value: object = "leaf"
    for level in range(levels):
        value = {"level": level, "next": value}
    return value


# C-F1 (Fable review 99897de5, reproduced in both shells; Lead d06fbf85): the reader's -Raw view is ConvertTo-Json
# -Depth 12, so a request nested deeper reached the writer with its deepest values replaced by strings, and the
# reply bound to that changed copy. A request 14 containers deep (the event counts one) is now refused before any
# write; the depth-7 control binds exactly, its nonce, digest and expected responders included.
BINDING = {"request_digest": "sha256:" + "d" * 64, "expected_responders": ["claude-rco-1"]}


@pytest.mark.skipif(not SHELLS, reason="Windows PowerShell or pwsh is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("levels,refused", [(5, False), (12, True)], ids=["control_depth_7", "deep_depth_14"])
def test_c_f1_a_request_deeper_than_the_reader_view_is_refused_and_a_shallow_one_binds_exactly(tmp_path, shell,
                                                                                               levels, refused):
    event = dict(REQUEST, **BINDING, payload={"nonce": "n1", "deep": _nest(levels)})
    script, runtime = _fixture(tmp_path, [event])
    done = _run(shell, script, runtime)
    if refused:
        assert "REFUSED:" in done.stdout and "cannot be bound exactly" in done.stdout, done.stdout + done.stderr
        assert not (runtime / "reply-capture.json").exists()
        return
    assert "CAPTURED" in done.stdout, done.stdout + done.stderr
    captured = json.loads(json.loads((runtime / "reply-capture.json").read_text(encoding="utf-8-sig"))["request"])
    assert captured["payload"] == event["payload"]                       # exact, all the way down
    keys = (*BINDING, "request_id", "to", "session_id", "agent_uuid")
    assert {key: captured[key] for key in keys} == {key: event[key] for key in keys}


# R-D1 (RCO2 report 8DF45964; reproduced by Fable 08A5539F in both shells): with two or more events the reader's
# -Raw view is an ARRAY, so -Depth 12 keeps only 12 container levels of each event, and the depth guard measured
# the already-cut copy. A 13-15 deep request on a multi-event log was bound to a copy 12 deep with its deepest
# object turned into a string. The guard now refuses 12 and deeper on any log; 11 still binds exactly.
@pytest.mark.skipif(not SHELLS, reason="Windows PowerShell or pwsh is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("noise", [True, False], ids=["two_events", "one_event"])
@pytest.mark.parametrize("depth,refused", [(11, False), (12, True), (13, True), (14, True)],
                         ids=["depth_11", "depth_12", "depth_13", "depth_14"])
def test_r_d1_the_depth_guard_holds_on_a_multi_event_log_and_at_the_single_event_boundary(tmp_path, shell, noise,
                                                                                            depth, refused):
    event = dict(REQUEST, **BINDING, payload={"nonce": "n1", "deep": _nest(depth - 2)})   # event + payload = 2
    script, runtime = _fixture(tmp_path, [NOISE, event] if noise else [event])
    done = _run(shell, script, runtime)
    if refused:
        assert "REFUSED:" in done.stdout and "cannot be bound exactly" in done.stdout, done.stdout + done.stderr
        assert not (runtime / "reply-capture.json").exists()
        return
    assert "CAPTURED" in done.stdout, done.stdout + done.stderr
    captured = json.loads(json.loads((runtime / "reply-capture.json").read_text(encoding="utf-8-sig"))["request"])
    assert captured["payload"] == event["payload"]                       # exact, all the way down
    keys = (*BINDING, "request_id", "to", "session_id", "agent_uuid")
    assert {key: captured[key] for key in keys} == {key: event[key] for key in keys}


@pytest.mark.skipif(not SHELLS, reason="Windows PowerShell or pwsh is required")
@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_the_wrapper_offers_no_way_to_pass_a_request_body(shell):
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                             f"(Get-Command '{WRAPPER}').Parameters.Keys -join ','"],
                            capture_output=True, text=True, timeout=60)
    names = set(result.stdout.strip().split(","))
    assert {"Agent", "RequestId", "ResultJson"} <= names and "RequestEventJson" not in names, result.stdout

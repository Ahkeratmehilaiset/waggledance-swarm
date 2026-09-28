"""Closure must respect canonical append order, UTC time and terminal meaning."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.bridge_next_action import _open_requests_for_agent

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))


def event(agent, kind, status, stamp, to):
    return dict(agent=agent, type=kind, status=status, ts_utc=stamp,
                to=to, task_id="lead/closure", payload={}, message="fixture")


def scenario(case, bound):
    request = event("lead", "wake_request", "request", "2026-09-27T18:30:00.5000000Z", "peer")
    answer = event("peer", "message", "answered", "2026-09-27T18:31:00Z", "lead")
    if bound:
        request.update(request_id="closure-r1", request_digest="digest")
        answer.update(in_reply_to_request_id="closure-r1", in_reply_to_request_digest="digest",
                      in_reply_to_requester={"agent": "lead"})
    if case == "offset":
        answer["ts_utc"] = "2026-09-27T20:00:00+03:00"
    elif case == "fraction":
        answer["ts_utc"] = "2026-09-27T18:30:00Z"
    elif case == "equal":
        answer["ts_utc"] = request["ts_utc"]
    elif case == "naive":
        answer["ts_utc"] = "2026-09-27T18:31:00"
    elif case == "naive_request":
        request["ts_utc"] = "2026-09-27T18:30:00"
    elif case in {"not_done", "undone", "incomplete"}:
        answer.update(type="done", status=case)
    elif case == "withdrawal":
        answer.update(agent="lead", type="wake_request", status="closed", to="peer")
    elif case == "third_party":
        answer.update(agent="other", type="done", status="done")
    elif case.startswith("idle_"):
        request["payload"] = {"protocol_version": "idle-protocol.v1", "proposal_id": "proposal-1"}
        answer.update(type="intent", status="processing", payload={
            "protocol_version": "idle-protocol.v1", "responds_to": "proposal-1"})
        if case == "idle_offset":
            answer["ts_utc"] = "2026-09-27T20:00:00+03:00"
    rows = [answer, request] if case in {"before", "idle_before"} else [request, answer]
    if case == "identical_replay":
        rows.append(dict(request))
    return rows, case not in {"control", "withdrawal", "identical_replay"}


CASES = ["control", "withdrawal", "before", "offset", "fraction", "equal", "naive", "naive_request",
         "not_done", "undone", "incomplete", "third_party", "identical_replay", "idle_before", "idle_offset"]


@pytest.mark.parametrize("shell", SHELLS)
def test_contract_rejects_unspecified_datetime_independent_of_host_timezone(shell):
    helper = str(ROOT / ".agent-bridge/bin/BridgeRequestContract.ps1").replace("'", "''")
    script = f". '{helper}'; $v=[datetime]::new(2026,9,27,18,31,0,[DateTimeKind]::Unspecified); if ($null -ne (ConvertTo-BridgeContractTime $v)) {{ exit 9 }}"
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("bound", [False, True])
def test_python_closure_order(case, bound):
    rows, expected_open = scenario(case, bound)
    assert bool(_open_requests_for_agent(agent="peer", events=rows)) == expected_open


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("bound", [False, True])
def test_powershell_closure_order(tmp_path, shell, case, bound):
    rows, expected_open = scenario(case, bound)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BRIDGE_")}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(tmp_path)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File",
        str(ROOT / ".agent-bridge/bin/Get-BridgeNextAction.ps1"), "-Agent", "peer", "-Json",
        "-Now", "2026-09-27T19:00:00Z"], env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout[result.stdout.index("{"):])
    assert bool(data["open_incoming_count"]) == expected_open
    if case == "naive_request":
        assert data["oldest_open_request_age_seconds"] is None

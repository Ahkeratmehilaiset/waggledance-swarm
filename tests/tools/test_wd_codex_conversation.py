"""Conversation-owner protocol tests use fake servers, never model calls."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "ops/windows/reboot/Invoke-WdCodexConversationLoop.ps1"
OLD = ROOT / "ops/windows/reboot/Invoke-WdLaneTurnLoop.ps1"
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("powershell.exe"), shutil.which("pwsh")])) )


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(script, shell, timeout=30):
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                          cwd=ROOT, capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize("shell", SHELLS)
def test_rpc_allowlist_and_steer_binding(shell):
    result = run(f"""
$ErrorActionPreference='Stop'
. {q(OLD)}
. {q(RUNNER)}
$send = New-WdConversationRpc -Id 7 -Method 'turn/steer' -Parameters @{{threadId='thread-1';expectedTurnId='turn-2';input=@(@{{type='text';text='change focus'}})}}
$rejected=$false
try {{ New-WdConversationRpc -Id 8 -Method 'thread/shellCommand' -Parameters @{{}} }} catch {{ $rejected=$true }}
@{{send=$send;rejected=$rejected}} | ConvertTo-Json -Depth 12 -Compress
""", shell)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["rejected"] is True
    assert value["send"]["params"]["expectedTurnId"] == "turn-2"


@pytest.mark.parametrize("shell", SHELLS)
def test_pinned_start_configuration(shell):
    result = run(f"""
$ErrorActionPreference='Stop'
. {q(OLD)}
. {q(RUNNER)}
Get-WdConversationThreadParameters -Worktree 'C:\\work' -RuntimeRoot 'C:\\bridge' -Model 'gpt-5.6-sol' -Effort 'ultra' | ConvertTo-Json -Depth 20 -Compress
""", shell)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["model"] == "gpt-5.6-sol"
    assert value["approvalPolicy"] == "never"
    assert value["sandbox"] == "workspace-write"
    assert value["config"]["model_reasoning_effort"] == "ultra"
    assert value["config"]["sandbox_workspace_write"]["writable_roots"] == ["C:\\work", "C:\\bridge"]
    assert value["config"]["sandbox_workspace_write"]["network_access"] is False


FAKE = r'''
import datetime, json, os, pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
scenario = sys.argv[2]
agent = sys.argv[3] if len(sys.argv)>3 else 'codex-lead-1'
active = None
turns = 0

def send(value):
    print(json.dumps(value), flush=True)

def finish(status="completed"):
    if scenario == "human_tool_missing" and turns == 2:
        send({"method":"item/started","params":{"threadId":"thread-owned","turnId":active,"item":{"type":"futureUnknownTool","id":"unknown-1"}}})
    if scenario == "tool_progress":
        send({"method":"item/started","params":{"threadId":"thread-owned","turnId":active,"item":{"type":"commandExecution","id":"cmd-1","command":"python -m pytest","status":"inProgress"}}})
        send({"method":"item/completed","params":{"threadId":"thread-owned","turnId":active,"item":{"type":"commandExecution","id":"cmd-1","command":"python -m pytest","status":"completed","exitCode":0,"aggregatedOutput":"2 passed"}}})
    if status == "completed" and scenario not in ["missing", "reconcile_readonly"] and not (scenario in ["human_chat","human_tool_missing"] and turns == 2):
        spec = json.loads(next((root / ".codex-audit/wd-turn-loop").glob("turn-*.spec.json")).read_text(encoding="utf-8-sig"))
        specs = list((root / ".codex-audit/wd-turn-loop").glob("turn-*.spec.json"))
        spec = json.loads(max(specs, key=lambda p: p.stat().st_mtime_ns).read_text(encoding="utf-8-sig"))
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pathlib.Path(spec["compact_state_path"]).write_text(json.dumps({"schema":"wd.lane-current.v1","agent":spec["agent"],"worktree":spec["worktree"],"task_id":"test","next_action":"wait","status":"idle","updated_at_utc":stamp}))
        receipt = {k:spec[k] for k in ["turn_id","agent","session_id","generation","compact_state_path"]}
        receipt.update(disposition="idle",task_id="test")
        pathlib.Path(spec["receipt_path"]).write_text(json.dumps(receipt))
    send({"method":"item/agentMessage/delta","params":{"threadId":"thread-owned","turnId":active,"itemId":"answer-"+active,"delta":"Hello operator"}})
    send({"method":"item/completed","params":{"threadId":"thread-owned","turnId":active,"item":{"type":"agentMessage","id":"answer-"+active,"text":"Hello operator"}}})
    send({"method":"turn/completed","params":{"threadId":"thread-owned","turn":{"id":active,"status":status}}})

for line in sys.stdin:
    message = json.loads(line)
    with (root / "rpc.jsonl").open("a") as log: log.write(json.dumps(message)+"\n")
    method = message.get("method", "")
    if method == "initialized": continue
    if method == "initialize": send({"id":message["id"],"result":{}})
    elif method in ["thread/start", "thread/resume"]:
        history = [] if message["params"].get("excludeTurns") else [{"items":[{"type":"agentMessage","id":"history-one","text":"x"*600000 if scenario == "large_resume" else "Previously stored answer"}]}]
        send({"id":message["id"],"result":{"thread":{"id":"thread-owned","turns":history}}})
    elif method == "turn/start":
        turns += 1
        if scenario == "reconcile_readonly" and turns == 2:
            assert message["params"]["sandboxPolicy"]["type"] == "readOnly"
        active = "native-"+str(turns)
        owner = json.loads((root / ("bridge/.wd-turn-"+agent+".owner.json")).read_text(encoding="utf-8-sig"))
        assert pathlib.Path(owner["pending_path"]).exists(), "pending must precede model RPC"
        if scenario == "disconnect": sys.exit(0)
        if scenario == "late_start":
            send({"method":"turn/started","params":{"threadId":"thread-owned","turn":{"id":active,"status":"inProgress"}}})
            finish()
            send({"id":message["id"],"result":{"turn":{"id":active,"status":"inProgress"}}})
            continue
        send({"id":message["id"],"result":{"turn":{"id":active,"status":"inProgress"}}})
        send({"method":"turn/started","params":{"threadId":"thread-owned","turn":{"id":active,"status":"inProgress"}}})
        if scenario in ["timeout", "output"]:
            child = subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])
            (root / "descendant.pid").write_text(str(child.pid))
            if scenario == "output": print("x"*600000,flush=True)
            continue
        if scenario == "spontaneous":
            finish()
            send({"method":"turn/started","params":{"threadId":"thread-owned","turn":{"id":"unrequested","status":"inProgress"}}})
            continue
        if scenario == "wake_during" and turns == 1:
            (root / ("bridge/wake_"+agent)).write_text("first wake")
            (root / ("bridge/wake_"+agent)).write_text("coalesced wake")
        if scenario == "question":
            send({"id":"question-1","method":"item/tool/requestUserInput","params":{"threadId":"thread-owned","turnId":active,"questions":[{"id":"choice","header":"Choice","question":"Choose","isOther":False,"isSecret":False,"options":[{"label":"A","description":"A choice"}]}]}})
        elif scenario == "approval":
            send({"id":"approval-1","method":"item/commandExecution/requestApproval","params":{"threadId":"thread-owned","turnId":active}})
        elif scenario not in ["steer","late_steer","steer_rejected","interrupt","interrupt_repeated","interrupt_lost"] or turns > 1: finish()
    elif method == "turn/steer":
        assert message["params"]["expectedTurnId"] == active
        if scenario == "steer_rejected":
            finish()
            send({"id":message["id"],"error":{"code":-32600,"message":"Turn already ended"}})
        elif scenario == "late_steer":
            finish()
            send({"id":message["id"],"result":{"turnId":active}})
        else:
            send({"id":message["id"],"result":{"turnId":active}})
            finish()
    elif method == "turn/interrupt":
        if scenario == "interrupt_lost": sys.exit(0)
        send({"id":message["id"],"result":{}})
        if scenario == "interrupt_repeated": time.sleep(0.3)
        finish("interrupted")
    elif message.get("id") in ["question-1","approval-1"]:
        finish()
'''


@pytest.mark.skipif(os.name != "nt", reason="Windows job containment")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("scenario", ["complete", "steer", "late_start", "late_steer", "steer_rejected", "tool_progress", "interrupt", "interrupt_repeated", "interrupt_lost", "missing", "disconnect", "question", "approval", "timeout", "output", "spontaneous", "wake_during", "resume", "paused_resume", "large_resume", "resume_missing_initial", "human_chat", "human_tool_missing", "reconcile_readonly"])
def test_owned_fake_conversation(tmp_path, shell, scenario, agent="codex-lead-1", workflow_permissions=False, compatibility=False):
    model, effort = ("gpt-5.6-sol", "ultra") if agent == "codex-lead-1" else ("gpt-5.6-terra", "high")
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    extra_root = tmp_path / "common-git"
    extra_root.mkdir()
    (tmp_path / ".codex-audit").mkdir()
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    if scenario in ["resume", "paused_resume", "large_resume", "resume_missing_initial"]:
        journal = tmp_path / ".codex-audit/wd-turn-loop"
        journal.mkdir()
        (journal / "conversation.json").write_text(json.dumps({
            "schema":"wd.codex-conversation.v1", "agent":agent, "worktree":str(tmp_path),
            "thread_id":"thread-owned", "model":model, "effort":effort,
            "automatic_enabled":scenario != "paused_resume",
            "initial_context_delivered":scenario != "resume_missing_initial",
            **({"codex_permission_posture":"existing_interactive"} if compatibility else {}),
        }))
    image = tmp_path / "initial.png"
    image.write_bytes(b"fake local image for RPC test")
    native_python = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python313/python.exe"
    python = native_python if native_python.is_file() else Path(sys.executable)
    script = f"""
$ErrorActionPreference='Stop'
. {q(OLD)}
. {q(RUNNER)}
function New-WdConversationNativeProcess {{ param($CliPath,$Worktree)
    Initialize-WdConversationNativeType
    New-Object WdConversationProcess({q(python)}, [string[]]@({q(fake)}, {q(tmp_path)}, {q(scenario)}, {q(agent)}), $Worktree)
}}
$script:status=@{{active=$false;send=$false;reconcile=$false;text='';epoch='';turn='';automatic=$true}}; $script:sent=0; $script:tick=0; $script:question=''
$script:messages=[Collections.Generic.List[object]]::new()
$script:transportFacts=[Collections.Generic.List[object]]::new(); $script:turnFacts=[Collections.Generic.List[object]]::new()
function New-WdOperatorConversationView {{ param([switch]$Headless,$AgentLabel,$ModelLabel,$Title) $script:label=$ModelLabel; return @{{}} }}
function Update-WdOperatorConversationView {{ param($View) }}
function Close-WdOperatorConversationView {{ param($View) }}
function Add-WdOperatorConversationMessage {{ param($View,$Role,$Text,$ItemId,[switch]$Delta) $script:messages.Add(@{{role=$Role;text=$Text;item=$ItemId}}) }}
function Resolve-WdOperatorConversationAction {{ param($View,$ActionId,[bool]$Accepted,$Reason) $script:messages.Add(@{{role='resolution';text=$Reason;accepted=$Accepted}}) }}
function Set-WdOperatorConversationStatus {{ param($View,$Text,[bool]$CanSend,[bool]$CanInterrupt,[bool]$CanToggleAutomation,[bool]$AutomationEnabled,[bool]$TurnActive,$OwnerEpoch,$ObservedTurnId,[bool]$Interrupting,[bool]$CanReconcile,$RecoveryReason)
    $script:status=@{{text=$Text;active=$TurnActive;send=$CanSend;reconcile=$CanReconcile;epoch=$OwnerEpoch;turn=$ObservedTurnId;automatic=$AutomationEnabled}}
}}
function Show-WdOperatorConversationQuestion {{ param($View,$RequestId,$Questions) $script:question=$RequestId }}
function Clear-WdOperatorConversationQuestion {{ param($View,$RequestId) $script:question='' }}
function Get-WdOperatorConversationActions {{ param($View)
    $script:tick++
    if ({q(scenario)} -in @('human_chat','human_tool_missing') -and $script:sent -eq 0 -and $script:status.send -and -not $script:status.active) {{
        $script:sent++; return @{{kind='send';id='question-text';text='How are things?';owner_epoch=$script:status.epoch;observed_turn_id=''}}
    }}
    if ({q(scenario)} -ceq 'reconcile_readonly' -and $script:sent -eq 0 -and $script:status.reconcile) {{
        $script:sent++; return @{{kind='reconcile';id='recovery-text';text='Explain what happened without making changes.';owner_epoch=$script:status.epoch;observed_turn_id=''}}
    }}
    if ({q(scenario)} -ceq 'interrupt_repeated' -and $script:status.active -and $script:status.send -and $script:sent -lt 2) {{
        $script:sent++; return @{{kind='interrupt';id=('stop-'+$script:sent);owner_epoch=$script:status.epoch;observed_turn_id=$script:status.turn}}
    }}
    if ($script:question) {{ return @{{kind='question_answer';request_id=$script:question;answer=@{{choice=@('A')}}}} }}
    if ($script:status.active -and $script:status.send -and $script:sent -eq 0 -and {q(scenario)} -in @('steer','late_steer','steer_rejected','interrupt','interrupt_lost')) {{
        $script:sent++
        return @{{kind=if({q(scenario)} -in @('interrupt','interrupt_lost')){{'interrupt'}}else{{'send'}};id='input-1';text='Change focus';owner_epoch=$script:status.epoch;observed_turn_id=$script:status.turn}}
    }}
    if ({q(scenario)} -eq 'interrupt' -and $script:sent -eq 1 -and $script:status.send -and -not $script:status.active) {{
        $script:sent++; return @{{kind='send';id='input-2';text='Resume with new direction';owner_epoch=$script:status.epoch;observed_turn_id=''}}
    }}
    if ($script:tick -gt 12 -and (($script:status.send -and -not $script:status.active -and ({q(scenario)} -ne 'interrupt' -or $script:sent -eq 2)) -or $script:status.text -like 'blocked_*' -or ($script:status.reconcile -and ({q(scenario)} -ne 'reconcile_readonly' -or $script:sent -eq 1)))) {{ return @{{kind='close'}} }}
    return @()
}}
$result=Invoke-WdCodexConversationLoop -Agent {agent} -Model {model} -Effort {effort} -CliPath {q(python)} -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -SessionId test-session -Generation {'a'*40} -CompactStatePath {q(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt 'Initial directive' -ContinuationPrompt 'Continue directive' -ImagePath {q(image)} -Headless -RpcTimeoutSeconds 3 -TurnTimeoutSeconds {1 if scenario == 'timeout' else 5} -MaxIterations 160 -NetworkAccess ${str(workflow_permissions or compatibility).lower()} -AdditionalWritableRoots @({q(extra_root) if workflow_permissions else ''}) {'-CodexPermissionPosture existing_interactive' if compatibility else ''} -OnTransportReady {{param($Facts) $script:transportFacts.Add($Facts)}} -OnTurnFinalized {{param($Facts) $script:turnFacts.Add($Facts)}}
$again=$null
if ({q(scenario)} -in @('missing','interrupt_lost')) {{
    $again=Invoke-WdCodexConversationLoop -Agent {agent} -Model {model} -Effort {effort} -CliPath {q(python)} -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -SessionId restarted -Generation {'a'*40} -CompactStatePath {q(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt 'Must not replay' -Headless -MaxIterations 1
}}
@{{owner=$result;messages=$script:messages.ToArray();status=$script:status;restart=$again;transport=$script:transportFacts.ToArray();terminals=$script:turnFacts.ToArray();label=$script:label}} | ConvertTo-Json -Depth 20 -Compress
"""
    result = run(script, shell, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert len(value["transport"]) == 1
    transport = value["transport"][0]
    assert transport["agent"] == agent and transport["model"] == model
    assert transport["native_pid"] > 0 and transport["owner_pid"] > 0
    assert transport["native_parent_pid"] == transport["owner_pid"]
    assert transport["native_process_start_utc"].endswith("Z")
    assert model in value["label"] and effort in value["label"]
    calls = [json.loads(line) for line in (tmp_path / "rpc.jsonl").read_text().splitlines()]
    thread_method = "thread/resume" if scenario in ["resume", "paused_resume", "large_resume", "resume_missing_initial"] else "thread/start"
    assert len([x for x in calls if x.get("method") == thread_method]) == 1
    assert not any(x.get("method") == "thread/shellCommand" for x in calls)
    thread_call = next(x for x in calls if x.get("method") == thread_method)
    expected_roots = [str(tmp_path), str(runtime)] + ([str(extra_root)] if workflow_permissions else [])
    if compatibility:
        assert thread_call["params"]["sandbox"] == "danger-full-access"
        assert "sandbox_workspace_write" not in thread_call["params"]["config"]
        assert value["owner"]["cli_permission_posture"] == "danger-full-access_never"
        assert any("full access" in x["text"].lower() and "task authority" in x["text"].lower() for x in value["messages"] if x["role"] == "system")
    else:
        configured = thread_call["params"]["config"]["sandbox_workspace_write"]
        assert thread_call["params"]["sandbox"] == "workspace-write"
        assert value["owner"]["cli_permission_posture"] == "workspace-write_never"
        assert configured["writable_roots"] == expected_roots
        assert configured["network_access"] is workflow_permissions
    for call in [x for x in calls if x.get("method") == "turn/start"]:
        policy = call["params"]["sandboxPolicy"]
        if policy["type"] == "readOnly":
            assert policy["networkAccess"] is False
            assert "writableRoots" not in policy
        elif compatibility:
            assert policy == {"type":"dangerFullAccess"}
        else:
            assert policy["writableRoots"] == expected_roots
            assert policy["networkAccess"] is workflow_permissions
    identity = json.loads((tmp_path / ".codex-audit/wd-turn-loop/conversation.json").read_text())
    if compatibility:
        assert identity["codex_permission_posture"] == "existing_interactive"
    if scenario == "disconnect":
        assert identity["initial_context_delivered"] is False
    elif scenario != "paused_resume":
        assert identity["initial_context_delivered"] is True
    if scenario in ["steer", "late_steer"]:
        assert len([x for x in calls if x.get("method") == "turn/steer"]) == 1
    if scenario == "steer_rejected":
        assert any(x["role"] == "resolution" and not x["accepted"] for x in value["messages"])
        assert len([x for x in calls if x.get("method") == "turn/start"]) == 1
        assert not any(x["role"] == "user" and x["text"] == "Change focus" for x in value["messages"]), "rejected text must not appear as accepted user input"
    if scenario == "interrupt_repeated":
        assert len([x for x in calls if x.get("method") == "turn/interrupt"]) == 1
    if scenario == "resume_missing_initial":
        turn = next(x for x in calls if x.get("method") == "turn/start")
        assert any(x.get("type") == "localImage" and x.get("path") == str(image) for x in turn["params"]["input"])
        assert "Initial directive" in turn["params"]["input"][0]["text"]
    if scenario == "tool_progress":
        assert any(x["role"] == "tool" and "python -m pytest" in x["text"] for x in value["messages"])
    if scenario == "interrupt":
        assert len([x for x in calls if x.get("method") == "turn/start"]) == 2
        assert value["status"]["automatic"] is False
        assert len(list((tmp_path / ".codex-audit/wd-turn-loop").glob("*.interrupted.json"))) == 1
    if scenario in ["missing", "disconnect", "timeout", "output", "spontaneous", "interrupt_lost", "human_tool_missing", "reconcile_readonly"]:
        assert value["owner"]["status"] == "blocked"
        assert value["status"]["send"] is False
        if scenario != "spontaneous":
            assert list((tmp_path / ".codex-audit/wd-turn-loop").glob("*.pending"))
        if scenario in ["missing", "interrupt_lost"]:
            assert value["restart"]["last_disposition"] == "blocked_previous_unresolved_turn"
    else:
        assert value["owner"]["status"] == "stopped", value
        assert not list((tmp_path / ".codex-audit/wd-turn-loop").glob("*.pending"))
        if scenario != "paused_resume":
            assert any(x["role"] == "assistant" for x in value["messages"])
    if scenario in ["resume", "paused_resume", "large_resume"]:
        assert next(x for x in calls if x.get("method") == "thread/resume")["params"].get("excludeTurns") is True
        assert any("not loaded" in x["text"] for x in value["messages"])
    if scenario == "paused_resume":
        assert not any(x.get("method") == "turn/start" for x in calls)
        assert value["status"]["automatic"] is False
    if scenario in ["interrupt", "interrupt_lost", "paused_resume"]:
        identity = json.loads((tmp_path / ".codex-audit/wd-turn-loop/conversation.json").read_text())
        assert identity["automatic_enabled"] is False
    if scenario == "wake_during":
        assert len([x for x in calls if x.get("method") == "turn/start"]) == 2
        assert not (runtime / f"wake_{agent}").exists()
    if scenario in ["timeout", "output"]:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        pid = int((tmp_path / "descendant.pid").read_text())
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 0) == 0, "owned descendant survived shutdown"
            finally:
                kernel.CloseHandle(handle)
    if scenario == "question":
        answer = next(x for x in calls if x.get("id") == "question-1")
        assert answer["result"]["answers"] == {"choice":{"answers":["A"]}}
    if scenario == "approval":
        assert "error" in next(x for x in calls if x.get("id") == "approval-1")
    if scenario in ["human_chat", "human_tool_missing", "reconcile_readonly"]:
        assert len([x for x in calls if x.get("method") == "turn/start"]) == 2
    if scenario == "human_chat":
        assert value["owner"]["last_disposition"] == "native_chat_completed"
        assert value["owner"]["task_completion_verified"] is False
    if scenario in ["human_tool_missing", "reconcile_readonly"]:
        assert value["status"]["reconcile"] is True
        assert value["status"]["automatic"] is False
    if scenario == "reconcile_readonly":
        journal = tmp_path / ".codex-audit/wd-turn-loop"
        assert len(list(journal.glob("*.pending"))) == 1
        assert len(list(journal.glob("*.reconciliation.json"))) == 1


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("scenario", [
    pytest.param("lease", marks=pytest.mark.skipif(os.name != "nt", reason="Windows C-drive lease admission")),
    pytest.param("old_pending", marks=pytest.mark.skipif(os.name != "nt", reason="Windows C-drive pending admission")),
    "wrong_pins", "interactive",
])
def test_admission_before_native_start(tmp_path, shell, scenario):
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    old = tmp_path / "old-worktree"
    old_journal = old / ".codex-audit/wd-turn-loop"
    native_python = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python313/python.exe"
    python = native_python if native_python.is_file() else Path(sys.executable)
    if scenario == "old_pending":
        old_journal.mkdir(parents=True)
        pending = old_journal / ("turn-" + "d"*32 + ".pending")
        pending.write_text("{}")
        (runtime / ".wd-turn-codex-lead-1.owner.json").write_text(json.dumps({
            "schema":"wd.lane-turn-owner.v1", "agent":"codex-lead-1", "worktree":str(old),
            "journal_root":str(old_journal), "pending_path":str(pending), "status":"running",
            "session_id":"old", "generation":"b"*40,
        }))
    result = run(f"""
$ErrorActionPreference='Stop'
. {q(OLD)}
. {q(RUNNER)}
function New-WdConversationNativeProcess {{ throw 'native must not start' }}
$lease=$null
try {{
    if ({q(scenario)} -ceq 'lease') {{ $lease=[IO.File]::Open({q(runtime / '.wd-turn-codex-lead-1.lock')},[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None) }}
    try {{
        Invoke-WdCodexConversationLoop -CliPath {q(python)} -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -SessionId test -Generation {'a'*40} -CompactStatePath {q(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt 'test' -Model {q('wrong' if scenario == 'wrong_pins' else 'gpt-5.6-sol')} -ExistingInteractivePid {123 if scenario == 'interactive' else 0} | ConvertTo-Json -Compress
    }} catch {{ @{{error=$_.Exception.Message}} | ConvertTo-Json -Compress }}
}} finally {{ if ($null -ne $lease) {{ $lease.Dispose() }} }}
""", shell)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    if scenario == "lease":
        assert value["status"] == "blocked_duplicate_owner"
    elif scenario == "old_pending":
        assert value["last_disposition"] == "blocked_previous_unresolved_turn"
    elif scenario == "interactive":
        assert value["status"] == "unsupported_live_interactive"
    else:
        assert value["error"] == "conversation lane pins mismatch"
    assert not (tmp_path / ".codex-audit/wd-turn-loop").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_bounded_artifacts_preserve_pending(tmp_path, shell):
    for index in range(40):
        prefix = tmp_path / f"turn-{index:032x}"
        prefix.with_suffix(".checkpointed.json").write_text("{}")
        prefix.with_suffix(".state.json").write_text("{}")
    protected = tmp_path / ("turn-" + "f"*32 + ".pending")
    protected.write_text("evidence")
    for index in range(140):
        (tmp_path / f"rpc-epoch-{index:08d}.json").write_text("{}")
        (tmp_path / f"rpc-epoch-{index:08d}.result.json").write_text("{}")
    unresolved = tmp_path / "rpc-epoch-99999999.json"
    unresolved.write_text("unresolved")
    result = run(f"$ErrorActionPreference='Stop'; . {q(OLD)}; . {q(RUNNER)}; Remove-WdConversationArtifacts {q(tmp_path)}", shell)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list(tmp_path.glob("*.checkpointed.json"))) == 32
    assert len(list(tmp_path.glob("rpc-*.result.json"))) == 128
    assert protected.read_text() == "evidence"
    assert unresolved.read_text() == "unresolved"


@pytest.mark.skipif(os.name != "nt", reason="Windows native backend")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("scenario", ["complete", "interrupt", "missing", "reconcile_readonly"])
def test_tools_conversation_is_separate(tmp_path, shell, scenario):
    test_owned_fake_conversation(tmp_path, shell, scenario, agent="codex-tools-1")


@pytest.mark.parametrize("shell", SHELLS)
def test_explicit_workflow_posture(shell):
    result = run(f"""
$ErrorActionPreference='Stop'
. {q(OLD)}
. {q(RUNNER)}
Get-WdConversationThreadParameters -Worktree 'C:\\work' -RuntimeRoot 'C:\\bridge' -Model 'gpt-5.6-sol' -Effort ultra -AdditionalWritableRoots @('C:\\common-git','C:\\COMMON-GIT') -NetworkAccess $true | ConvertTo-Json -Depth 20 -Compress
""", shell)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["approvalPolicy"] == "never"
    assert value["sandbox"] == "workspace-write"
    assert value["config"]["sandbox_workspace_write"]["network_access"] is True
    assert value["config"]["sandbox_workspace_write"]["writable_roots"] == ["C:\\work", "C:\\bridge", "C:\\common-git"]


@pytest.mark.skipif(os.name != "nt", reason="Persistent Windows C-drive roots")
@pytest.mark.parametrize("shell", SHELLS)
def test_workflow_roots_validation(tmp_path, shell):
    runtime = tmp_path / "bridge"
    extra = tmp_path / "common-git"
    runtime.mkdir()
    extra.mkdir()
    result = run(f"""
$ErrorActionPreference='Stop'
. {q(OLD)}
. {q(RUNNER)}
$roots=@(Resolve-WdConversationWritableRoots -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -AdditionalWritableRoots @({q(extra)},{q(str(extra).upper())}))
$rejected=@()
foreach ($invalid in @('C:\\','relative','\\\\server\\share',{q(tmp_path / 'missing')})) {{
    try {{ Resolve-WdConversationWritableRoots -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -AdditionalWritableRoots @($invalid) | Out-Null; $rejected+=$false }} catch {{ $rejected+=$true }}
}}
@{{roots=$roots;rejected=$rejected}} | ConvertTo-Json -Compress
""", shell)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["roots"] == [str(tmp_path), str(runtime), str(extra)]
    assert value["rejected"] == [True, True, True, True]


@pytest.mark.skipif(os.name != "nt", reason="Windows native backend")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("scenario", ["complete", "reconcile_readonly"])
def test_workflow_posture_reaches_native_but_not_recovery(tmp_path, shell, scenario):
    test_owned_fake_conversation(tmp_path, shell, scenario, workflow_permissions=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows native backend")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("scenario", ["complete", "resume", "reconcile_readonly"])
def test_existing_interactive_posture_native_and_recovery(tmp_path, shell, scenario):
    test_owned_fake_conversation(tmp_path, shell, scenario, compatibility=True)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("scenario", [
    "tools", "wrong_pins", "network_off", "extra_roots",
    pytest.param("saved_mismatch", marks=pytest.mark.skipif(os.name != "nt", reason="Windows C-drive saved identity")),
    pytest.param("saved_legacy", marks=pytest.mark.skipif(os.name != "nt", reason="Windows C-drive saved identity")),
])
def test_existing_interactive_posture_rejected_before_writes(tmp_path, shell, scenario):
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    journal = tmp_path / ".codex-audit/wd-turn-loop"
    if scenario in ("saved_mismatch", "saved_legacy"):
        journal.mkdir(parents=True)
        saved = {"codex_permission_posture":"workspace_write"} if scenario == "saved_mismatch" else {}
        (journal / "conversation.json").write_text(json.dumps(saved))
    before = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = run(f"""
$ErrorActionPreference='Stop'
. {q(OLD)}
. {q(RUNNER)}
function New-WdConversationNativeProcess {{ throw 'native must not start' }}
try {{
    Invoke-WdCodexConversationLoop -Agent {'codex-tools-1' if scenario == 'tools' else 'codex-lead-1'} -Model {'gpt-5.6-terra' if scenario in ('tools','wrong_pins') else 'gpt-5.6-sol'} -Effort {'high' if scenario == 'tools' else 'ultra'} -CliPath {q(shell)} -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} -SessionId test -Generation {'a'*40} -CompactStatePath {q(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt test -CodexPermissionPosture existing_interactive -NetworkAccess ${str(scenario != 'network_off').lower()} -AdditionalWritableRoots @({q(runtime) if scenario == 'extra_roots' else ''}) | Out-Null
    @{{error='not rejected'}} | ConvertTo-Json -Compress
}} catch {{ @{{error=$_.Exception.Message}} | ConvertTo-Json -Compress }}
""", shell)
    assert result.returncode == 0, result.stdout + result.stderr
    error = json.loads(result.stdout)["error"]
    assert "pins mismatch" in error or "permission posture" in error, error
    after = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before

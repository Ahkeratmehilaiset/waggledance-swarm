"""Actual backend -> Tools readiness writer contracts, without model/live calls."""
from __future__ import annotations

import json
import importlib.util
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops/windows/reboot"
SHELLS = sorted({path for path in (shutil.which("powershell.exe"), shutil.which("pwsh")) if path})
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELLS, reason="Windows contained native transport required")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("shell", SHELLS)
def test_actual_hard_hold_retirement_starts_new_paused_owner(shell, tmp_path):
    """Real contained backend -> cold archive helpers -> real paused backend.

    Only isolated fixtures are retired. The operator-facing launcher and its
    console confirmation are deliberately not invoked by this model-owned test.
    """
    helper_spec = importlib.util.spec_from_file_location(
        "wd_owned_conversation_fixture", ROOT / "tests/tools/test_wd_codex_conversation.py"
    )
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    script, blocked, before_calls = helper.test_owned_fake_conversation(
        tmp_path, shell, "timeout", compatibility=True, return_script=True
    )
    assert blocked["owner"]["status"] == "blocked"
    journal = tmp_path / ".codex-audit/wd-turn-loop"
    runtime = tmp_path / "bridge"
    pointer = runtime / ".wd-turn-codex-lead-1.owner.json"
    original = {
        "journal/" + path.relative_to(journal).as_posix(): path.read_bytes()
        for path in journal.rglob("*") if path.is_file()
    }
    original["runtime-owner-pointer.json"] = pointer.read_bytes()
    assert any(path.endswith(".pending") for path in original)
    old_identity = json.loads(original["journal/conversation.json"])
    compact = tmp_path / ".codex-audit/wd-current-state.json"
    compact.write_text(json.dumps({
        "schema": "wd.lane-current.v1", "agent": "codex-lead-1",
        "worktree": str(tmp_path), "task_id": "original-unresolved-work",
        "status": "blocked", "next_action": "Review unknown external effects",
    }), encoding="utf-8")
    claims = runtime / "work_queue/claims/original.json"
    claims.parent.mkdir(parents=True)
    claims.write_text('{"task_id":"original-unresolved-work","status":"claimed"}')
    protected = {path: path.read_bytes() for path in (compact, claims)}
    command = f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(REBOOT / 'start-wd-agent.ps1')},[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'Recovery launcher source did not parse' }}
foreach ($name in @('Resolve-NormalizedPath','Assert-LanePathWithoutReparse','Read-Utf8LaneSnapshot',
    'Get-WdManagedAttemptInventory','Assert-WdManagedOwnerInactive','Get-WdManagedAttemptEvidence',
    'Enter-WdManagedAttemptLease','Invoke-WdManagedAttemptRetirement')) {{
    $definition=$ast.Find({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name}},$true)
    if ($null -eq $definition) {{ throw "Recovery function absent: $name" }}
    . ([scriptblock]::Create($definition.Extent.Text))
}}
$lease=Enter-WdManagedAttemptLease -RuntimeRoot {quote(runtime)} -Agent codex-lead-1
try {{
    # Use the actual process snapshot, including Windows' PID-zero sentinel.
    $evidence=Get-WdManagedAttemptEvidence -Agent codex-lead-1 -Worktree {quote(tmp_path)} -RuntimeRoot {quote(runtime)}
    Invoke-WdManagedAttemptRetirement -Evidence $evidence -ReviewedJournalDigest $evidence.digest -Reason 'Isolated regression: abandon uncertain fake-native attempt' | ConvertTo-Json -Depth 8 -Compress
}} finally {{ $lease.Dispose() }}
"""
    retired = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
    )
    assert retired.returncode == 0, retired.stdout + retired.stderr
    result = json.loads(retired.stdout)
    assert result["status"] == "managed_attempt_retired"
    assert not pointer.exists() and not journal.exists()
    archive = Path(result["archive_path"])
    manifest_bytes = Path(result["manifest_path"]).read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest().upper() == result["manifest_sha256"]
    manifest = json.loads(manifest_bytes)
    assert manifest["disposition"] == "operator_abandoned_uncertain_attempt"
    assert manifest["external_effects_unknown"] is True
    assert manifest["operator_handoff_required"] is True
    for key in ("task_completion_verified", "replay_performed", "bridge_claims_affected"):
        assert manifest[key] is False
    assert manifest["prior_session_id"] == "test-session"
    assert manifest["prior_generation"] == "a" * 40
    assert {entry["path"] for entry in manifest["inventory"]} == set(original)
    for entry in manifest["inventory"]:
        saved = (archive / entry["path"]).read_bytes()
        assert saved == original[entry["path"]]
        assert len(saved) == entry["length"]
        assert hashlib.sha256(saved).hexdigest().upper() == entry["sha256"]

    # This is a second actual owner process. No synthetic saved conversation is
    # planted and no automatic arming action is submitted to the new UI.
    fresh_script = script.replace("'timeout'", "'fresh_paused'")
    fresh_script = fresh_script.replace("-SessionId test-session", "-SessionId recovered-session")
    fresh_script = fresh_script.replace("-Generation " + "a" * 40, "-Generation " + "b" * 40)
    fresh = helper.run(fresh_script, shell, timeout=25)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    recovered = json.loads(fresh.stdout)
    assert recovered["owner"]["status"] == "stopped"
    assert recovered["owner"]["task_completion_verified"] is False
    assert recovered["status"]["automatic"] is False
    assert len(recovered["transport"]) == 1 and recovered["terminals"] == []
    calls = [json.loads(line) for line in (tmp_path / "rpc.jsonl").read_text().splitlines()]
    new_calls = calls[len(before_calls):]
    assert sum(call.get("method") == "thread/start" for call in new_calls) == 1
    assert not any(call.get("method") in ("thread/resume", "turn/start") for call in new_calls)
    identity = json.loads((journal / "conversation.json").read_text())
    assert identity["automatic_enabled"] is False
    assert identity["initial_context_delivered"] is False
    assert not list(journal.glob("*.pending"))
    assert json.loads((archive / "journal/conversation.json").read_bytes()) == old_identity
    assert Path(result["manifest_path"]).read_bytes() == manifest_bytes
    for logical_path, content in original.items():
        assert (archive / logical_path).read_bytes() == content
    for path, content in protected.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize("shell", SHELLS)
def test_actual_tools_callbacks_preserve_checkpoint_across_human_chat(shell, tmp_path):
    """One owned native child/thread, actual typed callbacks, actual writer/UI."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (tmp_path / ".codex-audit").mkdir()
    fake = tmp_path / "fake_native.py"
    fake.write_text('''import datetime, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
turns = 0
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    with (root / "calls.jsonl").open("a") as log: log.write(json.dumps(message)+"\\n")
    method = message.get("method", "")
    if method == "initialize": send({"id":message["id"],"result":{}})
    elif method == "thread/start": send({"id":message["id"],"result":{"thread":{"id":"one-owned-thread"}}})
    elif method == "turn/start":
        turns += 1
        native_id = "native-turn-" + str(turns)
        send({"id":message["id"],"result":{"turn":{"id":native_id}}})
        send({"method":"turn/started","params":{"threadId":"one-owned-thread","turn":{"id":native_id}}})
        if turns == 1:
            spec = json.loads(next((root / ".codex-audit/wd-turn-loop").glob("*.spec.json")).read_text(encoding="utf-8-sig"))
            stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            checkpoint = {"schema":"wd.lane-current.v1","agent":spec["agent"],"worktree":spec["worktree"],
                          "task_id":"scoped-checkpoint","status":"idle","next_action":"Wait for operator","updated_at_utc":stamp}
            pathlib.Path(spec["compact_state_path"]).write_text(json.dumps(checkpoint))
            receipt = {key:spec[key] for key in ["turn_id","agent","session_id","generation","compact_state_path"]}
            receipt.update(disposition="idle", task_id="scoped-checkpoint")
            pathlib.Path(spec["receipt_path"]).write_text(json.dumps(receipt))
        else:
            assert turns == 2, "No unexpected automatic replay/second consumer"
            assert "Explain the previous result" in message["params"]["input"][0]["text"]
        # The second turn is human chat only: no tool item, checkpoint, or receipt.
        send({"method":"item/agentMessage/delta","params":{"threadId":"one-owned-thread","turnId":native_id,"itemId":"answer-"+str(turns),"delta":"Recorded workflow" if turns == 1 else "Plain human explanation"}})
        send({"method":"turn/completed","params":{"threadId":"one-owned-thread","turn":{"id":native_id,"status":"completed"}}})
''', encoding="utf-8")
    native_python = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python313/python.exe"
    if not native_python.is_file():
        native_python = Path(sys.executable)
    ready = runtime / "tools-ready.json"
    command = f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
. {quote(REBOOT / 'Invoke-WdLaneTurnLoop.ps1')}
. {quote(REBOOT / 'Show-WdOperatorConversation.ps1')}
. {quote(REBOOT / 'Invoke-WdCodexConversationLoop.ps1')}
# Import only the three real writer definitions; never run the Tools launcher.
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(REBOOT / 'start-wd-tools-consumer.ps1')},[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'Tools writer source did not parse' }}
foreach ($name in @('ConvertTo-WdToolsUtc','Get-WdToolsConversationFact','Write-WdToolsConversationReadiness')) {{
    $definition=$ast.Find({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name}},$true)
    if ($null -eq $definition) {{ throw "Writer function absent: $name" }}
    . ([scriptblock]::Create($definition.Extent.Text))
}}
$script:nativeStarts=0
function New-WdConversationNativeProcess {{ param($CliPath,$Worktree)
    $script:nativeStarts++
    Initialize-WdConversationNativeType
    New-Object WdConversationProcess({quote(native_python)},[string[]]@({quote(fake)},{quote(tmp_path)}),$Worktree)
}}
function Start-AgentBridgeConsumerLoop {{ throw 'A second consumer must never start' }}
$script:records=[Collections.Generic.List[object]]::new()
$script:facts=[Collections.Generic.List[object]]::new()
$script:base=[ordered]@{{agent='codex-tools-1';session_id='tools-integration';run_id='tools-integration';generation={'a'*40!r}
    worktree={quote(tmp_path)};model='gpt-5.6-terra';reasoning_effort='high';pid=$PID
    process_start_utc=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToString('o')}}
$script:readyState=@{{transport_ready=$false;transport_ready_at_utc=$null;thread_id=$null;native_pid=$null;native_parent_pid=$null;native_process_start_utc=$null
    last_turn_id=$null;last_native_turn_id=$null;last_native_status=$null;last_turn_disposition=$null;last_turn_finalized_at_utc=$null;native_checkpoint_verified=$false
    last_checkpoint_turn_id=$null;last_checkpoint_native_turn_id=$null;last_checkpoint_disposition=$null;last_checkpoint_verified_at_utc=$null}}
$onReady={{param($Facts)
    $script:facts.Add($Facts)
    $script:records.Add((Write-WdToolsConversationReadiness -Path {quote(ready)} -BaseRecord $script:base -State $script:readyState -Facts $Facts -Phase transport))
}}
$onFinal={{param($Facts)
    $script:facts.Add($Facts)
    $script:records.Add((Write-WdToolsConversationReadiness -Path {quote(ready)} -BaseRecord $script:base -State $script:readyState -Facts $Facts -Phase terminal))
}}
$script:realFactory=${{function:New-WdOperatorConversationView}}
$script:realPump=${{function:Update-WdOperatorConversationView}}
$script:stage=0
function New-WdOperatorConversationView {{param([switch]$Headless,$AgentLabel,$Title,$ModelLabel)
    $script:view=& $script:realFactory @PSBoundParameters
    return $script:view
}}
function Update-WdOperatorConversationView {{param($View)
    & $script:realPump -View $View
    if ($View.State.CanSend -and -not $View.State.TurnActive) {{
        if ($script:records.Count -eq 2 -and $script:stage -eq 0) {{
            Submit-WdOperatorConversationAction -View $View -Kind automation_toggle -Enabled $false
            Submit-WdOperatorConversationAction -View $View -Kind send -Text 'Explain the previous result'
            $script:stage=1
        }} elseif ($script:records.Count -eq 3 -and $script:stage -eq 1) {{
            Submit-WdOperatorConversationAction -View $View -Kind close
            $script:stage=2
        }}
    }}
}}
$result=Invoke-WdCodexConversationLoop -Agent codex-tools-1 -Model gpt-5.6-terra -Effort high -CliPath {quote(native_python)} -Worktree {quote(tmp_path)} -RuntimeRoot {quote(runtime)} -SessionId tools-integration -Generation {'a'*40} -CompactStatePath {quote(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt 'Scoped fake workflow' -Headless -RpcTimeoutSeconds 3 -TurnTimeoutSeconds 8 -MaxIterations 200 -OnTransportReady $onReady -OnTurnFinalized $onFinal
[pscustomobject]@{{result=$result;records=$script:records.ToArray();facts=$script:facts.ToArray();nativeStarts=$script:nativeStarts;stage=$script:stage
    closed=$script:view.State.Closed;automatic=$script:view.State.AutomationEnabled;messages=$script:view.State.Messages.ToArray()}} | ConvertTo-Json -Depth 16 -Compress
"""
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-STA", "-Command", command],
                            cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)
    assert record["stage"] == 2 and record["closed"] is True, record
    assert record["nativeStarts"] == 1 and record["automatic"] is False
    assert record["result"]["last_disposition"] == "native_chat_completed"
    transport, checkpoint, chat = record["records"]
    assert transport["schema"] == "wd.tools-consumer-ready.v2"
    assert transport["status"] == "transport_ready" and transport["readiness_scope"] == "ui_transport_only"
    assert transport["native_checkpoint_verified"] is False and transport["last_checkpoint_turn_id"] is None
    assert transport["native_pid"] > 0 and transport["native_pid"] != transport["pid"]
    assert transport["native_parent_pid"] == transport["pid"]
    assert transport["thread_id"] == checkpoint["thread_id"] == chat["thread_id"] == "one-owned-thread"
    assert checkpoint["native_checkpoint_verified"] is True and checkpoint["last_turn_disposition"] == "idle"
    assert chat["native_checkpoint_verified"] is False and chat["last_turn_disposition"] == "native_chat_completed"
    assert chat["last_turn_id"] != checkpoint["last_turn_id"]
    for key in ["last_checkpoint_turn_id", "last_checkpoint_native_turn_id", "last_checkpoint_disposition", "last_checkpoint_verified_at_utc"]:
        assert chat[key] == checkpoint[key]
    assert all(row["task_completion_verified"] is False for row in record["records"])
    assert all(fact["model"] == "gpt-5.6-terra" and fact["effort"] == "high" for fact in record["facts"])
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert sum(call.get("method") == "thread/start" for call in calls) == 1
    assert sum(call.get("method") == "turn/start" for call in calls) == 2
    rows = [row for row in record["messages"] if row["Role"] == "user"]
    assert len(rows) == 1 and rows[0]["DeliveryState"] == "accepted"
    journal = tmp_path / ".codex-audit/wd-turn-loop"
    assert len(list(journal.glob("*.checkpointed.json"))) == 1
    assert len(list(journal.glob("*.chat-completed.json"))) == 1
    assert not list(journal.glob("*.pending"))
    assert json.loads(ready.read_text(encoding="utf-8-sig")) == chat

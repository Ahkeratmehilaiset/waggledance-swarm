"""Native failures notify Lead without requiring a successful model turn."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_wd_capacity_observer import ROOT, HOSTS, sha
from tools import bridge_capacity_collector as collector


def test_native_alert_output_is_sanitized_and_keeps_one_incident(tmp_path, monkeypatch, capsys):
    import io
    path = tmp_path / "observer.sqlite"
    payload = dict(session_id="11111111-2222-3333-4444-555555555555",
                   hook_event_name="StopFailure", error="rate_limit",
                   prompt="PRIVATE PROMPT", error_details="PRIVATE DETAILS")
    rows = []
    for _ in range(2):
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
        assert collector.main(["--store", str(path), "--claude-hook", "--emit-alert"]) == 0
        raw = capsys.readouterr().out
        assert "PRIVATE" not in raw
        rows.append(json.loads(raw))
    assert rows[0]["alert_id"] == rows[1]["alert_id"]
    assert rows[0]["automatic_retry_allowed"] is False
    collector.record_claude_hook(path, dict(session_id=payload["session_id"], hook_event_name="Stop"))
    assert collector.record_claude_hook(path, payload)["alert_id"] != rows[0]["alert_id"]


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("case", ["normal", "uncertain", "crash_after_append", "wrong_identity", "tamper"])
def test_notice_deduplicates_and_never_replays_uncertain_delivery(tmp_path, host, case):
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    bundle = tmp_path / "bundle"
    bin_path = bundle / "tools-bootstrap/.agent-bridge/bin"
    bin_path.mkdir(parents=True)
    thread = "11111111-2222-3333-4444-555555555555"
    alert = "a" * 32
    evidence = dict(pin_status="manifest_and_launcher_verified", observed_agent="fable-5",
                    native_conversation_id="wrong" if case == "wrong_identity" else thread)
    (bin_path / "Get-BridgeExecutionEvidence.ps1").write_text("'"+json.dumps(evidence)+"'")
    observed = dict(hook_event_name="StopFailure", native_thread_id=thread, alert_id=alert,
                    availability_state="rate_limited", observed_at="2026-09-20T00:00:00Z")
    event = dict(request_id="capacity-"+alert, agent="fable-5", payload=dict(alert_id=alert),
                 _bridge_delivery=dict(canonical_durable=True))
    writer = """
param($Agent,$Type,$Status,$To,$TaskId,$RequestId,$Message,$PayloadJson,[switch]$ReceiptJson)
Add-Content -LiteralPath $env:WD_TEST_COUNT 'write'
if($env:WD_TEST_CASE -eq 'uncertain'){throw 'Uncertain fixture write'}
if($env:WD_TEST_CASE -eq 'crash_after_append'){Set-Content $env:WD_TEST_APPENDED yes;throw 'Receipt lost'}
'EVENT'
""".replace("EVENT", json.dumps(event))
    (bin_path / "Write-AgentEvent.ps1").write_text(writer)
    reader = """
param([switch]$Raw,[switch]$NoAckReceived,[switch]$NoContinuity,$Tail)
if(Test-Path $env:WD_TEST_APPENDED){'EVENTS'}else{'[]'}
""".replace("EVENTS", json.dumps([{"agent":"system","payload":{}}, event]))
    (bin_path / "Read-AgentBridge.ps1").write_text(reader)
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps(dict(files={
        "tools-bootstrap/.agent-bridge/bin/"+p.name: sha(p) for p in bin_path.iterdir()
    })))
    anchor = sha(manifest)
    if case == "tamper":
        (bin_path / "Write-AgentEvent.ps1").write_text("throw 'must never execute'")
    fixture = tmp_path / "observation.json"
    fixture.write_text(json.dumps(observed))
    harness = tmp_path / "run.ps1"
    source = ROOT / "ops/windows/reboot/Invoke-WdCapacityObserver.ps1"
    harness.write_text(f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Parse error'}}
foreach($name in @('Assert-CapacityPath','Get-ObserverHash','Get-WdCapacityHookIdentity','Publish-WdCapacityAlert')){{
 $n=@($ast.FindAll({{param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name}},$true))
 . ([scriptblock]::Create($n[0].Extent.Text))
}}
$observation=Get-Content '{fixture}' -Raw|ConvertFrom-Json
for($i=0;$i -lt 2;$i++){{
 try{{Publish-WdCapacityAlert $observation '{tmp_path / "observer.sqlite"}'}}
 catch{{[Console]::Error.WriteLine($_.Exception.Message)}}
}}
""")
    env = dict(os.environ, WD_BRIDGE_BIN=str(bin_path), WD_REBOOT_EXPECTED_MANIFEST_HASH=anchor,
               WD_TEST_COUNT=str(tmp_path / "count"), WD_TEST_CASE=case,
               WD_TEST_APPENDED=str(tmp_path / "appended"))
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    count = tmp_path / "count"
    if case in ("wrong_identity", "tamper"):
        assert not count.exists()
        assert not (tmp_path / "bridge-alerts").exists()
    else:
        assert count.read_text().splitlines() == ["write"], result.stderr
        record = json.loads((tmp_path / "bridge-alerts" / (alert+".json")).read_text(encoding="utf-8-sig"))
        assert record["state"] == ("send_pending" if case == "uncertain" else "canonical"), result.stderr


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("case", ["recover", "repeat_cycle", "changed_after_resume", "foreign_setting", "foreign_identity", "changed_setting", "transport"])
def test_cron_guard_owns_only_its_override_and_resumes_after_success(tmp_path, host, case):
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    directory = tmp_path / ".claude"
    directory.mkdir()
    settings = directory / "settings.local.json"
    initial = dict(model="operator-selected", env={"OTHER": "preserved"})
    if case == "foreign_setting":
        initial["env"]["CLAUDE_CODE_DISABLE_CRON"] = "0"
    settings.write_text(json.dumps(initial))
    observed = dict(hook_event_name="StopFailure", availability_state="rate_limited",
                    native_thread_id="11111111-2222-3333-4444-555555555555",
                    alert_id="a" * 32, observed_at="2026-09-20T00:00:00Z")
    if case == "transport":
        observed["availability_state"] = "transport_error"
    fixture = tmp_path / "observation.json"
    fixture.write_text(json.dumps(observed))
    source = ROOT / "ops/windows/reboot/Invoke-WdCapacityObserver.ps1"
    harness = tmp_path / "guard.ps1"
    observed_agent = "claude-rco-1" if case == "foreign_identity" else "fable-5"
    harness.write_text(f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Parse error'}}
foreach($name in @('Assert-CapacityPath','Get-ObserverHash','Update-WdNativeCronGuard')){{
 $n=@($ast.FindAll({{param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name}},$true))
 . ([scriptblock]::Create($n[0].Extent.Text))
}}
function Get-WdCapacityHookIdentity {{[pscustomobject]@{{agent='{observed_agent}'}}}}
$o=Get-Content '{fixture}' -Raw|ConvertFrom-Json
try{{
 Update-WdNativeCronGuard $o '{tmp_path}' 'fable-5'
 Update-WdNativeCronGuard $o '{tmp_path}' 'fable-5'
 Copy-Item '{settings}' '{tmp_path / "paused.json"}'
 if('{case}' -ceq 'changed_setting'){{
   $s=Get-Content '{settings}' -Raw|ConvertFrom-Json
   $s.env.CLAUDE_CODE_DISABLE_CRON='0';$s|ConvertTo-Json|Set-Content '{settings}'
 }}
 $o.hook_event_name='Stop';$o.availability_state='successful_turn_observed'
 Update-WdNativeCronGuard $o '{tmp_path}' 'fable-5'
 if('{case}' -cin @('repeat_cycle','changed_after_resume')){{
   if('{case}' -ceq 'changed_after_resume'){{
     $s=Get-Content '{settings}' -Raw|ConvertFrom-Json
     $s.env.OTHER='operator-edited';$s|ConvertTo-Json|Set-Content '{settings}'
   }}
   $o.hook_event_name='StopFailure';$o.availability_state='rate_limited'
   Update-WdNativeCronGuard $o '{tmp_path}' 'fable-5'
   Copy-Item '{settings}' '{tmp_path / "second-pause.json"}'
   $o.hook_event_name='Stop';$o.availability_state='successful_turn_observed'
   Update-WdNativeCronGuard $o '{tmp_path}' 'fable-5'
   Update-WdNativeCronGuard $o '{tmp_path}' 'fable-5'
 }}
}}catch{{[Console]::Error.WriteLine($_.Exception.Message)}}
""")
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    final = json.loads(settings.read_text(encoding="utf-8-sig"))
    state = directory / "wd-capacity-cron-guard.json"
    if case == "transport":
        assert final == initial, result.stderr
        assert not state.exists(), result.stderr
    elif case in ("recover", "repeat_cycle"):
        assert final["env"].pop("CLAUDE_CODE_DISABLE_CRON", None) == "0", result.stderr
        assert final == initial
        owned = json.loads(state.read_text(encoding="utf-8-sig"))
        assert owned["state"] == "resumed"
        assert owned["resumed_settings_sha256"] == sha(settings)
        paused = json.loads((tmp_path / "paused.json").read_text(encoding="utf-8-sig"))
        assert paused["env"]["CLAUDE_CODE_DISABLE_CRON"] == "1"
        if case == "repeat_cycle":
            paused = json.loads((tmp_path / "second-pause.json").read_text(encoding="utf-8-sig"))
            assert paused["env"]["CLAUDE_CODE_DISABLE_CRON"] == "1"
        assert not result.stderr
    elif case == "changed_after_resume":
        assert final["env"]["OTHER"] == "operator-edited"
        assert final["env"]["CLAUDE_CODE_DISABLE_CRON"] == "0"
        assert not (tmp_path / "second-pause.json").exists()
        assert result.stderr
    elif case == "changed_setting":
        assert final["env"]["CLAUDE_CODE_DISABLE_CRON"] == "0"
        assert state.exists()
        assert "changed" in result.stderr
    else:
        assert final == initial
        assert not state.exists()
        assert result.stderr

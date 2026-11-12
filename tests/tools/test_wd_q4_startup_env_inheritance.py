"""Synthetic startup recall to child environment, without a live lane or Mind.

This checks process inheritance only; it does not prove a model read the value.
"""
import json
from pathlib import Path
import subprocess

import pytest

import test_wd_q4_shared_agent_startup as agent
import test_wd_q4_startup_packet_parity as packets
from test_wd_startup_recovery import load


pytestmark = pytest.mark.skipif(not agent.SHELL, reason="Windows PowerShell required")


@pytest.mark.parametrize("shell", agent.SHELLS)
@pytest.mark.parametrize("consumer", ["agent", "tools"])
@pytest.mark.parametrize("case", ["valid_v1", "valid_v2", "malformed", "record_failure"])
def test_synthetic_recall_to_inert_child_environment(tmp_path, shell, consumer, case):
    version = 2 if case == "valid_v2" else 1
    packet, raw = packets._packet(version, "malformed_json" if case == "malformed" else "valid")
    fx = agent.startup_read_fixture(
        tmp_path, packets.LANE, mode="producer_bytes", producer_bytes=raw,
        audit_case="file" if case == "record_failure" else "ok",
    )
    source = agent.LAUNCHER if consumer == "agent" else agent.ROOT / "ops/windows/reboot/start-wd-tools-consumer.ps1"
    variable = "startupMindRead" if consumer == "agent" else "toolsStartupMindRead"
    script = "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n"
    script += "if($PSVersionTable.PSEdition -eq 'Desktop'){$env:PSModulePath=Join-Path $PSHOME 'Modules'}\n"
    script += "\n".join(load(source, name) for name in agent.STARTUP_READ_FUNCTIONS)
    script += "\n" + load(source, "Clear-WdInheritedMindReadSettings")
    script += "\n" + fx["env_prelude"] + "\n"
    script += "$env:AGENT_BRIDGE_AGENT='codex-tools-1';$env:AGENT_BRIDGE_OWNER_TOKEN='synthetic-owner-token'\n"
    call = agent.startup_read_call(fx, packets.LANE).split("\nif ($null -eq $r)")[0]
    script += call.removeprefix(fx["env_prelude"] + "\n")
    script += "\n$" + variable + "=$r\n$sourcePath=" + agent._quote(source) + "\n"
    script += r'''
$env:WD_MIND_STARTUP_RECALL='synthetic stale content'
Clear-WdInheritedMindReadSettings
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($sourcePath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'source_parse'}
$needle="[Environment]::SetEnvironmentVariable('WD_MIND_STARTUP_RECALL'"
$guards=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and
    $node.Extent.Text.Contains($needle)},$true)|Sort-Object {$_.Extent.Text.Length})
if($guards.Count -ne 1 -or $guards[0].Extent.Text.Length -gt 1000){throw 'guard_scope'}
. ([scriptblock]::Create($guards[0].Extent.Text))
# The owned child executes only this fixed code; it never opens files or invokes a CLI.
$childCode=@'
$value=[Environment]::GetEnvironmentVariable('WD_MIND_STARTUP_RECALL','Process')
$hash='';$chars=0
if($null -ne $value){
  $h=[Security.Cryptography.SHA256]::Create()
  try{$hash=[BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($value))).Replace('-','').ToLowerInvariant()}
  finally{$h.Dispose()}
  $chars=$value.Length
}
@{present=($null -ne $value);sha256=$hash;chars=$chars}|ConvertTo-Json -Compress
'@
$encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCode))
$shellName=if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'}
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName=[IO.Path]::Combine($PSHOME,$shellName)
$start.Arguments='-NoLogo -NoProfile -NonInteractive -EncodedCommand '+$encoded
$start.UseShellExecute=$false;$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
$child=[Diagnostics.Process]::Start($start)
try{
  $out=$child.StandardOutput.ReadToEndAsync();$err=$child.StandardError.ReadToEndAsync()
  if(-not $child.WaitForExit(10000)){$child.Kill();throw 'inert_child_timeout'}
  if(-not [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($out,$err),2000)){throw 'inert_child_io_timeout'}
  if($child.ExitCode -ne 0){throw 'inert_child_failed'}
  $observed=$out.Result|ConvertFrom-Json
  @{outcome=$r.outcome;reason=$r.reason;record_written=$r.record_written;
    delivered_chars=$r.delivered_chars;delivered_sha256=$r.delivered_sha256;child=$observed}|ConvertTo-Json -Depth 4 -Compress
}finally{$child.Dispose()}
'''
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=agent.ROOT, capture_output=True, text=True, timeout=60, check=True,
    )
    assert packets.MARKER not in result.stdout + result.stderr
    assert packets.TOKEN not in result.stdout + result.stderr
    observed = json.loads(result.stdout.strip())
    success = case.startswith("valid")
    assert observed["child"]["present"] is success, observed
    if success:
        assert observed["outcome"] == "success" and observed["record_written"] is True
        assert observed["child"]["chars"] == observed["delivered_chars"] > 0
        assert observed["child"]["sha256"] == observed["delivered_sha256"]
        stored = json.loads(fx["record"].read_text(encoding="utf-8-sig"))
        assert stored["delivered_sha256"] == observed["child"]["sha256"]
        assert packets.MARKER not in json.dumps(stored)
    else:
        assert observed["outcome"] in ("refused", "unknown")
        assert observed["child"]["chars"] == 0 and observed["child"]["sha256"] == ""

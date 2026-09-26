"""Execute only the Tools reconciliation AST with inert process-operation mocks."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell") ])))


@pytest.mark.skipif(not SHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case,launches,stops,marker", [
    ("opaque-ready", 0, 0, "UNVERIFIABLE"),
    ("opaque-unknown", 0, 0, "CONFLICT"),
    ("opaque-whitespace", 0, 0, "CONFLICT"),
    ("opaque-stale-ready", 0, 0, "CONFLICT"),
    ("opaque-and-stale-wrapper", 0, 0, "CONFLICT"),
    ("opaque-and-healthy-wrapper", 0, 0, "CONFLICT"),
    ("empty", 1, 0, "LAUNCHED"),
    ("unrelated-opaque", 1, 0, "LAUNCHED"),
    ("self-only", 1, 0, "LAUNCHED"),
    ("healthy", 0, 0, ""),
    ("stale-wrapper", 1, 1, "LAUNCHED"),
    ("dry-empty", 0, 0, "WOULD-RELAUNCH"),
    ("dry-opaque", 0, 0, "CONFLICT"),
    # Several elevated shells plus one live, readiness-named wrapper.
    ("many-opaque-ready", 0, 0, "UNVERIFIABLE"),
    # Several elevated shells, readiness names a wrapper that is gone.
    ("many-opaque-owner-gone", 1, 0, "LAUNCHED"),
    ("opaque-owner-gone", 1, 0, "IGNORED"),
    ("dry-opaque-owner-gone", 0, 0, "WOULD-RELAUNCH"),
    # Owner gone but a readable wrapper exists: never ignore the opaque hosts.
    ("opaque-owner-gone-with-wrapper", 0, 0, "CONFLICT"),
    # Owner unknown (no or unreadable record): still blocks, as before.
    ("many-opaque-unknown", 0, 0, "CONFLICT"),
])
def test_opaque_process_is_not_absent(shell, case, launches, stops, marker):
    source = str(ROOT / "ops/windows/reboot/wd_supervisor.ps1").replace("'", "''")
    script = r"""
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'source parse failed' }
$command=$ast.Find({param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Invoke-WdToolsReconcileLocked'}, $true)
if ($null -eq $command) { throw 'missing actual reconciliation call' }
$body=@($command.CommandElements | Where-Object {$_ -is [Management.Automation.Language.ScriptBlockExpressionAst]})
if ($body.Count -ne 1) { throw 'unexpected reconciliation AST' }
# Never dot-source the supervisor or run any top-level code.
$action=[scriptblock]::Create($body[0].ScriptBlock.Extent.Text.Trim().Substring(1).TrimEnd().TrimEnd('}'))
$case='__CASE__'; $selfPid=99999
$script:fixture=@(); $script:launches=0; $script:stops=0
$actions=[Collections.Generic.List[string]]::new()
$toolsLauncher='launcher.ps1'; $configuredToolsLauncher='launcher.ps1'; $toolsConfig='config.json'
$toolsGeneration='generation'; $toolsAgent='codex-tools-1'; $toolsValidation=@{}
$tools=@{codex_timeout_seconds=60}; $readinessPath='never-read.json'
$toolsConversationSurface='headless'; $toolsPowerShellHost='never-start.exe'; $toolsConflictPath='never-write.json'
$Apply= -not $case.StartsWith('dry-')
if ($case -like 'many-opaque*') {
    foreach ($id in 50,51,52) { $script:fixture+= [pscustomobject]@{ProcessId=$id;Name='powershell.exe';CommandLine=$null} }
}
if ($case -like 'opaque*' -or $case -like 'dry-opaque*' -or $case -eq 'many-opaque-ready') {
    $line=if ($case -eq 'opaque-whitespace') {'   '} else {$null}
    $script:fixture+= [pscustomobject]@{ProcessId=42;Name='powershell.exe';CommandLine=$line}
}
if ($case -in @('healthy','stale-wrapper','opaque-and-stale-wrapper','opaque-and-healthy-wrapper','opaque-owner-gone-with-wrapper')) {
    $script:fixture+= [pscustomobject]@{ProcessId=43;Name='pwsh.exe';CommandLine='wrapper'}
}
if ($case -eq 'unrelated-opaque') {$script:fixture+= [pscustomobject]@{ProcessId=44;Name='other.exe';CommandLine=$null}}
if ($case -eq 'self-only') {$script:fixture+= [pscustomobject]@{ProcessId=$selfPid;Name='pwsh.exe';CommandLine=$null}}
function Get-CimInstance { param($ClassName,$ErrorAction) return $script:fixture }
function Assert-WdToolsLauncherGeneration { param($Processes,$AllowedPaths) }
function Test-NamedCommandLineArgument { param($CommandLine,$HostKind,$Name,$Value)
    if ($Name -eq 'Generation' -and $case -in @('stale-wrapper','opaque-and-stale-wrapper')) {return $false}
    return $CommandLine -eq 'wrapper'
}
function Test-ToolsWrapperReadiness { param($Process,$Tools,$Validation,$Generation,$ConfigPath,$ReadinessPath) return $case -in @('healthy','opaque-and-healthy-wrapper') }
function Test-ToolsReadinessTargetsProcess { param($Process,$Generation,$ReadinessPath) return $case -in @('opaque-ready','many-opaque-ready') -and $Process.ProcessId -eq 42 }
function Test-ToolsWrapperWithinStartupGrace { param($Process,$GraceSeconds) return $false }
function Test-ToolsReadinessOwnerGone { param($Processes,$ReadinessPath) return $case -like '*owner-gone*' }
function Get-AgentCommandProcesses { return @() }
function Assert-MachineToolsConfigExact { param($MachineConfigPath) }
function Assert-SupervisorBundleFileIntegrity { param($RelativePath) }
function Stop-VerifiedProcessTree { param($RootProcess,$InitialProcesses,$ConflictPath) $script:stops++; return 1 }
function Start-OutOfTaskJobPowerShell { param($HostPath,$Arguments,$Label,[switch]$VisibleTerminal) $script:launches++; $actions.Add('LAUNCHED') }
& $action
[pscustomobject]@{launches=$script:launches;stops=$script:stops;actions=($actions -join '|')} | ConvertTo-Json -Compress
""".replace("__SOURCE__", source).replace("__CASE__", case)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["launches"] == launches, observed
    assert observed["stops"] == stops, observed
    assert marker in observed["actions"], observed


@pytest.mark.skipif(not SHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case,expected", [
    ("pid-absent", True),
    ("pid-reused", True),
    ("pid-alive", False),
    ("pid-alive-within-skew", False),
    ("pid-duplicate", False),
    ("no-record", False),
    ("malformed-record", False),
    ("zero-pid", False),
])
def test_readiness_owner_gone_only_when_provably_gone(tmp_path, shell, case, expected):
    source = str(ROOT / "ops/windows/reboot/wd_supervisor.ps1").replace("'", "''")
    record = tmp_path / "ready.json"
    started = "2026-09-26T08:17:08.0000000Z"
    if case == "malformed-record":
        record.write_text("{not json", encoding="utf-8")
    elif case != "no-record":
        pid = 0 if case == "zero-pid" else 4242
        record.write_text(json.dumps({"pid": pid, "process_start_utc": started}), encoding="utf-8")
    live = {
        "pid-absent": "@([pscustomobject]@{ProcessId=7;CreationDate='2026-09-26T08:17:08Z'})",
        "pid-reused": "@([pscustomobject]@{ProcessId=4242;CreationDate='2026-09-26T09:30:00Z'})",
        "pid-alive": "@([pscustomobject]@{ProcessId=4242;CreationDate='2026-09-26T08:17:08Z'})",
        "pid-alive-within-skew": "@([pscustomobject]@{ProcessId=4242;CreationDate='2026-09-26T08:17:09Z'})",
        "pid-duplicate": "@([pscustomobject]@{ProcessId=4242;CreationDate='2026-09-26T09:30:00Z'},[pscustomobject]@{ProcessId=4242;CreationDate='2026-09-26T09:31:00Z'})",
    }.get(case, "@([pscustomobject]@{ProcessId=7;CreationDate='2026-09-26T08:17:08Z'})")
    script = r"""
$ErrorActionPreference = 'Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$null,[ref]$null)
foreach ($name in 'ConvertTo-SupervisorUtc','Test-ToolsReadinessOwnerGone') {
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}, $true)
    if ($null -eq $fn) { throw "missing $name" }
    . ([scriptblock]::Create($fn.Extent.Text))
}
[bool](Test-ToolsReadinessOwnerGone -Processes __LIVE__ -ReadinessPath '__RECORD__') | ConvertTo-Json -Compress
""".replace("__SOURCE__", source).replace("__LIVE__", live).replace("__RECORD__", str(record).replace("'", "''"))
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is expected

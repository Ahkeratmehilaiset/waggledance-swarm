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
    # Headless has no lifetime lock, so a healthy wrapper does not settle it.
    ("opaque-and-healthy-wrapper", 0, 0, "CONFLICT"),
    # A ready native-terminal wrapper holds the role and its lifetime lock: the
    # opaque hosts are reported unverified, and nothing is launched or stopped.
    ("native-opaque-and-healthy-wrapper", 0, 0,
     "UNVERIFIED 1 unreadable host(s) beside ready consumer-loop:codex-tools-1 pid=43; no process changes"),
    ("native-many-opaque-and-healthy-wrapper", 0, 0, "UNVERIFIED 4 unreadable host(s)"),
    ("dry-native-opaque-and-healthy-wrapper", 0, 0, "UNVERIFIED 1 unreadable host(s)"),
    # Anything short of exactly that still blocks, without touching a process.
    ("native-opaque-and-starting-wrapper", 0, 0, "unreadable host ownership"),
    ("native-opaque-and-stale-wrapper", 0, 0, "unreadable host ownership"),
    ("native-opaque-healthy-and-stale-wrapper", 0, 0, "unreadable host ownership"),
    ("native-opaque-healthy-wrapper-and-legacy", 0, 0, "unreadable host ownership"),
    ("native-opaque-healthy-and-second-exact-wrapper", 0, 0, "unreadable host ownership"),
    # The readiness record naming an opaque host rules the exception out.
    ("native-opaque-target-and-healthy-wrapper", 0, 0, "unreadable host ownership"),
    # Only native_terminal takes the lifetime lock; every other surface blocks.
    ("none-opaque-and-healthy-wrapper", 0, 0, "unreadable host ownership"),
    ("local_window-opaque-and-healthy-wrapper", 0, 0, "unreadable host ownership"),
    ("dry-native-opaque-and-starting-wrapper", 0, 0, "unreadable host ownership"),
    ("dry-native-opaque-healthy-wrapper-and-legacy", 0, 0, "unreadable host ownership"),
    ("dry-native-opaque-healthy-and-second-exact-wrapper", 0, 0, "unreadable host ownership"),
    ("dry-native-opaque-target-and-healthy-wrapper", 0, 0, "unreadable host ownership"),
    ("dry-none-opaque-and-healthy-wrapper", 0, 0, "unreadable host ownership"),
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
    # The owner-gone relaxation relies on the native-terminal lifetime lock.
    ("headless-opaque-owner-gone", 0, 0, "CONFLICT"),
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
# The supervisor's own final gate: any CONFLICT action throws, so the task exits 1.
$top=@($ast.EndBlock.Statements); $gateIndex=-1
for ($k=0; $k -lt $top.Count; $k++) {
    if ($top[$k] -is [Management.Automation.Language.AssignmentStatementAst] -and $top[$k].Left.Extent.Text -ceq '$conflicts') { $gateIndex=$k }
}
if ($gateIndex -lt 0 -or $gateIndex + 1 -ge $top.Count -or $top[$gateIndex + 1] -isnot [Management.Automation.Language.IfStatementAst]) { throw 'missing final conflict gate' }
$gate=[scriptblock]::Create($top[$gateIndex].Extent.Text + [Environment]::NewLine + $top[$gateIndex + 1].Extent.Text)
$case='__CASE__'; $selfPid=99999
$script:fixture=@(); $script:launches=0; $script:stops=0
$actions=[Collections.Generic.List[string]]::new()
$toolsLauncher='launcher.ps1'; $configuredToolsLauncher='launcher.ps1'; $toolsConfig='config.json'
$toolsGeneration='generation'; $toolsAgent='codex-tools-1'; $toolsValidation=@{}
$tools=@{codex_timeout_seconds=60}; $readinessPath='never-read.json'
$toolsConversationSurface=if ($case -like 'headless-*') {'headless'} elseif ($case -like '*none-opaque*') {'none'} elseif ($case -like '*local_window-*') {'local_window'} elseif ($case -like '*owner-gone*' -or $case -like '*native-*') {'native_terminal'} else {'headless'}
$readyFamily = $case -like '*native-*' -or $case -like '*none-opaque*' -or $case -like '*local_window-*'
$toolsPowerShellHost='never-start.exe'; $toolsConflictPath='never-write.json'
$Apply= -not $case.StartsWith('dry-')
if ($case -like 'many-opaque*') {
    foreach ($id in 50,51,52) { $script:fixture+= [pscustomobject]@{ProcessId=$id;Name='powershell.exe';CommandLine=$null} }
}
if ($case -like 'opaque*' -or $case -like 'dry-opaque*' -or $case -like 'headless-opaque*' -or $case -eq 'many-opaque-ready') {
    $line=if ($case -eq 'opaque-whitespace') {'   '} else {$null}
    $script:fixture+= [pscustomobject]@{ProcessId=42;Name='powershell.exe';CommandLine=$line}
}
if ($case -in @('healthy','stale-wrapper','opaque-and-stale-wrapper','opaque-and-healthy-wrapper','opaque-owner-gone-with-wrapper')) {
    $script:fixture+= [pscustomobject]@{ProcessId=43;Name='pwsh.exe';CommandLine='wrapper'}
}
if ($readyFamily) {
    $script:fixture+= [pscustomobject]@{ProcessId=42;Name='powershell.exe';CommandLine=$null}
    if ($case -like '*native-many-*') {
        foreach ($id in 50,51,52) { $script:fixture+= [pscustomobject]@{ProcessId=$id;Name='pwsh.exe';CommandLine=$null} }
    }
    $script:fixture+= [pscustomobject]@{ProcessId=43;Name='powershell.exe';CommandLine='wrapper'}
    if ($case -eq 'native-opaque-healthy-and-stale-wrapper') {
        $script:fixture+= [pscustomobject]@{ProcessId=45;Name='powershell.exe';CommandLine='wrapper-stale'}
    }
    if ($case -like '*second-exact-wrapper') {
        $script:fixture+= [pscustomobject]@{ProcessId=45;Name='powershell.exe';CommandLine='wrapper'}
    }
}
if ($case -eq 'unrelated-opaque') {$script:fixture+= [pscustomobject]@{ProcessId=44;Name='other.exe';CommandLine=$null}}
if ($case -eq 'self-only') {$script:fixture+= [pscustomobject]@{ProcessId=$selfPid;Name='pwsh.exe';CommandLine=$null}}
function Get-CimInstance { param($ClassName,$ErrorAction) return $script:fixture }
function Assert-WdToolsLauncherGeneration { param($Processes,$AllowedPaths) }
function Test-NamedCommandLineArgument { param($CommandLine,$HostKind,$Name,$Value)
    if ($Name -eq 'Generation' -and ($case -in @('stale-wrapper','opaque-and-stale-wrapper','native-opaque-and-stale-wrapper') -or $CommandLine -eq 'wrapper-stale')) {return $false}
    return $CommandLine -in @('wrapper','wrapper-stale')
}
function Test-ToolsWrapperReadiness { param($Process,$Tools,$Validation,$Generation,$ConfigPath,$ReadinessPath)
    return $case -in @('healthy','opaque-and-healthy-wrapper') -or ($readyFamily -and $case -like '*healthy*' -and $Process.ProcessId -eq 43)
}
function Test-ToolsReadinessTargetsProcess { param($Process,$Generation,$ReadinessPath)
    return ($case -in @('opaque-ready','many-opaque-ready') -or $case -like '*opaque-target-*') -and $Process.ProcessId -eq 42
}
function Test-ToolsWrapperWithinStartupGrace { param($Process,$GraceSeconds) return $false }
function Test-ToolsReadinessOwnerGone { param($Processes,$ReadinessPath) return $case -like '*owner-gone*' }
function Get-AgentCommandProcesses {
    if ($case -like '*-and-legacy') { return @([pscustomobject]@{ProcessId=46;Name='powershell.exe';CommandLine='legacy'}) }
    return @()
}
function Assert-MachineToolsConfigExact { param($MachineConfigPath) }
function Assert-SupervisorBundleFileIntegrity { param($RelativePath) }
function Stop-VerifiedProcessTree { param($RootProcess,$InitialProcesses,$ConflictPath) $script:stops++; return 1 }
function Start-OutOfTaskJobPowerShell { param($HostPath,$Arguments,$Label,[switch]$VisibleTerminal) $script:launches++; $actions.Add('LAUNCHED') }
& $action
[pscustomobject]@{launches=$script:launches;stops=$script:stops;actions=($actions -join '|')} | ConvertTo-Json -Compress
& $gate
""".replace("__SOURCE__", source).replace("__CASE__", case)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    conflict = "CONFLICT" in marker or "ownership" in marker
    observed = json.loads(result.stdout.splitlines()[0])
    assert observed["launches"] == launches, observed
    assert observed["stops"] == stops, observed
    assert marker in observed["actions"], observed
    if conflict:
        assert result.returncode == 1 and "supervisor reconciliation conflict" in result.stderr, result.stderr
    else:
        assert "CONFLICT" not in observed["actions"], observed
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not SHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case,expected", [
    ("pid-absent", True),
    ("pid-reused", True),
    ("pid-alive", False),
    ("pid-alive-within-skew", False),
    ("pid-reused-3s", True),
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
        "pid-reused-3s": "@([pscustomobject]@{ProcessId=4242;CreationDate='2026-09-26T08:17:11Z'})",
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


STARTED = "2026-09-26T08:17:08.0000000Z"


@pytest.mark.skipif(not SHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case,record_pid,record_generation,launches,marker", [
    ("live-opaque-old-generation", 42, "old-generation", 0, "CONFLICT"),
    ("dead-owner-unrelated-opaque", 4242, "generation", 1, "IGNORED"),
    ("no-record-opaque", None, None, 0, "CONFLICT"),
])
def test_owner_gone_gate_is_wired_to_the_full_snapshot_and_the_real_record(
        tmp_path, shell, case, record_pid, record_generation, launches, marker):
    source = str(ROOT / "ops/windows/reboot/wd_supervisor.ps1").replace("'", "''")
    record = tmp_path / "ready.json"
    if record_pid is not None:
        record.write_text(json.dumps({"pid": record_pid, "generation": record_generation,
                                      "process_start_utc": STARTED}), encoding="utf-8")
    script = r"""
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'source parse failed' }
foreach ($name in 'ConvertTo-SupervisorUtc','Test-ToolsReadinessTargetsProcess','Test-ToolsReadinessOwnerGone') {
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}, $true)
    if ($null -eq $fn) { throw "missing $name" }
    . ([scriptblock]::Create($fn.Extent.Text))
}
$command=$ast.Find({param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Invoke-WdToolsReconcileLocked'}, $true)
if ($null -eq $command) { throw 'missing actual reconciliation call' }
$body=@($command.CommandElements | Where-Object {$_ -is [Management.Automation.Language.ScriptBlockExpressionAst]})
if ($body.Count -ne 1) { throw 'unexpected reconciliation AST' }
$action=[scriptblock]::Create($body[0].ScriptBlock.Extent.Text.Trim().Substring(1).TrimEnd().TrimEnd('}'))
$case='__CASE__'; $selfPid=99999
$script:fixture=@(); $script:launches=0; $script:stops=0
$actions=[Collections.Generic.List[string]]::new()
$toolsLauncher='launcher.ps1'; $configuredToolsLauncher='launcher.ps1'; $toolsConfig='config.json'
$toolsGeneration='generation'; $toolsAgent='codex-tools-1'; $toolsValidation=@{}
$tools=@{codex_timeout_seconds=60}; $readinessPath='__RECORD__'
$toolsConversationSurface='native_terminal'; $toolsPowerShellHost='never-start.exe'; $toolsConflictPath='never-write.json'
$Apply=$true
$started='2026-09-26T08:17:08.0000000Z'
if ($case -eq 'live-opaque-old-generation') {
    $script:fixture+=[pscustomobject]@{ProcessId=42;Name='powershell.exe';CommandLine=$null;CreationDate=$started}
}
if ($case -eq 'dead-owner-unrelated-opaque') {
    foreach ($id in 50,51) { $script:fixture+=[pscustomobject]@{ProcessId=$id;Name='powershell.exe';CommandLine=$null;CreationDate=$started} }
}
if ($case -eq 'no-record-opaque') {
    $script:fixture+=[pscustomobject]@{ProcessId=42;Name='powershell.exe';CommandLine=$null;CreationDate=$started}
}
function Get-CimInstance { param($ClassName,$ErrorAction) return $script:fixture }
function Assert-WdToolsLauncherGeneration { param($Processes,$AllowedPaths) }
function Test-NamedCommandLineArgument { param($CommandLine,$HostKind,$Name,$Value) return $CommandLine -eq 'wrapper' }
function Test-ToolsWrapperReadiness { param($Process,$Tools,$Validation,$Generation,$ConfigPath,$ReadinessPath) return $false }
function Test-ToolsWrapperWithinStartupGrace { param($Process,$GraceSeconds) return $false }
function Get-AgentCommandProcesses { return @() }
function Assert-MachineToolsConfigExact { param($MachineConfigPath) }
function Assert-SupervisorBundleFileIntegrity { param($RelativePath) }
function Stop-VerifiedProcessTree { param($RootProcess,$InitialProcesses,$ConflictPath) $script:stops++; return 1 }
function Start-OutOfTaskJobPowerShell { param($HostPath,$Arguments,$Label,[switch]$VisibleTerminal) $script:launches++; $actions.Add('LAUNCHED') }
& $action
[pscustomobject]@{launches=$script:launches;stops=$script:stops;actions=($actions -join '|')} | ConvertTo-Json -Compress
""".replace("__SOURCE__", source).replace("__CASE__", case).replace("__RECORD__", str(record).replace("'", "''"))
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["launches"] == launches, observed
    assert observed["stops"] == 0, observed
    assert marker in observed["actions"], observed


CONSUMER = ROOT / "ops/windows/reboot/start-wd-tools-consumer.ps1"
LOCK_OPEN = ("$nativeToolsLease = [IO.File]::Open($nativeLock,[IO.FileMode]::OpenOrCreate,"
             "[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)")
LOCK_RELEASE = "} finally { if ($null -ne $nativeToolsLease) { $nativeToolsLease.Dispose() } }"


def test_a_ready_native_wrapper_holds_the_lifetime_lock():
    """The supervisor's ready-wrapper case rests on this ordering in the consumer."""
    text = CONSUMER.read_text(encoding="utf-8")
    assert text.count(LOCK_OPEN) == 1 and text.count(LOCK_RELEASE) == 1
    assert text.count("$nativeToolsLease.Dispose()") == 1           # released only at the very end
    opened = text.index(LOCK_OPEN)
    terminal = text.index("Invoke-WdNativeToolsTerminal -Saved $nativeToolsSaved")
    assert opened < terminal < text.index(LOCK_RELEASE)
    assert text.rfind("\ntry {\n", 0, opened) == text.rfind("\ntry {\n", 0, terminal)   # one top-level try spans both
    function = text[text.index("function Invoke-WdNativeToolsTerminal"):]
    function = function[:function.index("\nfunction ")]
    ready = function.index("$record.schema='wd.tools-consumer-ready.v3'")
    assert ready < function.index("Write-WdTurnJson $ReadinessPath $record")
    assert function.index("Write-WdTurnJson $ReadinessPath $record") < function.index("$native.WaitForExit()")


@pytest.mark.skipif(not SHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
def test_a_second_opener_of_the_lock_is_refused(tmp_path, shell):
    lock = str(tmp_path / ".wd-turn-codex-tools-1.lock").replace("'", "''")
    opener = LOCK_OPEN.split(" = ", 1)[1]
    script = f"""
$ErrorActionPreference = 'Stop'
$nativeLock = '{lock}'
$first = {opener}
try {{
    try {{ $second = {opener}; $second.Dispose(); 'second-opened' }}
    catch [System.IO.IOException] {{ 'refused' }}
}} finally {{ $first.Dispose() }}
$third = {opener}; $third.Dispose(); 'reopened-after-release'
"""
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["refused", "reopened-after-release"]


@pytest.mark.skipif(not SHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case,expected", [
    ("same-pid-and-start", True),
    ("start-within-skew", True),
    ("pid-reused", False),
    ("record-without-start", False),
    ("process-without-start", False),
    ("other-generation", False),
    ("other-pid", False),
    ("no-record", False),
])
def test_readiness_names_an_opaque_host_only_with_its_start_time(tmp_path, shell, case, expected):
    source = str(ROOT / "ops/windows/reboot/wd_supervisor.ps1").replace("'", "''")
    record = tmp_path / "ready.json"
    if case != "no-record":
        body = {"pid": 42, "generation": "other" if case == "other-generation" else "generation"}
        if case != "record-without-start":
            body["process_start_utc"] = STARTED
        record.write_text(json.dumps(body), encoding="utf-8")
    created = {"start-within-skew": "'2026-09-26T08:17:09Z'", "pid-reused": "'2026-09-26T08:17:11Z'",
               "process-without-start": "$null"}.get(case, "'2026-09-26T08:17:08Z'")
    pid = 43 if case == "other-pid" else 42
    script = r"""
$ErrorActionPreference = 'Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$null,[ref]$null)
foreach ($name in 'ConvertTo-SupervisorUtc','Test-ToolsReadinessTargetsProcess') {
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}, $true)
    if ($null -eq $fn) { throw "missing $name" }
    . ([scriptblock]::Create($fn.Extent.Text))
}
$process=[pscustomobject]@{ProcessId=__PID__;Name='powershell.exe';CommandLine=$null;CreationDate=__CREATED__}
[bool](Test-ToolsReadinessTargetsProcess -Process $process -Generation 'generation' -ReadinessPath '__RECORD__') | ConvertTo-Json -Compress
""".replace("__SOURCE__", source).replace("__PID__", str(pid)).replace("__CREATED__", created) \
        .replace("__RECORD__", str(record).replace("'", "''"))
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is expected


@pytest.mark.skipif(not SHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("held", [True, False])
def test_a_refused_second_consumer_leaves_readiness_byte_identical(tmp_path, shell, held):
    """Run the consumer's real code from the lifetime lock to its first readiness change.

    With the lock held (a live wrapper), that code must fail at the lease and leave the
    ready wrapper's readiness file byte-identical. The twin without a holder must pass the
    lease and reach the readiness removal, so the refusal is not vacuous.
    """
    text = CONSUMER.read_text(encoding="utf-8")
    start = text.index("if ($conversationSurface -ceq 'native_terminal') {\n    if ([Console]::IsInputRedirected)")
    anchor = "    Remove-Item -LiteralPath $readinessPath -Force -ErrorAction Stop\n}\n"
    end = text.index(anchor, start) + len(anchor)
    code = text[start:end]
    assert LOCK_OPEN in code and code.count("[Console]::IsInputRedirected") == 1
    assert code.index(LOCK_OPEN) < code.index("Remove-Item -LiteralPath $readinessPath")
    # pytest's stdin is not a console; that guard is the only line substituted.
    code = code.replace("[Console]::IsInputRedirected", "$false")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    readiness_root = tmp_path / "ready"
    readiness_root.mkdir()
    readiness = readiness_root / "codex-tools-1.json"
    original = b'{"schema":"wd.tools-consumer-ready.v3","pid":4242}\r\n'
    readiness.write_bytes(original)
    slice_path = tmp_path / "slice.ps1"
    slice_path.write_text(code, encoding="utf-8")
    quote = lambda p: str(p).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$conversationSurface = 'native_terminal'
$runtimeRoot = '{quote(runtime)}'
$readinessRoot = '{quote(readiness_root)}'
$readinessPath = '{quote(readiness)}'
$worktree = '{quote(tmp_path)}'
$verifiedConversationCode = @{{ 'Invoke-WdLaneTurnLoop.ps1' = '' }}
function Get-WdNativeToolsRuntimeFunctions {{ param($VerifiedCode) return {{ }} }}
function Assert-WdTurnPath {{ param($Path) return $Path }}
function Get-WdPreviousTurnBlocker {{ param($Path, $Agent) return $null }}
function Get-WdNativeToolsResumeState {{ param($Worktree) return $null }}
$holder = $null
if (${'true' if held else 'false'}) {{
    $holder = [IO.File]::Open((Join-Path $runtimeRoot '.wd-turn-codex-tools-1.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
}}
$nativeToolsLease = $null
try {{
    . '{quote(slice_path)}'
    'passed-lease'
}} catch [System.IO.IOException] {{
    'refused-at-lease'
}} finally {{
    if ($null -ne $nativeToolsLease) {{ $nativeToolsLease.Dispose() }}
    if ($null -ne $holder) {{ $holder.Dispose() }}
}}
"""
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    if held:
        assert result.stdout.split() == ["refused-at-lease"]
        assert readiness.read_bytes() == original
    else:
        assert result.stdout.split() == ["passed-lease"]
        assert not readiness.exists()            # the live path goes on to replace readiness

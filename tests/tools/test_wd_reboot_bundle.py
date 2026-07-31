from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
POWERSHELL = (
    shutil.which("pwsh")
    or shutil.which("powershell")
    or shutil.which("powershell.exe")
)


def _run_powershell(script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    return subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=check,
    )


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_all_reboot_powershell_files_parse() -> None:
    files = sorted(REBOOT.glob("*.ps1"))
    assert files
    for path in files:
        quoted = str(path).replace("'", "''")
        result = _run_powershell(
            "$tokens=$null; $errors=$null; "
            f"[void][System.Management.Automation.Language.Parser]::ParseFile('{quoted}',"
            "[ref]$tokens,[ref]$errors); "
            "if ($errors.Count) { $errors | ForEach-Object { "
            "Write-Error $_.Message }; exit 1 }"
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


def test_fleet_manifest_pins_exact_persistent_generations() -> None:
    manifest = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["primary_repo_root"] == r"C:\Python\project2"
    assert manifest["runtime_root"] == r"C:\Python\project2-master\.agent-bridge"
    assert "Invoke-WdToolsCodex.ps1" in (
        manifest["deployment"]["required_bundle_files"]
    )

    lanes = {lane["agent"]: lane for lane in manifest["lanes"]}
    assert set(lanes) == {
        "codex-lead-1",
        "claude-rco-1",
        "claude-rco-2",
        "fable-5",
    }
    assert lanes["codex-lead-1"]["head"] == (
        "69926c874779aef2859c09cbe80d9c81127c2986"
    )
    assert lanes["claude-rco-1"]["head"] == (
        "5524acaa94f853168bdf79e656c5f083db1b10fc"
    )
    assert lanes["claude-rco-2"]["head"] == (
        "5524acaa94f853168bdf79e656c5f083db1b10fc"
    )
    assert lanes["fable-5"]["head"] == (
        "99f14f92fd32d54ea2b6693973adf22829a0cbc5"
    )
    assert len({lane["agent_uuid"] for lane in lanes.values()}) == 4
    for lane in lanes.values():
        assert lane["worktree"].startswith("C:\\")
        assert len(lane["head"]) == 40
        assert "model" not in lane


def test_claude_launchers_use_provider_default_without_old_opus_pin() -> None:
    agent_launcher = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    bundle_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(REBOOT.glob("*"))
        if path.is_file()
    )
    assert "claude-opus-" not in bundle_text.lower()
    assert "--model" not in agent_launcher.lower()
    assert "'--dangerously-skip-permissions'" in agent_launcher
    assert "model_selection = 'provider_default'" in agent_launcher
    assert "legacy Opus or model labels" in agent_launcher
    assert "not a pin or current runtime identity" in agent_launcher


def test_reboot_path_cannot_create_git_worktrees_or_rearm_merge_driver() -> None:
    launch_text = "\n".join(
        (REBOOT / name).read_text(encoding="utf-8")
        for name in (
            "start-wd-all.ps1",
            "start-wd-agent.ps1",
            "start-wd-tools-consumer.ps1",
        )
    )
    supervisor = (REBOOT / "wd_supervisor.ps1").read_text(encoding="utf-8")

    assert "Start-AgentBridgeWorktreeSession.ps1" not in launch_text
    assert "New-AgentBridgeWorktree.ps1" not in launch_text
    assert "Enable-ScheduledTask" not in supervisor
    assert "Write-AgentEvent.ps1" not in supervisor
    assert "Restore-BridgeSpool.ps1" not in supervisor
    assert "Watch-AgentsBridgeNudge.ps1" not in supervisor
    assert "WindowsApps\\Microsoft.PowerShell_" not in supervisor
    assert "supervisor_hold_task" not in launch_text
    assert "(?:^|\\s)-Apply(?:\\s|$)" not in launch_text
    assert "(?:^|\\s)-Apply(?:\\s|$)" not in supervisor
    assert "Test-ContainsApplySwitch $commandLine" in launch_text
    assert "-Loop\\s+-PollSeconds\\s+120" in launch_text
    assert "-Loop\\s+-PollSeconds\\s+120" in supervisor
    assert "required watcher source is missing" in supervisor
    assert "required tools consumer launcher is missing" in supervisor
    assert "WARN tools consumer launcher missing" not in supervisor
    assert (
        "(Test-TextContains ([string]$_.CommandLine) $ScriptName) -and"
        in supervisor
    )
    assert (
        "(Test-TextContains ([string]$_.CommandLine) $watcherScript) -and"
        in supervisor
    )
    assert "CmdletizationQuery_NotFound" in supervisor
    assert "Get-OptionalScheduledTask" in supervisor
    assert "$allAgentWatchers = @(" in supervisor
    assert "$legacyConsumers = @(" in supervisor


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_legacy_driver_proof_rejects_suffixes_commands_and_apply(tmp_path: Path) -> None:
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Test-LegacyDriverProvenNonApply'
  }},
  $true
)
. ([scriptblock]::Create($functionAst.Extent.Text))
function New-FakeTask(
  [string] $Arguments,
  [string] $Execute = 'powershell.exe'
) {{
  [pscustomobject]@{{
    Actions = @([pscustomobject]@{{
      Execute = $Execute
      Arguments = $Arguments
    }})
  }}
}}
$expected = 'C:\\Python\\Invoke-BridgeMergeDriver.ps1'
$safe = '-NoProfile -ExecutionPolicy Bypass -File ' +
  'C:\\Python\\Invoke-BridgeMergeDriver.ps1 -Loop -PollSeconds 120'
$records = [ordered]@{{
  safe = Test-LegacyDriverProvenNonApply (New-FakeTask $safe) $expected
  quoted = Test-LegacyDriverProvenNonApply (
    New-FakeTask '-NoProfile -ExecutionPolicy Bypass -File "C:\\Python\\Invoke-BridgeMergeDriver.ps1" -Loop -PollSeconds 120'
  ) $expected
  suffix = Test-LegacyDriverProvenNonApply (
    New-FakeTask '-NoProfile -ExecutionPolicy Bypass -File C:\\Python\\Invoke-BridgeMergeDriver.ps1.evil -Loop -PollSeconds 120'
  ) $expected
  command = Test-LegacyDriverProvenNonApply (
    New-FakeTask '-NoProfile -Command "& C:\\Python\\Invoke-BridgeMergeDriver.ps1"'
  ) $expected
  apply = Test-LegacyDriverProvenNonApply (
    New-FakeTask ($safe + ' -Apply')
  ) $expected
  metachar = Test-LegacyDriverProvenNonApply (
    New-FakeTask ($safe + '; Write-Output MUTATOR')
  ) $expected
  evil_executable = Test-LegacyDriverProvenNonApply (
    New-FakeTask $safe 'C:\\evil\\powershell.exe'
  ) $expected
}}
[pscustomobject]$records | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "safe": True,
        "quoted": True,
        "suffix": False,
        "command": False,
        "apply": False,
        "metachar": False,
        "evil_executable": False,
    }


def test_supervisor_preflight_requires_exact_registered_action_tuple() -> None:
    launcher = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8")
    register = (REBOOT / "Register-WdScheduledTasks.ps1").read_text(
        encoding="utf-8"
    )
    for text in (launcher, register):
        assert "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass " in text
        assert '-File "{0}" -Apply' in text
        assert "'C:\\Python'" in text
    assert "is not the exact stable registered tuple" in launcher
    assert "$action.Arguments -cne $expectedSupervisorArguments" in launcher


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_live_apply_detection_rejects_powershell_abbreviations() -> None:
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Test-ContainsApplySwitch'
  }},
  $true
)
. ([scriptblock]::Create($functionAst.Extent.Text))
[pscustomobject]@{{
  full = Test-ContainsApplySwitch 'driver.ps1 -Apply'
  quoted_abbrev = Test-ContainsApplySwitch 'driver.ps1 "-Ap"'
  shortest_colon = Test-ContainsApplySwitch 'driver.ps1 -A:$true'
  unrelated = Test-ContainsApplySwitch 'driver.ps1 -Append output'
  path = Test-ContainsApplySwitch 'C:\\Apply\\driver.ps1'
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "full": True,
        "quoted_abbrev": True,
        "shortest_colon": True,
        "unrelated": False,
        "path": False,
    }


def test_supervisor_snapshot_is_structured_and_version_independent() -> None:
    snapshot = json.loads(
        (REBOOT / "wd_supervisor_loop.json").read_text(encoding="utf-8")
    )
    assert snapshot["schema"] == "wd.supervisor-loop.v2"
    tools = snapshot["tools_consumer"]
    assert tools["agent"] == "codex-tools-1"
    assert tools["agent_uuid"] == "7a8af68d-20bc-4598-9953-23c5dd98b102"
    assert tools["worktree"] == r"C:\Python\project2"
    assert tools["sandbox"] == "workspace-write"
    assert tools["approval_policy"] == "never"
    assert "executable" not in tools
    assert "model" not in tools
    serialized = json.dumps(snapshot)
    assert "WindowsApps" not in serialized
    assert "7.6.3" not in serialized


@pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_tools_consumer_removes_windowsapps_from_codex_path() -> None:
    wrapper_path = REBOOT / "start-wd-tools-consumer.ps1"
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "function Test-CodexSandboxShell" in wrapper_text
    assert "$env:Path = $codexPathPlan.Path" in wrapper_text
    assert "':workspace'" in wrapper_text

    wrapper = str(wrapper_path).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{wrapper}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'tools consumer parse failed' }}
foreach ($name in @('Test-PathAtOrBelow', 'New-CodexSandboxPath')) {{
  $functionAst = $ast.Find(
    {{
      param($node)
      $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }},
    $true
  )
  if ($null -eq $functionAst) {{ throw "missing function: $name" }}
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}
$packageRoot = 'C:\\Program Files\\WindowsApps'
$aliasRoot = 'C:\\Users\\janik\\AppData\\Local\\Microsoft\\WindowsApps'
$currentPath = @(
  'C:\\Program Files\\WindowsApps\\Microsoft.PowerShell_7.6.4.0_x64__8wekyb3d8bbwe',
  'C:\\WINDOWS\\System32',
  'C:\\Users\\janik\\AppData\\Local\\Microsoft\\WindowsApps',
  'C:\\Program Files\\Git\\cmd',
  'C:\\Users\\janik\\AppData\\Roaming\\npm',
  'c:\\windows\\system32',
  'C:\\'
) -join ';'
$plan = New-CodexSandboxPath `
  -CurrentPath $currentPath `
  -PythonExecutable 'C:\\Tools\\Python313\\python.exe' `
  -PowerShellExecutable 'C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' `
  -WindowsAppsRoots @($packageRoot, $aliasRoot)
[pscustomobject]@{{
  entries = @($plan.Entries)
  removed = @($plan.RemovedWindowsAppsEntries)
}} | ConvertTo-Json -Depth 4 -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "entries": [
            r"C:\WINDOWS\System32\WindowsPowerShell\v1.0",
            r"C:\Tools\Python313",
            r"C:\Tools\Python313\Scripts",
            r"C:\WINDOWS\System32",
            r"C:\Program Files\Git\cmd",
            r"C:\Users\janik\AppData\Roaming\npm",
            "C:\\",
        ],
        "removed": [
            (
                r"C:\Program Files\WindowsApps"
                r"\Microsoft.PowerShell_7.6.4.0_x64__8wekyb3d8bbwe"
            ),
            r"C:\Users\janik\AppData\Local\Microsoft\WindowsApps",
        ],
    }


@pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_tools_consumer_sandbox_probe_is_exact_and_fail_closed(
    tmp_path: Path,
) -> None:
    wrapper_path = REBOOT / "start-wd-tools-consumer.ps1"
    fake_codex = tmp_path / "fake-codex.ps1"
    capture = tmp_path / "arguments.json"
    fake_codex.write_text(
        """
[IO.File]::WriteAllText(
  $env:WD_FAKE_CAPTURE,
  (ConvertTo-Json -InputObject ([string[]]$args) -Compress)
)
if ($env:WD_FAKE_MODE -eq 'missing-marker') {
  Write-Output 'WRONG_MARKER'
  & $env:ComSpec /c exit 0
  return
}
Write-Output 'WD_TOOLS_SANDBOX_OK'
if ($env:WD_FAKE_MODE -eq 'nonzero') {
  & $env:ComSpec /c exit 9
  return
}
& $env:ComSpec /c exit 0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    wrapper = str(wrapper_path).replace("'", "''")
    fake = str(fake_codex).replace("'", "''")
    capture_quoted = str(capture).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{wrapper}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'tools consumer parse failed' }}
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Test-CodexSandboxShell'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing sandbox probe function' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
$env:WD_FAKE_CAPTURE = '{capture_quoted}'
$env:WD_FAKE_MODE = 'success'
Test-CodexSandboxShell `
  -CodexCommand '{fake}' `
  -Worktree 'C:\\Python\\project2' `
  -ShellPath 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
$arguments = Get-Content -LiteralPath '{capture_quoted}' -Raw | ConvertFrom-Json
$env:WD_FAKE_MODE = 'missing-marker'
try {{
  Test-CodexSandboxShell `
    -CodexCommand '{fake}' `
    -Worktree 'C:\\Python\\project2' `
    -ShellPath 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
  $missingMarkerFailed = $false
}}
catch {{
  $missingMarkerFailed = $_.Exception.Message -like '*cannot launch*'
}}
$env:WD_FAKE_MODE = 'nonzero'
try {{
  Test-CodexSandboxShell `
    -CodexCommand '{fake}' `
    -Worktree 'C:\\Python\\project2' `
    -ShellPath 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
  $nonzeroFailed = $false
}}
catch {{
  $nonzeroFailed = $_.Exception.Message -like '*exit=9*'
}}
[pscustomobject]@{{
  arguments = [string[]]$arguments
  missing_marker_failed = $missingMarkerFailed
  nonzero_failed = $nonzeroFailed
}} | ConvertTo-Json -Depth 5 -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "arguments": [
            "sandbox",
            "-P",
            ":workspace",
            "-C",
            r"C:\Python\project2",
            "--",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::Out.Write('WD_TOOLS_SANDBOX_OK')",
        ],
        "missing_marker_failed": True,
        "nonzero_failed": True,
    }


@pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_tools_codex_shim_restores_safe_path_and_forwards_tick(
    tmp_path: Path,
) -> None:
    wrapper_path = REBOOT / "start-wd-tools-consumer.ps1"
    shim_path = REBOOT / "Invoke-WdToolsCodex.ps1"
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "WD_TOOLS_CODEX_REAL_COMMAND" in wrapper_text
    assert "WD_TOOLS_CODEX_SAFE_PATH" in wrapper_text
    assert "CodexCommand = $codexShim" in wrapper_text

    fake_codex = tmp_path / "fake-codex.ps1"
    capture = tmp_path / "shim-capture.json"
    fake_codex.write_text(
        """
$stdinText = (@($input | ForEach-Object { [string]$_ }) -join "`n")
$capture = [pscustomobject]@{
  arguments = [string[]]$args
  stdin = $stdinText
  path = $env:Path
}
[IO.File]::WriteAllText(
  $env:WD_FAKE_CAPTURE,
  ($capture | ConvertTo-Json -Depth 4 -Compress)
)
$exitCode = if ($env:WD_FAKE_MODE -eq 'nonzero') { 9 } else { 0 }
& $env:ComSpec /c exit $exitCode
""".strip()
        + "\n",
        encoding="utf-8",
    )

    shim = str(shim_path).replace("'", "''")
    fake = str(fake_codex).replace("'", "''")
    capture_quoted = str(capture).replace("'", "''")
    safe_path = (
        str(Path(os.environ["SystemRoot"]) / "System32")
        + ";"
        + str(Path(os.environ["SystemRoot"]).anchor)
    ).replace("'", "''")
    invocation = f"""
$env:WD_TOOLS_CODEX_REAL_COMMAND = '{fake}'
$env:WD_TOOLS_CODEX_SAFE_PATH = '{safe_path}'
$env:WD_FAKE_CAPTURE = '{capture_quoted}'
$env:Path = 'C:\\Program Files\\WindowsApps\\Microsoft.PowerShell_test'
"line one`nline two" | & '{shim}' `
  '--ask-for-approval' 'never' 'exec' '-C' 'C:\\Repo With Space' `
  '--sandbox' 'workspace-write' '-'
if ($null -ne $LASTEXITCODE) {{ exit $LASTEXITCODE }}
if ($?) {{ exit 0 }}
exit 1
"""
    success = _run_powershell(invocation, check=False)
    assert success.returncode == 0, success.stderr
    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload == {
        "arguments": [
            "--ask-for-approval",
            "never",
            "exec",
            "-C",
            r"C:\Repo With Space",
            "--sandbox",
            "workspace-write",
            "-",
        ],
        "stdin": "line one\nline two",
        "path": safe_path,
    }
    assert "windowsapps" not in payload["path"].casefold()

    nonzero_invocation = (
        "$env:WD_FAKE_MODE = 'nonzero'\n" + invocation
    )
    nonzero = _run_powershell(nonzero_invocation, check=False)
    assert nonzero.returncode == 9


def test_real_launcher_updates_each_cli_once_and_dry_run_returns_first() -> None:
    launcher = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8")
    assert launcher.count("-Arguments @('update')") == 2
    assert "codex update (once)" in launcher
    assert "claude update (once)" in launcher
    dry_return = launcher.index("DRY RUN: no updates")
    first_update = launcher.index("Updating Codex CLI once")
    assert dry_return < first_update
    assert "WD_CLI_VERSIONS_CURRENT.json" in launcher
    assert "function Resolve-ApplicationPath" in launcher
    assert "-All `" in launcher
    assert "Microsoft\\WindowsApps\\wt.exe" in launcher
    assert "Start-Process -FilePath $wtPath" in launcher
    assert "if ($DryRun) { Write-Warning $message } else { throw $message }" not in launcher
    assert "duplicate live lane" in launcher
    assert "duplicate supervisor-managed Tools consumers" in launcher
    assert "$bundleMode -ceq 'source'" in launcher
    assert "if (Test-Path -LiteralPath $handshakeDirectory)" in launcher
    assert "Grok model viability probe" in launcher
    assert "Tools consumer config differs from the committed deployed bundle" in launcher


def test_deployer_requires_clean_pushed_commit_before_machine_writes() -> None:
    text = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    status_gate = text.index(
        "@('status', '--porcelain', '--', 'ops/windows/reboot')"
    )
    upstream_gate = text.index("reboot-bundle commit is not pushed")
    first_copy = text.index("Copy-Item -LiteralPath (Join-Path $sourceRoot $name)")
    assert status_gate < first_copy
    assert upstream_gate < first_copy
    assert "WD_REBOOT_INTEGRITY_CURRENT.sha256" in text
    assert "wd-reboot-backups" in text
    assert "'Invoke-WdToolsCodex.ps1'," in text
    assert r"C:\Python\project2\.git" in text
    assert "manifest hash differs from source commit" in text
    preflight = text.index("Running mutation-free deployment preflight")
    first_store_write = text.index(
        "New-Item -ItemType Directory -Path $storeFull"
    )
    assert preflight < first_store_write
    assert "-DryRun `" in text[preflight:first_store_write]
    assert "rollback also failed" in text
    assert "`$LASTEXITCODE" not in text


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_native_stderr_is_checked_by_exit_code_under_powershell_51() -> None:
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Invoke-CheckedNative'
  }},
  $true
)
. ([scriptblock]::Create($functionAst.Extent.Text))
$ErrorActionPreference = 'Stop'
$success = Invoke-CheckedNative `
  -Path $env:ComSpec `
  -Arguments @('/d', '/c', 'echo successful-warning 1>&2 & exit /b 0') `
  -Label success
$failureCaught = $false
try {{
  Invoke-CheckedNative `
    -Path $env:ComSpec `
    -Arguments @('/d', '/c', 'echo real-failure 1>&2 & exit /b 7') `
    -Label failure |
    Out-Null
}}
catch {{
  $failureCaught = $_.Exception.Message -match 'exit code 7'
}}
[pscustomobject]@{{
  success_returned = -not [string]::IsNullOrWhiteSpace([string]$success)
  failure_caught = $failureCaught
  preference_restored = $ErrorActionPreference -eq 'Stop'
}} | ConvertTo-Json -Compress
"""
    )
    record = json.loads(
        next(
            line
            for line in reversed(result.stdout.splitlines())
            if line.startswith("{")
        )
    )
    assert record == {
        "success_returned": True,
        "failure_caught": True,
        "preference_restored": True,
    }


def test_deployed_wrappers_preserve_named_parameters() -> None:
    text = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    assert "ValueFromRemainingArguments" not in text
    assert "& $target @PSBoundParameters" in text
    assert "& `$target @targetParameters" in text
    assert "[switch] $DryRun" in text
    assert "[switch] $Apply" in text
    assert "[switch] $ValidateOnly" in text
    assert "$targetParameters['Agent']" in text


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_generated_wrappers_forward_switches_and_named_values(tmp_path: Path) -> None:
    target = tmp_path / "target.ps1"
    target.write_text(
        """
[CmdletBinding()]
param(
  [string] $RunId = '',
  [ValidateRange(10, 300)] [int] $HandshakeTimeoutSeconds = 90,
  [switch] $SkipCliUpdate,
  [switch] $DryRun
)
& $env:ComSpec /c exit 7
[pscustomobject]@{
  run_id = $RunId
  timeout = $HandshakeTimeoutSeconds
  skip_update = [bool]$SkipCliUpdate
  dry_run = [bool]$DryRun
  handled_native_status = $LASTEXITCODE
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    agent_target = tmp_path / "agent-target.ps1"
    agent_target.write_text(
        """
[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $Agent,
  [string] $RunId = '',
  [switch] $DryRun
)
[pscustomobject]@{
  agent = $Agent
  run_id = $RunId
  dry_run = [bool]$DryRun
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    deploy = str(REBOOT / "Deploy-WdRebootBundle.ps1").replace("'", "''")
    target_quoted = str(target).replace("'", "''")
    agent_target_quoted = str(agent_target).replace("'", "''")
    fleet_wrapper = str(tmp_path / "fleet-wrapper.ps1").replace("'", "''")
    agent_wrapper = str(tmp_path / "agent-wrapper.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{deploy}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'deployer parse failed' }}
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'New-ForwardingWrapper'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'wrapper generator function not found' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
$utf8 = New-Object Text.UTF8Encoding($false)
$fleetText = New-ForwardingWrapper `
  -Target '{target_quoted}' `
  -ExpectedHash (Get-FileHash -LiteralPath '{target_quoted}' -Algorithm SHA256).Hash `
  -WrapperKind fleet
[IO.File]::WriteAllText('{fleet_wrapper}', $fleetText, $utf8)
$agentText = New-ForwardingWrapper `
  -Target '{agent_target_quoted}' `
  -ExpectedHash (Get-FileHash -LiteralPath '{agent_target_quoted}' -Algorithm SHA256).Hash `
  -WrapperKind agent `
  -FixedAgent fable-5
[IO.File]::WriteAllText('{agent_wrapper}', $agentText, $utf8)
$fleet = & '{fleet_wrapper}' `
  -RunId reboot-123 `
  -HandshakeTimeoutSeconds 42 `
  -SkipCliUpdate `
  -DryRun
$agent = & '{agent_wrapper}' -RunId lane-456 -DryRun
[pscustomobject]@{{ fleet = $fleet; agent = $agent }} |
  ConvertTo-Json -Depth 6 -Compress
"""
    )
    record = json.loads(result.stdout)
    assert record["fleet"] == {
        "run_id": "reboot-123",
        "timeout": 42,
        "skip_update": True,
        "dry_run": True,
        "handled_native_status": 7,
    }
    assert record["agent"] == {
        "agent": "fable-5",
        "run_id": "lane-456",
        "dry_run": True,
    }


def test_root_runbook_names_current_one_line_and_authority_hold() -> None:
    text = (ROOT / "BOOT_AFTER_REBOOT.md").read_text(encoding="utf-8")
    assert (
        "powershell -NoProfile -ExecutionPolicy Bypass "
        "-File C:\\Python\\start-wd-all.ps1"
    ) in text
    assert "WD_REBOOT_INTEGRITY_CURRENT.sha256" in text
    assert "deliberately" in text
    assert "must never enable it" in text


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_grok_resolver_is_dry_run_clean_and_cache_is_bounded(tmp_path: Path) -> None:
    fake = tmp_path / "fake-grok.ps1"
    fake.write_text(
        """
if ($args.Count -eq 1 -and $args[0] -eq '--version') {
  Write-Output 'grok 9.9.9 (fake)'
  exit 0
}
if ($args.Count -eq 1 -and $args[0] -eq 'models') {
  Write-Output 'Default model: grok-9.9'
  Write-Output ''
  Write-Output 'Available models:'
  Write-Output '  * grok-9.9 (default)'
  Write-Output '  - grok-9.8'
  exit 0
}
exit 7
""".strip()
        + "\n",
        encoding="utf-8",
    )
    resolver = str(REBOOT / "Resolve-WdGrokModel.ps1").replace("'", "''")
    fake_quoted = str(fake).replace("'", "''")
    output_quoted = str(tmp_path).replace("'", "''")

    dry = _run_powershell(
        f"& '{resolver}' -GrokCommand '{fake_quoted}' "
        f"-OutputDirectory '{output_quoted}' "
        "-NowUtc ([DateTimeOffset]'2026-07-30T10:00:00Z') -DryRun "
        "| ConvertTo-Json -Depth 8 -Compress"
    )
    dry_record = json.loads(dry.stdout)
    assert dry_record["Status"] == "verified_dry_run"
    assert dry_record["Model"] == "grok-9.9"
    assert not (tmp_path / "WD_GROK_MODEL_CURRENT.json").exists()
    assert not (tmp_path / "WD_GROK_MODEL_CURRENT.md").exists()

    real = _run_powershell(
        f"& '{resolver}' -GrokCommand '{fake_quoted}' "
        f"-OutputDirectory '{output_quoted}' "
        "-NowUtc ([DateTimeOffset]'2026-07-30T10:00:00Z') "
        "| ConvertTo-Json -Depth 8 -Compress"
    )
    real_record = json.loads(real.stdout)
    assert real_record["Status"] == "verified_persisted"
    persisted = json.loads(
        (tmp_path / "WD_GROK_MODEL_CURRENT.json").read_text(encoding="utf-8")
    )
    assert persisted["model"] == "grok-9.9"
    assert "--model 'grok-9.9'" in persisted["usage"]["single_turn"]
    assert "--effort high" in persisted["usage"]["single_turn"]

    repeated = _run_powershell(
        f"& '{resolver}' -GrokCommand '{fake_quoted}' "
        f"-OutputDirectory '{output_quoted}' "
        "-NowUtc ([DateTimeOffset]'2026-07-30T10:05:00Z') "
        "| ConvertTo-Json -Depth 8 -Compress"
    )
    repeated_record = json.loads(repeated.stdout)
    assert repeated_record["Status"] == "verified_persisted"
    replaced = json.loads(
        (tmp_path / "WD_GROK_MODEL_CURRENT.json").read_text(encoding="utf-8")
    )
    assert replaced["discovered_utc"] == "2026-07-30T10:05:00.0000000+00:00"

    fake.write_text("Write-Error 'offline'; exit 7\n", encoding="utf-8")
    cached = _run_powershell(
        f"& '{resolver}' -GrokCommand '{fake_quoted}' "
        f"-OutputDirectory '{output_quoted}' "
        "-NowUtc ([DateTimeOffset]'2026-08-01T10:00:00Z') -DryRun "
        "| ConvertTo-Json -Depth 8 -Compress"
    )
    cached_json = next(
        line for line in reversed(cached.stdout.splitlines()) if line.startswith("{")
    )
    cached_record = json.loads(cached_json)
    assert cached_record["Status"] == "verified_cache_fallback"
    assert cached_record["Model"] == "grok-9.9"

    stale = _run_powershell(
        f"& '{resolver}' -GrokCommand '{fake_quoted}' "
        f"-OutputDirectory '{output_quoted}' "
        "-NowUtc ([DateTimeOffset]'2026-08-08T10:00:01Z') -DryRun",
        check=False,
    )
    assert stale.returncode != 0
    stale_text = re.sub(r"\x1b\[[0-9;]*m", "", stale.stdout + stale.stderr)
    stale_normalized = " ".join(stale_text.split()).lower()
    assert "refusing to" in stale_normalized
    assert "guess or use a hard-coded model" in stale_normalized


def test_grok_contract_uses_provider_default_without_strength_guessing() -> None:
    resolver = (REBOOT / "Resolve-WdGrokModel.ps1").read_text(encoding="utf-8")
    launcher = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8")
    runbook = (REBOOT / "BOOT_AFTER_REBOOT.md").read_text(encoding="utf-8")

    assert "Authenticated CLI provider default" in resolver
    assert "no local version-name ranking" in resolver
    assert "authenticated CLI provider default" in launcher
    assert "does not guess a “strongest” model" in runbook
    assert "strongest current general model" not in launcher
    assert "default/strongest" not in runbook

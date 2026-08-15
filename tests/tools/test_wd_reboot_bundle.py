from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
TOOLS_WORKTREE = (
    r"C:\Python\waggledance-agent-worktrees"
    r"\codex-tools-1-post-reboot-tools-sandbox-followup-20260731"
)
TOOLS_BRANCH = "codex-tools-1/post-reboot-tools-sandbox-followup-20260731"
TOOLS_HEAD = "1c4e388355d045835edcd1f30cd53eddfdc3d2cb"
POWERSHELL = (
    shutil.which("pwsh")
    or shutil.which("powershell")
    or shutil.which("powershell.exe")
)
WINDOWS_POWERSHELL_PATH = (
    Path(os.environ["SystemRoot"])
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
    if os.name == "nt"
    else None
)
WINDOWS_POWERSHELL = (
    str(WINDOWS_POWERSHELL_PATH)
    if WINDOWS_POWERSHELL_PATH is not None
    and WINDOWS_POWERSHELL_PATH.is_file()
    else None
)


def _run_powershell(
    script: str,
    *,
    check: bool = True,
    executable: str | None = POWERSHELL,
) -> subprocess.CompletedProcess[str]:
    assert executable is not None
    return subprocess.run(
        [
            executable,
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
    target = manifest["target_state"]
    target_path = REBOOT / target["relative_path"]
    assert target["id"] == "wd-swarm-target-state-v1"
    assert target["capability_effect"] == "none"
    assert target["source_image_sha256"] == (
        "A05774DF5EB15FDCE08A850149550C3CD94DC0F952136A5E1C02D37EFBE43117"
    )
    assert hashlib.sha256(target_path.read_bytes()).hexdigest().upper() == target["sha256"]
    supervisor = json.loads(
        (REBOOT / "wd_supervisor_loop.json").read_text(encoding="utf-8")
    )
    assert supervisor["target_state"] == target
    assert "Invoke-WdToolsCodex.ps1" in (
        manifest["deployment"]["required_bundle_files"]
    )
    required_bundle_files = manifest["deployment"]["required_bundle_files"]
    assert "WD_SWARM_TARGET_STATE_V1.md" in required_bundle_files
    for relative in required_bundle_files:
        if relative.startswith("tools-bootstrap/.agent-bridge/bin/"):
            source = ROOT / relative.removeprefix("tools-bootstrap/")
        elif relative == "tools-bootstrap/configs/bridge_identity_registry.json":
            source = ROOT / "configs" / "bridge_identity_registry.json"
        else:
            source = REBOOT / relative
        assert source.is_file(), f"bundle source is missing: {relative}"
    assert (
        "tools-bootstrap/.agent-bridge/bin/BridgeIncrementalReader.ps1"
        in required_bundle_files
    )
    assert (
        "tools-bootstrap/.agent-bridge/bin/BridgeLogReader.ps1"
        in required_bundle_files
    )
    tools_supervisor = manifest["tools_supervisor"]
    assert tools_supervisor["worktree"] == TOOLS_WORKTREE
    assert tools_supervisor["branch"] == TOOLS_BRANCH
    assert tools_supervisor["head"] == TOOLS_HEAD
    assert tools_supervisor["require_dedicated_worktree"] is True
    assert tools_supervisor["wait_seconds"] == 660
    assert tools_supervisor["resume_policy"] == "current_worktree"
    assert tools_supervisor["model"] == "gpt-5.6-terra"
    assert tools_supervisor["reasoning_effort"] == "high"
    assert tools_supervisor["replacement_conflict_path"] == (
        r"C:\Python\wd-reboot-runtime"
        r"\codex-tools-1-replacement-conflict.json"
    )

    lanes = {lane["agent"]: lane for lane in manifest["lanes"]}
    assert set(lanes) == {
        "codex-lead-1",
        "claude-rco-1",
        "claude-rco-2",
        "fable-5",
    }
    assert lanes["codex-lead-1"]["branch"] == (
        "codex-lead-1/magma-faiss-hex-genome-20260810"
    )
    assert lanes["codex-lead-1"]["head"] == (
        "fc03c60077d1787e1f2ec95c4f31d4605370103b"
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
    expected_models = {
        "codex-lead-1": ("gpt-5.6-sol", "max"),
        "claude-rco-1": ("sonnet", "max"),
        "claude-rco-2": ("sonnet", "max"),
        "fable-5": ("fable", "max"),
    }
    for agent, lane in lanes.items():
        assert lane["worktree"].startswith("C:\\")
        assert lane["worktree"] != TOOLS_WORKTREE
        assert len(lane["head"]) == 40
        assert (lane["model"], lane["effort"]) == expected_models[agent]
        assert lane["resume_policy"] == "current_worktree"


def test_target_state_is_binary_to_preserve_raw_manifest_hash() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "ops/windows/reboot/WD_SWARM_TARGET_STATE_V1.md binary" in attributes


def test_interactive_launchers_pin_current_models_and_max_effort() -> None:
    agent_launcher = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    bundle_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(REBOOT.glob("*"))
        if path.is_file()
    )
    assert "claude-opus-" not in bundle_text.lower()
    assert "'--model', $model" in agent_launcher
    assert "'--effort', $effort" in agent_launcher
    assert "model_reasoning_effort=\"{0}\"" in agent_launcher
    assert "'--dangerously-skip-permissions'" in agent_launcher
    assert "model_selection = 'explicit'" in agent_launcher
    assert "legacy model labels" in agent_launcher
    assert "target_state_manifested" in agent_launcher
    assert "-Status target_state_manifested" in agent_launcher


def test_each_lane_manifests_hash_bound_target_before_model_launch() -> None:
    agent_launcher = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    tools_launcher = (REBOOT / "start-wd-tools-consumer.ps1").read_text(
        encoding="utf-8"
    )
    target = (REBOOT / "WD_SWARM_TARGET_STATE_V1.md").read_text(encoding="utf-8")

    assert agent_launcher.count("-Status target_state_manifested `") == 1
    assert tools_launcher.count("-Status target_state_manifested `") == 1
    assert agent_launcher.index("-Status target_state_manifested `") < (
        agent_launcher.index("model_selection = 'explicit'")
    )
    assert agent_launcher.index("model_selection = 'explicit'") < (
        agent_launcher.index("& ([string]$cli.Source) @launchArguments")
    )
    assert tools_launcher.index("-Status target_state_manifested `") < (
        tools_launcher.index("$initialOutput = @(& $consumerScript")
    )
    assert "grants no authority" in target
    assert "flips no" in target


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
    assert "$toolsStarting.ProcessId" not in launch_text
    assert "$toolsStale.ProcessId" not in launch_text
    assert "Assert-DirectoryPathWithoutReparse" in launch_text
    assert "WD_TOOLS_CODEX_RUNTIME_ROOT" in launch_text
    assert "required watcher source is missing" in supervisor
    assert "required tools consumer launcher is missing" in supervisor
    assert "WARN tools consumer launcher missing" not in supervisor
    assert "Test-NamedCommandLineLeafArgument `" in supervisor
    assert "-Value $ScriptName" not in supervisor
    assert "-Name 'Agent' `" in supervisor
    assert "'-RuntimeRoot', $runtimeRoot" in supervisor
    assert "CommandLineToArgvW" in supervisor
    assert "Get-WdPowerShellFileInvocation" in supervisor
    assert "Test-WdCanonicalWatcherProcess `" in supervisor
    assert "Add-Type -ErrorAction Stop -TypeDefinition" in supervisor
    parser_body = supervisor[
        supervisor.index("function ConvertFrom-WdWindowsCommandLine") :
        supervisor.index("function Test-WdPowerShellSwitchToken")
    ]
    assert "catch" not in parser_body
    assert supervisor.index("Initialize-WdSupervisorCommandLineParser\n$processes") < (
        supervisor.index("if ($toolsEnabled)")
    )
    assert "Global\\WaggleDanceWatcherReconcileV1-" in supervisor
    assert "$mutex.WaitOne(0)" in supervisor
    assert "catch [Threading.AbandonedMutexException]" in supervisor
    assert "$mutex.ReleaseMutex()" in supervisor
    assert "$mutex.Dispose()" in supervisor
    watcher_lock_start = supervisor.index(
        "$watcherReconciled = Invoke-WdWatcherReconcileLocked"
    )
    watcher_lock_end = supervisor.index("if ($toolsEnabled)", watcher_lock_start)
    watcher_lock = supervisor[watcher_lock_start:watcher_lock_end]
    assert watcher_lock.index("$watcherProcesses = @(") < watcher_lock.index(
        "foreach ($agent in $watcherAgents)"
    )
    assert "$watcherProcesses `" in watcher_lock
    assert watcher_lock.index("foreach ($agent in $watcherAgents)") < (
        watcher_lock.index("Start-OutOfTaskJobPowerShell `")
    )
    assert "another supervisor owns the runtime mutex" in watcher_lock
    assert "CmdletizationQuery_NotFound" in supervisor
    assert "Get-OptionalScheduledTask" in supervisor
    assert "$allAgentWatchers = @(" in supervisor
    assert "$legacyConsumers = @(" in supervisor
    assert "'-Generation', $toolsGeneration" in supervisor
    assert "WOULD-REPLACE $replacementReason consumer-loop:codex-tools-1" in supervisor
    assert "'expired-startup'" in supervisor
    assert "'stale-generation'" in supervisor
    assert "Stop-VerifiedProcessTree `" in supervisor
    assert "-RootProcess $staleProcess `" in supervisor
    assert "-InitialProcesses $processes `" in supervisor
    assert "-ConflictPath $toolsConflictPath" in supervisor
    assert "System32\\taskkill.exe" in supervisor
    assert "/PID $killPid /F" in supervisor
    assert "/PID $killPid /T /F" not in supervisor
    assert "kill target PID identity changed" in supervisor
    assert "stale Tools process tree did not stop" in supervisor
    assert "wd.tools-replacement-conflict.v1" in supervisor
    assert "$lineageByPid" in supervisor
    assert "$rootKillSucceeded" in supervisor
    assert "Tools consumer preflight does not match supervisor generation" in supervisor
    assert "Assert-MachineToolsConfigExact" in supervisor
    assert "Assert-WdSupervisorPathWithoutReparse" in supervisor
    assert "& $configuredToolsLauncher" not in supervisor
    assert "'-File', $toolsLauncher" in supervisor
    assert (
        supervisor.count("-RelativePath 'start-wd-tools-consumer.ps1'") == 4
    )
    assert "Assert-SupervisorBundleFileIntegrity -RelativePath $watcherRelative" in (
        supervisor
    )
    assert "supervisor deployment manifest is not externally anchored" in supervisor
    assert "Test-ToolsWrapperReadiness" in supervisor
    assert "Test-ToolsWrapperWithinStartupGrace" in supervisor
    assert "function Start-OutOfTaskJobPowerShell" in supervisor
    assert "Invoke-CimMethod `" in supervisor
    assert "-ClassName Win32_Process `" in supervisor
    assert "-ClassName Win32_ProcessStartup `" in supervisor
    assert "-Property @{" in supervisor
    assert "0x08000000 -bor" in supervisor
    assert "0x00000400" in supervisor
    assert "ShowWindow = [uint16]0" in supervisor
    assert "EnvironmentVariables = [string[]]$environment.ToArray()" in (
        supervisor
    )
    assert supervisor.count("Start-OutOfTaskJobPowerShell `") == 3
    assert "Tools consumer preflight did not resolve stable Windows PowerShell" in (
        supervisor
    )
    assert supervisor.index("-ValidateOnly") < supervisor.index("$watcherScript =")


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
@pytest.mark.skipif(
    os.name != "nt",
    reason="watcher parser requires Windows CommandLineToArgvW",
)
def test_supervisor_unknown_host_is_a_nonmatch_not_a_validation_failure() -> None:
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
foreach ($name in @(
    'Test-NamedCommandLineArgument',
    'Initialize-WdSupervisorCommandLineParser',
    'ConvertFrom-WdWindowsCommandLine',
    'Test-WdPowerShellSwitchToken',
    'Test-WdPowerShellHostOptionToken',
    'Test-WdPowerShellFileSwitchToken',
    'Get-WdPowerShellHostKind',
    'Test-WdEncodedCommandValue',
    'Get-WdPowerShellFileInvocation'
  )) {{
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
Initialize-WdSupervisorCommandLineParser
$scriptPath = 'C:\\bundle\\start-wd-tools-consumer.ps1'
$configPath = 'C:\\bundle\\wd_supervisor_loop.json'
$generation = '0123456789012345678901234567890123456789'
$extensionlessCommand = (
  'pwsh -File ' + $scriptPath +
  ' -ConfigPath ' + $configPath +
  ' -Generation ' + $generation
)
$process = [pscustomobject]@{{
  Name = 'pwsh.exe'
  CommandLine = $extensionlessCommand
}}
$wrapperProcesses = @(
  $process | Where-Object {{
    $processHostKind = if ([string]$_.Name -ieq 'powershell.exe') {{
      'WindowsPowerShell'
    }} else {{
      'Pwsh'
    }}
    Test-NamedCommandLineArgument `
      -CommandLine ([string]$_.CommandLine) `
      -HostKind $processHostKind `
      -Name 'File' `
      -Value $scriptPath
  }}
)
$configuredProcesses = @(
  $wrapperProcesses | Where-Object {{
    Test-NamedCommandLineArgument `
      -CommandLine ([string]$_.CommandLine) `
      -HostKind 'Pwsh' `
      -Name 'ConfigPath' `
      -Value $configPath
  }}
)
$exactProcesses = @(
  $configuredProcesses | Where-Object {{
    Test-NamedCommandLineArgument `
      -CommandLine ([string]$_.CommandLine) `
      -HostKind 'Pwsh' `
      -Name 'Generation' `
      -Value $generation
  }}
)
[pscustomobject]@{{
  unknown_host = Test-NamedCommandLineArgument `
    -CommandLine $extensionlessCommand `
    -Name 'File' `
    -Value $scriptPath
  explicit_host = Test-NamedCommandLineArgument `
    -CommandLine $extensionlessCommand `
    -HostKind 'Pwsh' `
    -Name 'File' `
    -Value $scriptPath
  known_host = Test-NamedCommandLineArgument `
    -CommandLine ('pwsh.exe -File ' + $scriptPath) `
    -Name 'File' `
    -Value $scriptPath
  wrapper_count = $wrapperProcesses.Count
  configured_count = $configuredProcesses.Count
  exact_count = $exactProcesses.Count
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "unknown_host": False,
        "explicit_host": True,
        "known_host": True,
        "wrapper_count": 1,
        "configured_count": 1,
        "exact_count": 1,
    }


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_supervisor_report_only_log_is_byte_inert(tmp_path: Path) -> None:
    supervisor_path = REBOOT / "wd_supervisor.ps1"
    supervisor_text = supervisor_path.read_text(encoding="utf-8")
    assert supervisor_text.index(
        "Initialize-SupervisorLogParent -Path $logFull -Apply:$Apply"
    ) < supervisor_text.index("$powerShellHost = Resolve-PowerShellChildHost")
    supervisor = str(supervisor_path).replace("'", "''")
    log_path = str(tmp_path / "missing" / "supervisor.log").replace("'", "''")
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
foreach ($name in @(
    'Initialize-SupervisorLogParent',
    'Write-SupervisorLogLine'
  )) {{
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
$path = [IO.Path]::GetFullPath('{log_path}')
$parent = Split-Path -Parent $path
Initialize-SupervisorLogParent -Path $path -Apply:$false
Write-SupervisorLogLine -Path $path -Line 'dry-run' -Apply:$false
$dryParentExists = Test-Path -LiteralPath $parent
$dryFileExists = Test-Path -LiteralPath $path
[void](New-Item -ItemType Directory -Path $parent -Force)
Set-Content -LiteralPath $path -Value 'sentinel' -Encoding UTF8
$beforeDryBytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
Write-SupervisorLogLine -Path $path -Line 'dry-existing' -Apply:$false
$afterDryBytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
$dryExistingLines = @(Get-Content -LiteralPath $path)
Initialize-SupervisorLogParent -Path $path -Apply
Write-SupervisorLogLine -Path $path -Line 'apply' -Apply
[pscustomobject]@{{
  dry_parent_exists = $dryParentExists
  dry_file_exists = $dryFileExists
  dry_existing_bytes_equal = $beforeDryBytes -ceq $afterDryBytes
  dry_existing_lines = $dryExistingLines
  apply_file_exists = Test-Path -LiteralPath $path -PathType Leaf
  lines = @(Get-Content -LiteralPath $path)
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "dry_parent_exists": False,
        "dry_file_exists": False,
        "dry_existing_bytes_equal": True,
        "dry_existing_lines": ["sentinel"],
        "apply_file_exists": True,
        "lines": ["sentinel", "apply"],
    }


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.skipif(
    os.name != "nt",
    reason="watcher parser requires Windows CommandLineToArgvW",
)
def test_supervisor_watcher_discovery_requires_real_file_invocation() -> None:
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
foreach ($name in @(
    'Test-NamedCommandLineArgument',
    'Test-WdWatcherScriptArgument',
    'Test-NamedCommandLineLeafArgument',
    'Initialize-WdSupervisorCommandLineParser',
    'ConvertFrom-WdWindowsCommandLine',
    'Test-WdPowerShellSwitchToken',
    'Test-WdPowerShellHostOptionToken',
    'Test-WdPowerShellFileSwitchToken',
    'Get-WdPowerShellHostKind',
    'Test-WdEncodedCommandValue',
    'Get-WdPowerShellFileInvocation',
    'Test-WdCanonicalWatcherProcess',
    'Get-AgentCommandProcesses'
  )) {{
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
$watcher = 'C:\\bundle\\Watch-Bridge.ps1'
$runtime = 'C:\\runtime'
$hostPath = 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
$current = [pscustomobject]@{{
  ProcessId = 101
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + $watcher +
    ' -Agent codex-lead-1 ' +
    '-RuntimeRoot ' + $runtime
  )
}}
$stale = [pscustomobject]@{{
  ProcessId = 102
  Name = 'pwsh.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'pwsh.exe -File C:\\old\\Watch-Bridge.ps1 -Agent codex-lead-1 ' +
    '-RuntimeRoot ' + $runtime
  )
}}
$commandNoise = [pscustomobject]@{{
  ProcessId = 103
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -Command "Write-Output -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime + '"'
  )
}}
$shortCommandNoise = [pscustomobject]@{{
  ProcessId = 105
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -c "Write-Output -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime + '"'
  )
}}
$encodedNoise = [pscustomobject]@{{
  ProcessId = 106
  Name = 'pwsh.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'pwsh.exe -enc ignored -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$slashCommandNoise = [pscustomobject]@{{
  ProcessId = 107
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe /c "Write-Output -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime + '"'
  )
}}
$unicodeCommandNoise = [pscustomobject]@{{
  ProcessId = 108
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe ' + [char]0x2013 + 'c "Write-Output -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime + '"'
  )
}}
$implicitCommandNoise = [pscustomobject]@{{
  ProcessId = 111
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -NoProfile "Write-Output -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime + '"'
  )
}}
$wrongHost = [pscustomobject]@{{
  ProcessId = 104
  Name = 'cmd.exe'
  ExecutablePath = 'C:\\Windows\\System32\\cmd.exe'
  CommandLine = (
    'cmd.exe -File ' + $watcher + ' -Agent codex-lead-1 ' +
    '-RuntimeRoot ' + $runtime
  )
}}
$fileAlias = [pscustomobject]@{{
  ProcessId = 109
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe /fi C:\\other\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$unicodeFileAlias = [pscustomobject]@{{
  ProcessId = 110
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe ' + [char]0x2014 + 'f C:\\unicode\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$samePathFileAlias = [pscustomobject]@{{
  ProcessId = 112
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe /f ' + $watcher + ' -Agent codex-lead-1 ' +
    '-RuntimeRoot ' + $runtime
  )
}}
$staFileWatcher = [pscustomobject]@{{
  ProcessId = 113
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -Sta -File C:\\sta\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$mtaFileWatcher = [pscustomobject]@{{
  ProcessId = 114
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -Mta -File C:\\mta\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$versionFileWatcher = [pscustomobject]@{{
  ProcessId = 115
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -Version 5.1 -File C:\\version\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$invalidVersionOrderNoise = [pscustomobject]@{{
  ProcessId = 116
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -NoProfile -Version 5.1 -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$wrongExecutable = [pscustomobject]@{{
  ProcessId = 117
  Name = 'powershell.exe'
  ExecutablePath = 'C:\\evil\\powershell.exe'
  CommandLine = (
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + $watcher +
    ' -Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$shortOptionWatcher = [pscustomobject]@{{
  ProcessId = 118
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -nop -ep Bypass -f C:\\short\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$colonArgumentWatcher = [pscustomobject]@{{
  ProcessId = 119
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -f C:\\colon\\Watch-Bridge.ps1 ' +
    '-Age:codex-lead-1 -Ru:' + $runtime
  )
}}
$workingDirectoryWatcher = [pscustomobject]@{{
  ProcessId = 120
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -wd C:\\work -f C:\\working\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$consoleFileWatcher = [pscustomobject]@{{
  ProcessId = 121
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -p C:\\watcher.psc1 -f C:\\console\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$implicitFileWatcher = [pscustomobject]@{{
  ProcessId = 122
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -NoProfile C:\\implicit\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$encodedPrelude = [Convert]::ToBase64String(
  [Text.Encoding]::Unicode.GetBytes("'prelude'")
)
$encodedFileWatcher = [pscustomobject]@{{
  ProcessId = 123
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -ec ' + $encodedPrelude +
    ' -f C:\\encoded\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$pwshHostOptionsWatcher = [pscustomobject]@{{
  ProcessId = 124
  Name = 'pwsh.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'pwsh.exe -i -NoProfileLoadTime -custom pipe -settings settings.json ' +
    '-f C:\\pwsh\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$windowsHostOptionsWatcher = [pscustomobject]@{{
  ProcessId = 125
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -st -i Text -f C:\\winps\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$oddEncodedFileWatcher = [pscustomobject]@{{
  ProcessId = 126
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -e AA== -f C:\\odd\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$surrogateEncodedFileWatcher = [pscustomobject]@{{
  ProcessId = 127
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -e ANg= -f C:\\surrogate\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$windowsEncodedImplicitNoise = [pscustomobject]@{{
  ProcessId = 128
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'powershell.exe -e AA== C:\\not-run\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$pwshEncodedImplicitWatcher = [pscustomobject]@{{
  ProcessId = 129
  Name = 'pwsh.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    'pwsh.exe -e AA== C:\\pwsh-implicit\\Watch-Bridge.ps1 ' +
    '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
  )
}}
$all = @(
  Get-AgentCommandProcesses `
    -Processes @(
      $current, $stale, $commandNoise, $shortCommandNoise, $encodedNoise,
      $slashCommandNoise, $unicodeCommandNoise, $wrongHost, $fileAlias,
      $unicodeFileAlias, $implicitCommandNoise, $samePathFileAlias,
      $staFileWatcher, $mtaFileWatcher, $versionFileWatcher,
      $invalidVersionOrderNoise, $wrongExecutable, $shortOptionWatcher,
      $colonArgumentWatcher, $workingDirectoryWatcher, $consoleFileWatcher,
      $implicitFileWatcher, $encodedFileWatcher, $pwshHostOptionsWatcher,
      $windowsHostOptionsWatcher, $oddEncodedFileWatcher,
      $surrogateEncodedFileWatcher, $windowsEncodedImplicitNoise,
      $pwshEncodedImplicitWatcher
    ) `
    -ScriptName 'Watch-Bridge.ps1' `
    -Agent 'codex-lead-1'
)
$exact = @(
  $all | Where-Object {{
    Test-WdCanonicalWatcherProcess `
      -Process $_ `
      -ExpectedExecutable $hostPath `
      -ScriptPath $watcher `
      -Agent codex-lead-1 `
      -RuntimeRoot $runtime
  }}
)
[pscustomobject]@{{
  all_count = $all.Count
  all_pids = @($all | ForEach-Object {{ [int]$_.ProcessId }})
  exact_count = $exact.Count
  exact_pid = [int]$exact[0].ProcessId
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "all_count": 20,
        "all_pids": [
            101,
            102,
            109,
            110,
            112,
            113,
            114,
            115,
            117,
            118,
            119,
            120,
            121,
            122,
            123,
            124,
            125,
            126,
            127,
            129,
        ],
        "exact_count": 1,
        "exact_pid": 101,
    }


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_supervisor_watcher_reconcile_mutex_is_scoped_and_nonblocking(
    tmp_path: Path,
) -> None:
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    root_a = str(tmp_path / "runtime-a").replace("'", "''")
    root_b = str(tmp_path / "runtime-b").replace("'", "''")
    ready = str(tmp_path / "holder-ready").replace("'", "''")
    release = str(tmp_path / "holder-release").replace("'", "''")
    result = _run_powershell(
        rf"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
foreach ($name in @(
    'Get-WdWatcherReconcileMutexName',
    'Invoke-WdWatcherReconcileLocked'
  )) {{
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
$rootA = [IO.Path]::GetFullPath('{root_a}')
$rootB = [IO.Path]::GetFullPath('{root_b}')
[void][IO.Directory]::CreateDirectory($rootA)
[void][IO.Directory]::CreateDirectory($rootB)
$nameA = Get-WdWatcherReconcileMutexName -RuntimeRoot $rootA
$nameAlias = Get-WdWatcherReconcileMutexName `
  -RuntimeRoot ($rootA.ToLowerInvariant() + '\\')
$nameB = Get-WdWatcherReconcileMutexName -RuntimeRoot $rootB
$job = Start-Job -ArgumentList $nameA, '{ready}', '{release}' -ScriptBlock {{
  param($mutexName, $readyPath, $releasePath)
  $mutex = [Threading.Mutex]::new($false, $mutexName)
  $acquired = $false
  try {{
    $acquired = $mutex.WaitOne(5000)
    if (-not $acquired) {{ throw 'holder failed to acquire mutex' }}
    [IO.File]::WriteAllText($readyPath, 'ready')
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    while (-not [IO.File]::Exists($releasePath)) {{
      if ([DateTimeOffset]::UtcNow -gt $deadline) {{
        throw 'holder release timeout'
      }}
      Start-Sleep -Milliseconds 25
    }}
  }}
  finally {{
    if ($acquired) {{ $mutex.ReleaseMutex() }}
    $mutex.Dispose()
  }}
}}
try {{
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
  while (-not [IO.File]::Exists('{ready}')) {{
    if ([DateTimeOffset]::UtcNow -gt $deadline) {{
      throw 'holder ready timeout'
    }}
    Start-Sleep -Milliseconds 25
  }}
  $stateAWhileHeld = @{{ ran = $false }}
  $stopwatch = [Diagnostics.Stopwatch]::StartNew()
  $contended = Invoke-WdWatcherReconcileLocked -RuntimeRoot $rootA -Action {{
    $stateAWhileHeld.ran = $true
  }}
  $stopwatch.Stop()
  $stateB = @{{ ran = $false }}
  $isolated = Invoke-WdWatcherReconcileLocked -RuntimeRoot $rootB -Action {{
    $stateB.ran = $true
  }}
  [IO.File]::WriteAllText('{release}', 'release')
  Wait-Job -Job $job -Timeout 10 | Out-Null
  if ($job.State -ne 'Completed') {{ throw "holder state: $($job.State)" }}
  Receive-Job -Job $job -ErrorAction Stop | Out-Null
  $stateAAfter = @{{ ran = $false }}
  $afterRelease = Invoke-WdWatcherReconcileLocked -RuntimeRoot $rootA -Action {{
    $stateAAfter.ran = $true
  }}
  [pscustomobject]@{{
    name_alias_equal = $nameA -ceq $nameAlias
    names_distinct = $nameA -cne $nameB
    name_shape = (
      $nameA.StartsWith(
        'Global\WaggleDanceWatcherReconcileV1-',
        [StringComparison]::Ordinal
      ) -and
      $nameA.Substring('Global\WaggleDanceWatcherReconcileV1-'.Length) `
        -cmatch '^[0-9A-F]{{64}}$'
    )
    contended = [bool]$contended
    contended_elapsed_ms = [int]$stopwatch.ElapsedMilliseconds
    ran_a_while_held = [bool]$stateAWhileHeld.ran
    isolated = [bool]$isolated
    ran_b = [bool]$stateB.ran
    after_release = [bool]$afterRelease
    ran_a_after = [bool]$stateAAfter.ran
  }} | ConvertTo-Json -Compress
}}
finally {{
  if ($null -ne $job) {{
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
  }}
}}
"""
    )
    payload = json.loads(result.stdout)
    assert payload == {
        "name_alias_equal": True,
        "names_distinct": True,
        "name_shape": True,
        "contended": False,
        "contended_elapsed_ms": payload["contended_elapsed_ms"],
        "ran_a_while_held": False,
        "isolated": True,
        "ran_b": True,
        "after_release": True,
        "ran_a_after": True,
    }
    assert payload["contended_elapsed_ms"] < 1000


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_supervisor_quotes_out_of_task_job_native_arguments() -> None:
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
      $node.Name -eq 'ConvertTo-WindowsCommandLineArgument'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing native argument quote helper' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
@(
  [pscustomobject]@{{
    input = 'plain'
    output = ConvertTo-WindowsCommandLineArgument 'plain'
  }}
  [pscustomobject]@{{
    input = ''
    output = ConvertTo-WindowsCommandLineArgument ''
  }}
  [pscustomobject]@{{
    input = 'C:\\Program Files\\PowerShell\\pwsh.exe'
    output = ConvertTo-WindowsCommandLineArgument (
      'C:\\Program Files\\PowerShell\\pwsh.exe'
    )
  }}
  [pscustomobject]@{{
    input = 'plain"quote'
    output = ConvertTo-WindowsCommandLineArgument 'plain"quote'
  }}
  [pscustomobject]@{{
    input = 'C:\\path with space\\'
    output = ConvertTo-WindowsCommandLineArgument 'C:\\path with space\\'
  }}
) | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == [
        {"input": "plain", "output": "plain"},
        {"input": "", "output": '""'},
        {
            "input": r"C:\Program Files\PowerShell\pwsh.exe",
            "output": r'"C:\Program Files\PowerShell\pwsh.exe"',
        },
        {"input": 'plain"quote', "output": r'"plain\"quote"'},
        {
            "input": "C:\\path with space\\",
            "output": '"C:\\path with space\\\\"',
        },
    ]


@pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_out_of_task_job_launch_is_hidden_and_inherits_environment() -> None:
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
foreach ($name in @(
  'ConvertTo-WindowsCommandLineArgument',
  'Start-OutOfTaskJobPowerShell'
)) {{
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
$script:capturedArguments = $null
function Invoke-CimMethod {{
  [CmdletBinding()]
  param(
    [string] $ClassName,
    [string] $MethodName,
    [hashtable] $Arguments
  )
  if ($ClassName -cne 'Win32_Process' -or $MethodName -cne 'Create') {{
    throw 'unexpected process request'
  }}
  $script:capturedArguments = $Arguments
  [pscustomobject]@{{
    ReturnValue = 0
    ProcessId = 4242
  }}
}}
$actions = New-Object 'System.Collections.Generic.List[string]'
Start-OutOfTaskJobPowerShell `
  -HostPath 'C:\\Program Files\\PowerShell\\pwsh.exe' `
  -ArgumentList @(
    '-File',
    'C:\\Python\\tools.ps1',
    '-ConfigPath',
    'C:\\Path With Space\\config.json'
  ) `
  -Name 'consumer-loop:codex-tools-1'
$startup = $script:capturedArguments.ProcessStartupInformation
[pscustomobject]@{{
  command_line = [string]$script:capturedArguments.CommandLine
  create_flags = [uint32]$startup.CreateFlags
  show_window = [uint16]$startup.ShowWindow
  has_path = [bool]@(
    $startup.EnvironmentVariables |
      Where-Object {{ $_ -match '^(?i:PATH)=' }}
  ).Count
  action = [string]$actions[0]
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "command_line": (
            r'"C:\Program Files\PowerShell\pwsh.exe" '
            r"-File C:\Python\tools.ps1 "
            r'-ConfigPath "C:\Path With Space\config.json"'
        ),
        "create_flags": 0x08000400,
        "show_window": 0,
        "has_path": True,
        "action": (
            "RELAUNCHED consumer-loop:codex-tools-1 "
            "pid=4242 out-of-task-job"
        ),
    }


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_tools_writable_bridge_validation_is_pristine_safe(tmp_path: Path) -> None:
    wrapper = str(REBOOT / "start-wd-tools-consumer.ps1").replace("'", "''")
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    candidate = runtime_root / "outbox" / "codex-tools-1"
    root_quoted = str(runtime_root).replace("'", "''")
    candidate_quoted = str(candidate).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{wrapper}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'tools consumer parse failed' }}
foreach ($name in @(
  'Test-PathAtOrBelow',
  'Assert-DirectoryPathWithoutReparse'
)) {{
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
$allowMissingPassed = $false
Assert-DirectoryPathWithoutReparse `
  -Candidate '{candidate_quoted}' `
  -Root '{root_quoted}' `
  -AllowMissing
$allowMissingPassed = $true
$strictMissingFailed = $false
try {{
  Assert-DirectoryPathWithoutReparse `
    -Candidate '{candidate_quoted}' `
    -Root '{root_quoted}'
}} catch {{
  $strictMissingFailed = $true
}}
$createdDuringValidation = Test-Path -LiteralPath '{candidate_quoted}'
[void](New-Item -ItemType Directory -Path '{candidate_quoted}' -Force)
Assert-DirectoryPathWithoutReparse `
  -Candidate '{candidate_quoted}' `
  -Root '{root_quoted}'
[pscustomobject]@{{
  allow_missing_passed = $allowMissingPassed
  strict_missing_failed = $strictMissingFailed
  created_during_validation = $createdDuringValidation
  strict_existing_passed = $true
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "allow_missing_passed": True,
        "strict_missing_failed": True,
        "created_during_validation": False,
        "strict_existing_passed": True,
    }


@pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt",
    reason="Windows junction semantics are unavailable",
)
def test_reboot_trusted_path_guards_reject_reparse_components(
    tmp_path: Path,
) -> None:
    scripts_and_functions = {
        REBOOT / "Deploy-WdRebootBundle.ps1": [
            "Resolve-FullPath",
            "Assert-WdPathWithoutReparse",
        ],
        REBOOT / "start-wd-agent.ps1": ["Assert-LanePathWithoutReparse"],
        REBOOT / "start-wd-tools-consumer.ps1": [
            "Test-PathAtOrBelow",
            "Assert-DirectoryPathWithoutReparse",
            "Assert-FilePathWithoutReparse",
        ],
        REBOOT / "wd_supervisor.ps1": [
            "Assert-WdSupervisorPathWithoutReparse"
        ],
    }
    loaders: list[str] = []
    for script_path, function_names in scripts_and_functions.items():
        quoted_path = str(script_path).replace("'", "''")
        quoted_names = ", ".join(f"'{name}'" for name in function_names)
        loaders.append(
            f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{quoted_path}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'trusted-path source parse failed' }}
foreach ($name in @({quoted_names})) {{
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
"""
        )
    root = tmp_path / "trusted"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "plain.txt").write_text("inside", encoding="utf-8")
    (outside / "payload.txt").write_text("outside", encoding="utf-8")
    root_quoted = str(root).replace("'", "''")
    outside_quoted = str(outside).replace("'", "''")
    result = _run_powershell(
        "\n".join(loaders)
        + f"""
$root = [IO.Path]::GetFullPath('{root_quoted}')
$plain = Join-Path $root 'plain.txt'
$outside = [IO.Path]::GetFullPath('{outside_quoted}')
$junction = Join-Path $root 'redirected'
[void](New-Item -ItemType Junction -Path $junction -Target $outside)
$escaped = Join-Path $junction 'payload.txt'

[void](Assert-WdPathWithoutReparse -Path $plain -TrustedRoot $root -ExpectedType Leaf)
[void](Assert-LanePathWithoutReparse -Path $plain -TrustedRoot $root -ExpectedType Leaf)
Assert-FilePathWithoutReparse -Candidate $plain -Root $root
[void](Assert-WdSupervisorPathWithoutReparse -Path $plain -ExpectedType Leaf)

$results = [ordered]@{{}}
foreach ($case in @(
  [pscustomobject]@{{
    name = 'deploy'
    action = {{ Assert-WdPathWithoutReparse -Path $escaped -TrustedRoot $root -ExpectedType Leaf }}
  }},
  [pscustomobject]@{{
    name = 'lane'
    action = {{ Assert-LanePathWithoutReparse -Path $escaped -TrustedRoot $root -ExpectedType Leaf }}
  }},
  [pscustomobject]@{{
    name = 'tools'
    action = {{ Assert-FilePathWithoutReparse -Candidate $escaped -Root $root }}
  }},
  [pscustomobject]@{{
    name = 'supervisor'
    action = {{
      Assert-WdSupervisorPathWithoutReparse -Path $escaped -ExpectedType Leaf
    }}
  }}
)) {{
  $rejected = $false
  try {{ [void](& $case.action) }} catch {{ $rejected = $true }}
  $caseName = [string]$case.name
  $results[$caseName] = $rejected
}}
$results | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "deploy": True,
        "lane": True,
        "tools": True,
        "supervisor": True,
    }


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
def test_tools_lineage_rejects_older_process_with_reused_parent_pid() -> None:
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
      $node.Name -eq 'Test-ToolsLineageEdge'
  }},
  $true
)
. ([scriptblock]::Create($functionAst.Extent.Text))
$parent = [pscustomobject]@{{
  ProcessId = 500
  ParentProcessId = 1
  CreationDate = [DateTime]::UtcNow
}}
$olderUnrelated = [pscustomobject]@{{
  ProcessId = 501
  ParentProcessId = 500
  CreationDate = ([DateTime]$parent.CreationDate).AddMinutes(-5)
}}
$newerChild = [pscustomobject]@{{
  ProcessId = 502
  ParentProcessId = 500
  CreationDate = ([DateTime]$parent.CreationDate).AddSeconds(1)
}}
[pscustomobject]@{{
  older = Test-ToolsLineageEdge $parent $olderUnrelated
  newer = Test-ToolsLineageEdge $parent $newerChild
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {"older": False, "newer": True}


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


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_current_worktree_resume_allows_cold_boot_but_rejects_unattested_live_lane() -> None:
    launcher_path = REBOOT / "start-wd-all.ps1"
    launcher_text = launcher_path.read_text(encoding="utf-8")
    assert "duplicate live lane" in launcher_text
    launcher = str(launcher_path).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'fleet launcher parse failed' }}
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Resolve-LanePinState'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing lane pin resolver' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
$lane = [pscustomobject]@{{
  agent = 'codex-lead-1'
  worktree = 'C:\\Python\\project2'
  require_dedicated_worktree = $false
  resume_policy = 'current_worktree'
  branch = 'pinned/branch'
  head = '1111111111111111111111111111111111111111'
}}
$exact = Resolve-LanePinState `
  -Lane $lane `
  -PrimaryRepoRoot 'C:\\Python\\project2' `
  -ActualBranch $lane.branch `
  -ActualHead $lane.head `
  -LiveCount 0
$liveDrift = Resolve-LanePinState `
  -Lane $lane `
  -PrimaryRepoRoot 'C:\\Python\\project2' `
  -ActualBranch 'active/branch' `
  -ActualHead '2222222222222222222222222222222222222222' `
  -LiveCount 1 `
  -LiveGenerationAttested:$true
$coldDrift = Resolve-LanePinState `
  -Lane $lane `
  -PrimaryRepoRoot 'C:\\Python\\project2' `
  -ActualBranch 'active/branch' `
  -ActualHead '2222222222222222222222222222222222222222' `
  -LiveCount 0
try {{
  [void](Resolve-LanePinState `
    -Lane $lane `
    -PrimaryRepoRoot 'C:\\Python\\project2' `
    -ActualBranch 'active/branch' `
    -ActualHead $lane.head `
    -LiveCount 1 `
    -LiveGenerationAttested:$false)
  $unattestedLiveDriftRejected = $false
}}
catch {{
  $unattestedLiveDriftRejected = $_.Exception.Message -like '*branch mismatch*'
}}
$dedicatedLane = [pscustomobject]@{{
  agent = 'claude-rco-1'
  worktree = 'C:\\Python\\waggledance-agent-worktrees\\claude-rco-1'
  require_dedicated_worktree = $true
  resume_policy = 'current_worktree'
  branch = 'pinned/branch'
  head = '1111111111111111111111111111111111111111'
}}
$dedicatedCold = Resolve-LanePinState `
  -Lane $dedicatedLane `
  -PrimaryRepoRoot 'C:\\Python\\project2' `
  -ActualBranch 'active/branch' `
  -ActualHead $dedicatedLane.head `
  -LiveCount 0
$pinnedLane = [pscustomobject]@{{
  agent = 'pinned-agent'
  worktree = 'C:\\Python\\pinned'
  require_dedicated_worktree = $true
  resume_policy = 'pinned'
  branch = 'pinned/branch'
  head = '1111111111111111111111111111111111111111'
}}
try {{
  [void](Resolve-LanePinState `
    -Lane $pinnedLane `
    -PrimaryRepoRoot 'C:\\Python\\project2' `
    -ActualBranch 'active/branch' `
    -ActualHead $lane.head `
    -LiveCount 0)
  $missingBranchRejected = $false
}}
catch {{
  $missingBranchRejected = $_.Exception.Message -like '*branch mismatch*'
}}
try {{
  [void](Resolve-LanePinState `
    -Lane $pinnedLane `
    -PrimaryRepoRoot 'C:\\Python\\project2' `
    -ActualBranch $lane.branch `
    -ActualHead '2222222222222222222222222222222222222222' `
    -LiveCount 0)
  $missingHeadRejected = $false
}}
catch {{
  $missingHeadRejected = $_.Exception.Message -like '*HEAD mismatch*'
}}
[pscustomobject]@{{
  exact = [bool]$exact.exact
  live_drift_exact = [bool]$liveDrift.exact
  live_drift_summary = [string]$liveDrift.summary
  cold_drift_exact = [bool]$coldDrift.exact
  cold_drift_summary = [string]$coldDrift.summary
  unattested_live_drift_rejected = $unattestedLiveDriftRejected
  dedicated_cold_drift_exact = [bool]$dedicatedCold.exact
  missing_branch_rejected = $missingBranchRejected
  missing_head_rejected = $missingHeadRejected
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "exact": True,
        "live_drift_exact": False,
        "live_drift_summary": (
            "attested live process; current worktree drift accepted without relaunch"
        ),
        "cold_drift_exact": False,
        "cold_drift_summary": "cold resume from canonical current worktree",
        "unattested_live_drift_rejected": True,
        "dedicated_cold_drift_exact": False,
        "missing_branch_rejected": True,
        "missing_head_rejected": True,
    }


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.skipif(
    os.name != "nt",
    reason="Tools generation probe requires Windows path semantics",
)
def test_tools_process_generation_distinguishes_current_stale_and_legacy(
    tmp_path: Path,
) -> None:
    launcher_path = REBOOT / "start-wd-all.ps1"
    launcher = str(launcher_path).replace("'", "''")
    readiness_path = str(tmp_path / "tools-ready.json").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'fleet launcher parse failed' }}
foreach ($name in @(
    'Resolve-NormalizedPath',
    'ConvertTo-UtcDateTimeOffset',
    'Test-NamedCommandLineArgument',
    'Test-ToolsProcessReadiness',
    'Get-ToolsProcessState'
  )) {{
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
$config = [pscustomobject]@{{
  launcher_script = 'C:\\Python\\start-wd-tools-consumer.ps1'
  config_path = 'C:\\Python\\wd_supervisor_loop.json'
  readiness_path = '{readiness_path}'
  worktree = 'C:\\Python\\tools-worktree'
  branch = 'tools/test'
  head = '{TOOLS_HEAD}'
  agent = 'codex-tools-1'
  resume_policy = 'current_worktree'
  model = 'gpt-5.6-terra'
  reasoning_effort = 'high'
}}
$bundleGeneration = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$processStarted = [DateTimeOffset]::UtcNow.AddSeconds(-1)
$current = [pscustomobject]@{{
  ProcessId = 101
  Name = 'powershell.exe'
  CreationDate = $processStarted
  CommandLine = (
    'powershell.exe -File C:\\Python\\start-wd-tools-consumer.ps1 ' +
    '-ConfigPath C:\\Python\\wd_supervisor_loop.json ' +
    '-Generation ' + $bundleGeneration
  )
}}
$ready = [ordered]@{{
  schema = 'wd.tools-consumer-ready.v1'
  generation = $bundleGeneration
  pid = 101
  process_start_utc = $processStarted.ToString('o')
  config_path = $config.config_path
  worktree = $config.worktree
  branch = $config.branch
  head = $config.head
  baseline_branch = $config.branch
  baseline_head = $config.head
  resume_policy = $config.resume_policy
  model = $config.model
  reasoning_effort = $config.reasoning_effort
  target_state_manifested = $true
  target_state_id = 'wd-swarm-target-state-v1'
  ready_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
}}
$ready | ConvertTo-Json | Set-Content -LiteralPath '{readiness_path}' -Encoding UTF8
$stale = [pscustomobject]@{{
  ProcessId = 102
  Name = 'powershell.exe'
  CommandLine = (
    'powershell.exe -File C:\\Python\\start-wd-tools-consumer.ps1 ' +
    '-ConfigPath C:\\Python\\wd_supervisor_loop.json'
  )
}}
$legacy = [pscustomobject]@{{
  ProcessId = 103
  Name = 'pwsh.exe'
  CommandLine = (
    'pwsh.exe -File C:\\repo\\Start-AgentBridgeConsumerLoop.ps1 ' +
    '-Agent codex-tools-1'
  )
}}
$substringNoise = [pscustomobject]@{{
  ProcessId = 104
  Name = 'powershell.exe'
  CommandLine = (
    'powershell.exe -Command "Write-Output ' +
    'C:\\Python\\start-wd-tools-consumer.ps1"'
  )
}}
$state = Get-ToolsProcessState `
  -ToolsConfig $config `
  -Generation $bundleGeneration `
  -Processes @($current, $stale, $legacy, $substringNoise)
[pscustomobject]@{{
  current = @($state.current).Count
  current_pid = [int](@($state.current)[0].ProcessId)
  starting = @($state.starting).Count
  stale = @($state.stale).Count
  stale_pid = [int](@($state.stale)[0].ProcessId)
  legacy = @($state.legacy).Count
  legacy_pid = [int](@($state.legacy)[0].ProcessId)
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "current": 1,
        "current_pid": 101,
        "starting": 0,
        "stale": 1,
        "stale_pid": 102,
        "legacy": 1,
        "legacy_pid": 103,
    }


def test_supervisor_snapshot_is_structured_and_version_independent() -> None:
    snapshot = json.loads(
        (REBOOT / "wd_supervisor_loop.json").read_text(encoding="utf-8")
    )
    assert snapshot["schema"] == "wd.supervisor-loop.v2"
    tools = snapshot["tools_consumer"]
    assert snapshot["watchers"]["script_relative"] == (
        r"tools-bootstrap\.agent-bridge\bin\Watch-Bridge.ps1"
    )
    assert snapshot["watchers"]["dependency_relatives"] == [
        r"tools-bootstrap\.agent-bridge\bin\BridgeIncrementalReader.ps1",
        r"tools-bootstrap\.agent-bridge\bin\BridgeLogReader.ps1",
    ]
    assert tools["agent"] == "codex-tools-1"
    assert tools["agent_uuid"] == "7a8af68d-20bc-4598-9953-23c5dd98b102"
    assert tools["worktree"] == TOOLS_WORKTREE
    assert tools["primary_repo_root"] == r"C:\Python\project2"
    assert tools["expected_common_git_dir"] == r"C:\Python\project2\.git"
    assert tools["require_dedicated_worktree"] is True
    assert tools["expected_branch"] == TOOLS_BRANCH
    assert tools["expected_head"] == TOOLS_HEAD
    assert tools["readiness_path"] == (
        r"C:\Python\wd-reboot-runtime\codex-tools-1-ready.json"
    )
    assert tools["replacement_conflict_path"] == (
        r"C:\Python\wd-reboot-runtime"
        r"\codex-tools-1-replacement-conflict.json"
    )
    assert tools["log_dir"] == TOOLS_WORKTREE + (
        r"\.codex-audit\bridge-consumer-canonical-tools"
    )
    assert tools["sandbox"] == "workspace-write"
    assert tools["approval_policy"] == "never"
    assert "executable" not in tools
    assert tools["resume_policy"] == "current_worktree"
    assert tools["model"] == "gpt-5.6-terra"
    assert tools["reasoning_effort"] == "high"
    assert snapshot["target_state"]["id"] == "wd-swarm-target-state-v1"
    serialized = json.dumps(snapshot)
    assert "WindowsApps" not in serialized
    assert "7.6.3" not in serialized


@pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_tools_consumer_rejects_primary_repo_even_if_config_disables_dedication(
    tmp_path: Path,
) -> None:
    wrapper = REBOOT / "start-wd-tools-consumer.ps1"
    config = json.loads(
        (REBOOT / "wd_supervisor_loop.json").read_text(encoding="utf-8")
    )
    tools = config["tools_consumer"]
    tools["worktree"] = r"C:\Python\project2"
    tools["log_dir"] = r"C:\Python\project2\.codex-audit\tools-negative-test"
    tools["require_dedicated_worktree"] = False
    config_path = tmp_path / "unsafe-tools-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "-ConfigPath",
            str(config_path),
            "-Generation",
            TOOLS_HEAD,
            "-ValidateOnly",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "requires require_dedicated_worktree=true" in (
        result.stdout + result.stderr
    )


@pytest.mark.skipif(
    POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_tools_consumer_rejects_modified_tracked_bootstrap_helper(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "bootstrap-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "WD Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "wd@example.invalid"],
        check=True,
    )
    bootstrap = repo / "bootstrap"
    bootstrap.mkdir()
    script_path = bootstrap / "consumer.ps1"
    helper_path = bootstrap / "helper.ps1"
    script_path.write_text("Write-Output 'pinned'\n", encoding="utf-8")
    helper_path.write_text("Write-Output 'helper'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", "bootstrap"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "pin bootstrap"],
        check=True,
    )

    wrapper_path = REBOOT / "start-wd-tools-consumer.ps1"
    wrapper = str(wrapper_path).replace("'", "''")
    repo_quoted = str(repo).replace("'", "''")
    helper_quoted = str(helper_path).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{wrapper}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'tools consumer parse failed' }}
foreach ($name in @('Invoke-GitText', 'Assert-TrackedScriptsMatchHead')) {{
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
Assert-TrackedScriptsMatchHead `
  -Worktree '{repo_quoted}' `
  -RelativePaths @('bootstrap') `
  -Label 'bootstrap test'
[IO.File]::AppendAllText('{helper_quoted}', "# modified`n")
try {{
  Assert-TrackedScriptsMatchHead `
    -Worktree '{repo_quoted}' `
    -RelativePaths @('bootstrap') `
    -Label 'bootstrap test'
  $modifiedRejected = $false
}}
catch {{
  $modifiedRejected = $_.Exception.Message -like '*does not match pinned HEAD*'
}}
[pscustomobject]@{{ modified_rejected = $modifiedRejected }} |
  ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {"modified_rejected": True}


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
def test_tools_consumer_resolves_codex_by_path_directory_before_file_type(
    tmp_path: Path,
) -> None:
    earlier = tmp_path / "earlier"
    later = tmp_path / "later"
    earlier.mkdir()
    later.mkdir()
    earlier_exe = earlier / "codex.exe"
    later_cmd = later / "codex.cmd"
    earlier_exe.touch()
    later_cmd.touch()

    wrapper_path = REBOOT / "start-wd-tools-consumer.ps1"
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "-ExecutableNames @('codex.exe', 'codex.cmd')" in wrapper_text
    wrapper = str(wrapper_path).replace("'", "''")
    path_value = os.pathsep.join((str(earlier), str(later))).replace("'", "''")
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
      $node.Name -eq 'Find-ApplicationInPath'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing Find-ApplicationInPath' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
Find-ApplicationInPath `
  -PathValue '{path_value}' `
  -ExecutableNames @('codex.cmd', 'codex.exe')
"""
    )
    assert Path(result.stdout.strip()) == earlier_exe.resolve()


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
    tools_worktree = TOOLS_WORKTREE.replace("'", "''")
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
  -Worktree '{tools_worktree}' `
  -ShellPath 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
$arguments = Get-Content -LiteralPath '{capture_quoted}' -Raw | ConvertFrom-Json
$env:WD_FAKE_MODE = 'missing-marker'
try {{
  Test-CodexSandboxShell `
    -CodexCommand '{fake}' `
    -Worktree '{tools_worktree}' `
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
    -Worktree '{tools_worktree}' `
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
            TOOLS_WORKTREE,
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
    WINDOWS_POWERSHELL is None,
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
    assert "WD_TOOLS_CODEX_ADDITIONAL_WRITABLE_DIRS" in wrapper_text
    assert "WD_TOOLS_CODEX_REASONING_EFFORT" in wrapper_text
    assert "codex_additional_writable_directories" in wrapper_text
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
    runtime_root = tmp_path / "bridge root"
    runtime_root.mkdir()
    writable_dirs = [
        runtime_root / "shared",
        runtime_root / "outbox" / "codex-tools-1",
        runtime_root / "spool",
        runtime_root / "work_queue",
    ]
    for writable_dir in writable_dirs:
        writable_dir.mkdir(parents=True)
    writable_json = json.dumps([str(path) for path in writable_dirs]).replace(
        "'", "''"
    )
    runtime_root_quoted = str(runtime_root).replace("'", "''")
    safe_path = (
        str(Path(os.environ["SystemRoot"]) / "System32")
        + ";"
        + str(Path(os.environ["SystemRoot"]).anchor)
    ).replace("'", "''")
    invocation = f"""
$env:WD_TOOLS_CODEX_REAL_COMMAND = '{fake}'
$env:WD_TOOLS_CODEX_SAFE_PATH = '{safe_path}'
$env:WD_TOOLS_CODEX_ADDITIONAL_WRITABLE_DIRS = '{writable_json}'
$env:WD_TOOLS_CODEX_RUNTIME_ROOT = '{runtime_root_quoted}'
$env:WD_TOOLS_CODEX_REASONING_EFFORT = 'high'
$env:WD_FAKE_CAPTURE = '{capture_quoted}'
$env:Path = 'C:\\Program Files\\WindowsApps\\Microsoft.PowerShell_test'
"line one`nline two" | & '{shim}' `
  '--ask-for-approval' 'never' 'exec' '-C' 'C:\\Repo With Space' `
  '--sandbox' 'workspace-write' '-'
if ($null -ne $LASTEXITCODE) {{ exit $LASTEXITCODE }}
if ($?) {{ exit 0 }}
exit 1
"""
    success = _run_powershell(
        invocation,
        check=False,
        executable=WINDOWS_POWERSHELL,
    )
    assert success.returncode == 0, success.stderr
    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload == {
        "arguments": [
            "-c",
            'model_reasoning_effort="high"',
            "--ask-for-approval",
            "never",
            "exec",
            "-C",
            r"C:\Repo With Space",
            "--sandbox",
            "workspace-write",
            *[
                value
                for writable_dir in writable_dirs
                for value in ("--add-dir", str(writable_dir))
            ],
            "-",
        ],
        "stdin": "line one\nline two",
        "path": safe_path,
    }
    assert "windowsapps" not in payload["path"].casefold()

    nonzero_invocation = (
        "$env:WD_FAKE_MODE = 'nonzero'\n" + invocation
    )
    nonzero = _run_powershell(
        nonzero_invocation,
        check=False,
        executable=WINDOWS_POWERSHELL,
    )
    assert nonzero.returncode == 9

    native_codex = tmp_path / "fake-native-codex.cmd"
    native_codex.write_text(
        "@echo OpenAI Codex test banner 1>&2\n@exit /b 0\n",
        encoding="utf-8",
    )
    native = str(native_codex).replace("'", "''")
    native_stderr_invocation = invocation.replace(
        f"$env:WD_TOOLS_CODEX_REAL_COMMAND = '{fake}'",
        f"$env:WD_TOOLS_CODEX_REAL_COMMAND = '{native}'",
    )
    native_stderr = _run_powershell(
        native_stderr_invocation,
        check=False,
        executable=WINDOWS_POWERSHELL,
    )
    assert native_stderr.returncode == 0, native_stderr.stderr
    assert "OpenAI Codex test banner" in native_stderr.stderr


def test_real_launcher_updates_each_cli_once_and_dry_run_returns_first() -> None:
    launcher = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8")
    supervisor = (REBOOT / "wd_supervisor.ps1").read_text(encoding="utf-8")
    tools_wrapper = (
        REBOOT / "start-wd-tools-consumer.ps1"
    ).read_text(encoding="utf-8")
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
    assert "$bundleMode -ceq 'source' -and $DryRun" in launcher
    assert "duplicate supervisor-managed Tools consumers" in launcher
    assert "$bundleMode -ceq 'source'" in launcher
    assert "if (Test-Path -LiteralPath $handshakeDirectory)" in launcher
    assert "Grok model viability probe" in launcher
    assert "Tools consumer config differs from the committed deployed bundle" in launcher
    tools_validation = launcher.index("-ValidateOnly")
    assert tools_validation < launcher.index("Updating Codex CLI once")
    assert tools_validation < launcher.index("Resolving the current Grok model")
    assert tools_validation < launcher.index("$supervisorApplyOutput = &")
    assert "Tools consumer validation does not match fleet pins" in launcher
    assert "codex-tools-1 is headless and live" in launcher
    assert "-Generation $bundleGeneration" in launcher
    assert "Get-ToolsProcessState" in launcher
    assert "ask WD-Supervisor to replace stale generation" in launcher
    assert "Reconciling five bridge watchers" in launcher
    assert "supervisor reconciliation conflict" in supervisor
    assert "$toolsValidationLauncher = if ($bundleMode -ceq 'deployed')" in launcher
    assert "$toolsValidationConfig = if ($bundleMode -ceq 'deployed')" in launcher
    assert "source-tree reboot rehearsal requires -DryRun" in launcher
    assert "$toolsSnapshotPath = if ($bundleMode -ceq 'source')" in launcher
    deployed_gate = launcher.index("if ($bundleMode -ceq 'deployed')")
    assert deployed_gate < launcher.index(
        "Tools consumer config differs from the committed deployed bundle"
    )
    assert "-RequireDedicatedWorktree:$requireDedicatedWorktree" in tools_wrapper
    assert "-PrimaryRepoRoot $primaryRepoRoot" in tools_wrapper
    assert "tools process generation mismatch" in tools_wrapper
    assert "Tools bundle dependency hash mismatch" in tools_wrapper
    assert "machine Tools config differs from the externally anchored bundle" in (
        tools_wrapper
    )
    assert "WD_REBOOT_EXPECTED_MANIFEST_HASH" in tools_wrapper
    bootstrap_gates = [
        match.start()
        for match in re.finditer(
            re.escape("Assert-ToolsBootstrapIntegrity `"),
            tools_wrapper,
        )
    ]
    assert len(bootstrap_gates) == 8
    session_call = tools_wrapper.index(". $sessionScript `")
    target_event = tools_wrapper.index("-Status target_state_manifested `")
    assert bootstrap_gates[0] < bootstrap_gates[1] < session_call
    assert session_call < bootstrap_gates[2] < target_event < bootstrap_gates[3]
    initial_call = tools_wrapper.index("$initialOutput = @(& $consumerScript")
    assert bootstrap_gates[4] < initial_call < bootstrap_gates[5]
    wake_call = tools_wrapper.index("$wakeOutput = @(& $consumerScript @wakeArguments)")
    assert bootstrap_gates[6] < wake_call < bootstrap_gates[7]
    assert (
        "Join-Path $PSScriptRoot 'tools-bootstrap\\.agent-bridge\\bin'"
        in tools_wrapper
    )
    assert "tools-bootstrap/configs/bridge_identity_registry.json" in tools_wrapper
    assert "$foreverArguments['Forever']" not in tools_wrapper


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_tools_consumer_classifies_initial_tick_fail_closed() -> None:
    wrapper_path = REBOOT / "start-wd-tools-consumer.ps1"
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "initial_tick_exit_code = [int]$initialResult.exit_code" in wrapper_text
    assert "initial_tick_timed_out = $initialTickTimedOut" in wrapper_text
    assert "status = $initialReadyStatus" in wrapper_text
    assert "initial_tick_disposition = $initialTickDisposition" in wrapper_text
    assert "initial_tick_log_path = [string]$initialResult.log_path" in wrapper_text

    wrapper = str(wrapper_path).replace("'", "''")
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
      $node.Name -eq 'Get-InitialTickDisposition'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing initial timeout classifier' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
[pscustomobject]@{{
  exact_timeout = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 124
      codex_timed_out = $true
      ran_codex = $true
    }}
  )
  timeout_flag_missing = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{ exit_code = 124; ran_codex = $true }}
  )
  timeout_flag_false = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 124
      codex_timed_out = $false
      ran_codex = $true
    }}
  )
  timeout_string_false = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 124
      codex_timed_out = 'false'
      ran_codex = $true
    }}
  )
  timeout_string_true = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 124
      codex_timed_out = 'true'
      ran_codex = $true
    }}
  )
  codex_not_run = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 124
      codex_timed_out = $true
      ran_codex = $false
    }}
  )
  wrong_exit = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 1
      codex_timed_out = $true
      ran_codex = $true
    }}
  )
  native_failure = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 1
      codex_timed_out = $false
      ran_codex = $true
    }}
  )
  string_exit = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = '124'
      codex_timed_out = $true
      ran_codex = $true
    }}
  )
  inconsistent_success = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 0
      codex_timed_out = $true
      ran_codex = $true
    }}
  )
  success = Get-InitialTickDisposition -Result (
    [pscustomobject]@{{
      exit_code = 0
      codex_timed_out = $false
      ran_codex = $true
    }}
  )
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "exact_timeout": "recoverable_timeout",
        "timeout_flag_missing": "invalid",
        "timeout_flag_false": "recoverable_failure",
        "timeout_string_false": "invalid",
        "timeout_string_true": "invalid",
        "codex_not_run": "failed",
        "wrong_exit": "failed",
        "native_failure": "recoverable_failure",
        "string_exit": "invalid",
        "inconsistent_success": "failed",
        "success": "success",
    }


def test_deployer_requires_clean_pushed_commit_before_machine_writes() -> None:
    text = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    status_gate = text.index("'.agent-bridge/bin'")
    upstream_gate = text.index("reboot-bundle commit is not pushed")
    first_copy = text.index("Copy-Item -LiteralPath $sourcePaths[[string]$name]")
    assert status_gate < first_copy
    assert upstream_gate < first_copy
    assert "WD_REBOOT_INTEGRITY_CURRENT.sha256" in text
    assert "wd-reboot-backups" in text
    assert "'Invoke-WdToolsCodex.ps1'," in text
    assert "'tools-bootstrap/.agent-bridge/bin/{0}' -f $file.Name" in text
    assert (
        "'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'"
        in text
    )
    assert (
        "'tools-bootstrap/.agent-bridge/bin/BridgeIncrementalReader.ps1'"
        in text
    )
    assert (
        "'tools-bootstrap/.agent-bridge/bin/BridgeLogReader.ps1'"
        in text
    )
    assert "'tools-bootstrap/configs/bridge_identity_registry.json'" in text
    assert "'archive'," in text
    assert "'--format=zip'," in text
    assert "$materializedRebootRoot" in text
    assert "WD_REBOOT_EXPECTED_MANIFEST_HASH" in text
    assert "ExpectedManifestHash" in text
    assert "unexpected recursive file set" in text
    assert "$expectedInstalledSet" in text
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
    assert "function Assert-WdPathWithoutReparse" in text
    assert text.index("$installedManifestPath =") < text.index(
        "-Path $installedManifestPath `"
    ) < text.index("$installedManifest =")
    machine_creation = text.index(
        "New-Item -ItemType Directory -Path $machineFull"
    )
    strict_machine_check = text.index(
        "-Path $machineFull `", machine_creation
    )
    assert strict_machine_check < text.index("$backupRoot =")


def test_interactive_lane_uses_anchored_bundle_bootstrap() -> None:
    launcher = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    assert "tools-bootstrap\\.agent-bridge\\bin" in launcher
    assert "lane deployment manifest is not externally anchored" in launcher
    assert "Assert-LaneBootstrapIntegrity" in launcher
    assert "source lane launcher supports -DryRun only" in launcher
    assert "Join-Path $worktree '.agent-bridge\\bin" not in launcher
    assert "SkipWakeWatcher = $true" in launcher
    assert "function Assert-LanePathWithoutReparse" in launcher
    assert launcher.index("-Path $ManifestPath -TrustedRoot") < launcher.index(
        "$manifestSnapshot = Read-Utf8LaneSnapshot"
    )
    starter_dot_source = launcher.index(". $starter @sessionArgs")
    final_integrity = launcher.rfind(
        "Assert-LaneBootstrapIntegrity `", 0, starter_dot_source
    )
    assert final_integrity != -1
    assert "-Path $starter" in launcher[final_integrity:starter_dot_source]


def test_reboot_watchers_use_delete_share_reader_stack() -> None:
    bridge_bin = ROOT / ".agent-bridge" / "bin"
    incremental = (bridge_bin / "BridgeIncrementalReader.ps1").read_text(
        encoding="utf-8"
    )
    reader = (bridge_bin / "BridgeLogReader.ps1").read_text(encoding="utf-8")
    watcher = (bridge_bin / "Watch-Bridge.ps1").read_text(encoding="utf-8")
    monitor = (bridge_bin / "Monitor-AgentBridge.ps1").read_text(encoding="utf-8")
    interactive_reader = (bridge_bin / "Read-AgentBridge.ps1").read_text(
        encoding="utf-8"
    )

    assert "BridgeLogReader.ps1" in incremental
    assert "[System.IO.FileShare]::Delete" in reader
    assert "BridgeIncrementalReader.ps1" in watcher
    assert "BridgeIncrementalReader.ps1" in monitor
    assert "BridgeIncrementalReader.ps1" in interactive_reader
    assert "Get-Content -LiteralPath $eventsPath" not in interactive_reader

    supervisor = (REBOOT / "wd_supervisor.ps1").read_text(encoding="utf-8")
    assert "$configuration.watchers.dependency_relatives" in supervisor
    assert "supervisor watcher dependency set is not exact" in supervisor
    assert (
        "Assert-SupervisorBundleFileIntegrity -RelativePath $watcherDependency"
        in supervisor
    )
    assert "Read-BridgeEventTail -Path $eventsPath -MaxLines 80" in supervisor
    assert "Get-Content -LiteralPath $eventsPath" not in supervisor
    assert "-Path $eventsPath -ExpectedType Leaf" in supervisor

    tools_consumer = (REBOOT / "start-wd-tools-consumer.ps1").read_text(
        encoding="utf-8"
    )
    assert "'BridgeIncrementalReader.ps1'," in tools_consumer
    assert "'BridgeLogReader.ps1'," in tools_consumer
    assert "function Assert-FilePathWithoutReparse" in tools_consumer
    assert tools_consumer.index("-Candidate $configFull -Root") < (
        tools_consumer.index("$configSnapshot = Read-Utf8FileSnapshot")
    )


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell 5.1 is unavailable",
)
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
""",
        executable=WINDOWS_POWERSHELL,
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
    assert "[string] $Generation" in text
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
$global:LASTEXITCODE = 7
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
    tools_target = tmp_path / "tools-target.ps1"
    tools_target.write_text(
        """
[CmdletBinding()]
param(
  [string] $ConfigPath = '',
  [Parameter(Mandatory)] [string] $Generation,
  [switch] $ValidateOnly
)
[pscustomobject]@{
  config_path = $ConfigPath
  generation = $Generation
  validate_only = [bool]$ValidateOnly
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    deployment_manifest = tmp_path / "deployment-manifest.json"
    deployment_manifest.write_text('{"schema_version":1}\n', encoding="utf-8")

    deploy = str(REBOOT / "Deploy-WdRebootBundle.ps1").replace("'", "''")
    target_quoted = str(target).replace("'", "''")
    agent_target_quoted = str(agent_target).replace("'", "''")
    tools_target_quoted = str(tools_target).replace("'", "''")
    fleet_wrapper = str(tmp_path / "fleet-wrapper.ps1").replace("'", "''")
    agent_wrapper = str(tmp_path / "agent-wrapper.ps1").replace("'", "''")
    tools_wrapper = str(tmp_path / "tools-wrapper.ps1").replace("'", "''")
    deployment_manifest_quoted = str(deployment_manifest).replace("'", "''")
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
$manifestHash = (
  Get-FileHash -LiteralPath '{deployment_manifest_quoted}' -Algorithm SHA256
).Hash
$fleetText = New-ForwardingWrapper `
  -Target '{target_quoted}' `
  -ExpectedHash (Get-FileHash -LiteralPath '{target_quoted}' -Algorithm SHA256).Hash `
  -ExpectedManifestHash $manifestHash `
  -WrapperKind fleet
[IO.File]::WriteAllText('{fleet_wrapper}', $fleetText, $utf8)
$agentText = New-ForwardingWrapper `
  -Target '{agent_target_quoted}' `
  -ExpectedHash (Get-FileHash -LiteralPath '{agent_target_quoted}' -Algorithm SHA256).Hash `
  -ExpectedManifestHash $manifestHash `
  -WrapperKind agent `
  -FixedAgent fable-5
[IO.File]::WriteAllText('{agent_wrapper}', $agentText, $utf8)
$toolsText = New-ForwardingWrapper `
  -Target '{tools_target_quoted}' `
  -ExpectedHash (Get-FileHash -LiteralPath '{tools_target_quoted}' -Algorithm SHA256).Hash `
  -ExpectedManifestHash $manifestHash `
  -WrapperKind tools
[IO.File]::WriteAllText('{tools_wrapper}', $toolsText, $utf8)
$fleet = & '{fleet_wrapper}' `
  -RunId reboot-123 `
  -HandshakeTimeoutSeconds 42 `
  -SkipCliUpdate `
  -DryRun
$agent = & '{agent_wrapper}' -RunId lane-456 -DryRun
$tools = & '{tools_wrapper}' `
  -ConfigPath C:\\Python\\wd_supervisor_loop.json `
  -Generation {TOOLS_HEAD} `
  -ValidateOnly
[pscustomobject]@{{ fleet = $fleet; agent = $agent; tools = $tools }} |
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
    assert record["tools"] == {
        "config_path": r"C:\Python\wd_supervisor_loop.json",
        "generation": TOOLS_HEAD,
        "validate_only": True,
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

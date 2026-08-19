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


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_action_registration_preserves_enabled_state_and_hides_window() -> None:
    register = str(REBOOT / "Register-WdScheduledTasks.ps1").replace("'", "''")
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$global:Enabled = $false
$global:CurrentAction = $null
$global:SetCalls = 0
$global:EnableCalls = 0
$global:DisableCalls = 0
$global:DesiredArguments = ''
function Get-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName)
  return [pscustomobject]@{{
    Settings = [pscustomobject]@{{ Enabled = $global:Enabled }}
    Actions = @($global:CurrentAction)
  }}
}}
function New-ScheduledTaskAction {{
  [CmdletBinding()]
  param(
    [string] $Execute,
    [string] $Argument,
    [string] $WorkingDirectory
  )
  $global:DesiredArguments = $Argument
  return [pscustomobject]@{{
    Execute = $Execute
    Arguments = $Argument
    WorkingDirectory = $WorkingDirectory
  }}
}}
function Set-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName, [object[]] $Action)
  $global:SetCalls++
  $global:CurrentAction = @($Action)
}}
function Enable-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName)
  $global:EnableCalls++
}}
function Disable-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName)
  $global:DisableCalls++
}}

$expectedArguments = (
  '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden ' +
  '-ExecutionPolicy Bypass -File "{supervisor}" -Apply'
)
$results = New-Object 'System.Collections.Generic.List[object]'
foreach ($initialEnabled in @($false, $true)) {{
  $global:Enabled = [bool]$initialEnabled
  $global:CurrentAction = [pscustomobject]@{{
    Execute = 'old.exe'
    Arguments = 'old'
    WorkingDirectory = 'C:\\old'
  }}
  $global:SetCalls = 0
  & '{register}' `
    -Apply `
    -TaskName 'WD-Supervisor' `
    -SupervisorScript '{supervisor}' `
    6>$null
  [void]$results.Add([pscustomobject]@{{
    initial_enabled = [bool]$initialEnabled
    final_enabled = [bool]$global:Enabled
    set_calls = [int]$global:SetCalls
    arguments_exact = [string]$global:DesiredArguments -ceq $expectedArguments
  }})
}}

$global:Enabled = $false
$global:CurrentAction = [pscustomobject]@{{
  Execute = 'old.exe'
  Arguments = 'old'
  WorkingDirectory = 'C:\\old'
}}
$global:SetCalls = 0
& '{register}' `
  -TaskName 'WD-Supervisor' `
  -SupervisorScript '{supervisor}' `
  6>$null
[pscustomobject]@{{
  apply = $results.ToArray()
  disabled_preflight_set_calls = [int]$global:SetCalls
  enable_calls = [int]$global:EnableCalls
  disable_calls = [int]$global:DisableCalls
}} | ConvertTo-Json -Depth 5 -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "apply": [
            {
                "initial_enabled": False,
                "final_enabled": False,
                "set_calls": 1,
                "arguments_exact": True,
            },
            {
                "initial_enabled": True,
                "final_enabled": True,
                "set_calls": 1,
                "arguments_exact": True,
            },
        ],
        "disabled_preflight_set_calls": 0,
        "enable_calls": 0,
        "disable_calls": 0,
    }


def test_fleet_manifest_pins_exact_persistent_generations() -> None:
    manifest = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["git_executable"] == r"C:\Program Files\Git\cmd\git.exe"
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
    assert supervisor["recovery_state_root"] == r"C:\Python\wd-reboot-runtime"
    assert supervisor["watchers"]["replacement_conflict_root"] == (
        r"C:\Python\wd-reboot-runtime\watcher-replacement-conflicts"
    )
    assert supervisor["watchers"]["host_policy"] == (
        "system_windows_powershell_v1"
    )
    assert supervisor["watchers"]["git_executable"] == (
        r"C:\Program Files\Git\cmd\git.exe"
    )
    assert supervisor["watchers"]["source_repo_root"] == r"C:\Python\project2"
    assert "Invoke-WdToolsCodex.ps1" in (
        manifest["deployment"]["required_bundle_files"]
    )
    required_bundle_files = manifest["deployment"]["required_bundle_files"]
    assert "WD_SWARM_TARGET_STATE_V1.md" in required_bundle_files
    assert "Watch-CodexPrompts.ps1" in required_bundle_files
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
    assert tools_supervisor["python_executable"] == (
        r"C:\Users\janik\AppData\Local\Programs\Python\Python313\python.exe"
    )
    assert supervisor["tools_consumer"]["python_executable"] == (
        tools_supervisor["python_executable"]
    )
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
        "codex-lead-1": ("gpt-5.6-sol", "ultra"),
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


def test_codex_prompt_watcher_is_exact_copy_and_integrity_mapped() -> None:
    watcher = REBOOT / "Watch-CodexPrompts.ps1"
    assert hashlib.sha256(watcher.read_bytes()).hexdigest().upper() == (
        "71A623531DA29DC8DAB5BE491AA2FB6773D99A610CDB2AC0DE4FA707E52C4CAB"
    )
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "ops/windows/reboot/Watch-CodexPrompts.ps1 binary" in attributes
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!ops/windows/reboot/Watch-CodexPrompts.ps1" in ignore
    deployer = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    assert "'Watch-CodexPrompts.ps1'," in deployer
    runbook = (REBOOT / "BOOT_AFTER_REBOOT.md").read_text(encoding="utf-8")
    assert "intentionally dangerous" in runbook
    assert re.search(
        r"bypasses both that script's command\s+allowlist and denylist",
        runbook,
    )
    assert "neither receives a UI prompt watcher" in runbook


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_codex_prompt_watcher_state_is_exact_and_fail_closed() -> None:
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    result = _run_powershell(
        rf"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'launcher parse failed' }}
foreach ($name in @(
    'Test-NamedCommandLineArgument',
    'Test-WdCommandLineSwitchSequenceExact',
    'Get-WdCodexPromptWatcherState'
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
$watcher = 'C:\bundle generation\Watch-CodexPrompts.ps1'
$log = 'C:\Python\wd-reboot-runtime\prompt-watchers\codex-lead-1.log'
$hostPath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$canonicalCommand = (
  '"' + $hostPath + '" -NoProfile -ExecutionPolicy Bypass ' +
  '-File "' + $watcher + '" -AllowAll -NoAllNighter ' +
  '-TabTitle codex-lead-1 -LogPath "' + $log + '"'
)
$canonical = [pscustomobject]@{{
  ProcessId = 101
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = $canonicalCommand
}}
$canonical2 = [pscustomobject]@{{
  ProcessId = 102
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = $canonicalCommand
}}
$loose = [pscustomobject]@{{
  ProcessId = 103
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = (
    $canonicalCommand.Replace(
      $watcher,
      'C:\Python\Watch-CodexPrompts.ps1'
    )
  )
}}
$extraUnsafe = [pscustomobject]@{{
  ProcessId = 104
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = $canonicalCommand + ' -MinUserIdleSeconds 0'
}}
$wrongHost = [pscustomobject]@{{
  ProcessId = 105
  Name = 'powershell.exe'
  ExecutablePath = 'C:\untrusted\powershell.exe'
  CommandLine = $canonicalCommand
}}
$otherLane = [pscustomobject]@{{
  ProcessId = 106
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = $canonicalCommand.Replace(
    '-TabTitle codex-lead-1',
    '-TabTitle claude-rco-1'
  )
}}
function Get-State([object[]]$Processes) {{
  Get-WdCodexPromptWatcherState `
    -Processes $Processes `
    -WatcherScript $watcher `
    -TargetTitle codex-lead-1 `
    -LogPath $log `
    -ExpectedExecutable $hostPath
}}
$current = Get-State @($canonical)
$duplicate = Get-State @($canonical, $canonical2)
$looseState = Get-State @($loose)
$extraState = Get-State @($extraUnsafe)
$hostState = Get-State @($wrongHost)
$otherState = Get-State @($otherLane)
$missing = Get-State @()
[pscustomobject]@{{
  current = [string]$current.action
  current_exact = @($current.exact).Count
  duplicate = [string]$duplicate.action
  loose = [string]$looseState.action
  extra = [string]$extraState.action
  wrong_host = [string]$hostState.action
  other_lane = [string]$otherState.action
  missing = [string]$missing.action
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL or POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "current": "current",
        "current_exact": 1,
        "duplicate": "conflict",
        "loose": "conflict",
        "extra": "conflict",
        "wrong_host": "conflict",
        "other_lane": "launch",
        "missing": "launch",
    }


def test_codex_prompt_watcher_launch_is_lead_only_and_post_handshake() -> None:
    launcher = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8")
    launch_block = launcher.index(
        "# The UI prompt watcher is intentionally separate from the five bridge"
    )
    handshake_verify = launcher.index(
        'throw "bridge bootstrap handshake process is not alive for $launchedAgent"'
    )
    assert handshake_verify < launch_block
    assert "$promptWatcherTargetTitle = 'codex-lead-1'" in launcher
    assert "$promptWatcherWindowTitle = 'WD Codex Prompt Watcher'" in launcher
    assert "'-AllowAll'" in launcher[launch_block:]
    assert "'-NoAllNighter'" in launcher[launch_block:]
    assert "-TargetTitle $promptWatcherTargetTitle" in launcher[launch_block:]
    assert "claude-rco-1" not in launcher[launch_block:]
    assert "claude-rco-2" not in launcher[launch_block:]
    assert "codex-tools-1" not in launcher[launch_block:]


def test_target_state_is_binary_to_preserve_raw_manifest_hash() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "ops/windows/reboot/WD_SWARM_TARGET_STATE_V1.md binary" in attributes


def test_interactive_launchers_pin_agent_specific_models_and_effort() -> None:
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
    assert "@('low', 'medium', 'high', 'xhigh', 'max', 'ultra')" in agent_launcher
    assert "@('low', 'medium', 'high', 'xhigh', 'max')" in agent_launcher
    assert "gpt-5.6-sol'; effort = 'ultra'" in agent_launcher
    assert "sonnet'; effort = 'max'" in agent_launcher
    assert "fable'; effort = 'max'" in agent_launcher


def test_each_lane_manifests_hash_bound_target_before_model_launch() -> None:
    agent_launcher = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    tools_launcher = (REBOOT / "start-wd-tools-consumer.ps1").read_text(
        encoding="utf-8"
    )
    target = (REBOOT / "WD_SWARM_TARGET_STATE_V1.md").read_text(encoding="utf-8")

    assert agent_launcher.count("-Status target_state_manifested `") == 1
    assert tools_launcher.count("-Status target_state_manifested `") == 1
    assert agent_launcher.count("-Status append_canary `") == 1
    assert tools_launcher.count("-Status append_canary `") == 1
    assert agent_launcher.index("-Status target_state_manifested `") < (
        agent_launcher.index("model_selection = 'explicit'")
    )
    assert agent_launcher.index("model_selection = 'explicit'") < (
        agent_launcher.index("& $cliPath @launchArguments")
    )
    assert "cli_executable_sha256 = $cliExecutableHash" in agent_launcher
    assert tools_launcher.index("-Status target_state_manifested `") < (
        tools_launcher.index("$initialOutput = @(& $consumerScript")
    )
    assert agent_launcher.index("-Status append_canary `") < (
        agent_launcher.index("model_selection = 'explicit'")
    )
    assert tools_launcher.index("-Status append_canary `") < (
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
    assert supervisor.index("Initialize-WdSupervisorCommandLineParser") < (
        supervisor.index("$toolsProcesses = @(")
    )
    assert "Global\\WaggleDanceWatcherReconcileV1-" in supervisor
    assert "Global\\WaggleDanceToolsReconcileV1-" in supervisor
    assert "$mutex.WaitOne(0)" in supervisor
    assert "catch [Threading.AbandonedMutexException]" in supervisor
    assert "$mutex.ReleaseMutex()" in supervisor
    assert "$mutex.Dispose()" in supervisor
    watcher_lock_start = supervisor.index(
        "$watcherReconciled = Invoke-WdWatcherReconcileLocked"
    )
    watcher_lock_end = supervisor.index(
        "if ($toolsEnabled -and -not $watcherReconciliationBlocked)",
        watcher_lock_start,
    )
    watcher_lock = supervisor[watcher_lock_start:watcher_lock_end]
    assert watcher_lock.index("$watcherProcesses = @(") < watcher_lock.index(
        "foreach ($agent in $watcherAgents)"
    )
    assert "$watcherProcesses `" in watcher_lock
    assert watcher_lock.index("foreach ($agent in $watcherAgents)") < (
        watcher_lock.index("Start-OutOfTaskJobPowerShell `")
    )
    assert "watcher reconciliation mutex is owned by another supervisor" in (
        watcher_lock
    )
    assert watcher_lock.index("$watcherPlans =") < watcher_lock.index(
        "$watcherConflictMessages.Count -gt 0"
    )
    fleet_plan_body = supervisor[
        supervisor.index("function Invoke-WdWatcherFleetPlan") : supervisor.index(
            "function ConvertTo-SupervisorUtc"
        )
    ]
    assert fleet_plan_body.index("$ConflictMessages.Count -gt 0") < (
        fleet_plan_body.index("& $StopAction")
    )
    assert watcher_lock.index("New-WdWatcherReplacementMarker `") < (
        watcher_lock.index("Stop-VerifiedProcessTree `")
    )
    assert watcher_lock.index("Stop-VerifiedProcessTree `") < watcher_lock.index(
        "$verifiedWatcherProcesses = @("
    )
    assert "post-reconcile count=" in watcher_lock
    assert watcher_lock.index("post-reconcile count=") < watcher_lock.index(
        "Remove-WdWatcherReplacementMarker"
    )
    containment_gate = supervisor.index(
        "Invoke-WdReconciliationUnderDriverHold `", watcher_lock_start - 500
    )
    watcher_blocked = supervisor.index(
        "$watcherReconciliationBlocked = $true", watcher_lock_start
    )
    tools_gate = supervisor.index(
        "if ($toolsEnabled -and -not $watcherReconciliationBlocked)",
        watcher_blocked,
    )
    final_conflict = supervisor.index(
        "supervisor reconciliation conflict", tools_gate
    )
    assert containment_gate < watcher_lock_start < watcher_blocked < tools_gate
    assert tools_gate < final_conflict
    driver_hold_body = supervisor[
        supervisor.index("function Invoke-WdReconciliationUnderDriverHold") :
        supervisor.index("$configFull =", supervisor.index(
            "function Invoke-WdReconciliationUnderDriverHold"
        ))
    ]
    assert driver_hold_body.index(
        "Invoke-TaskContainment $standingTaskName"
    ) < driver_hold_body.index(". $ReconciliationAction")
    assert (
        "SKIPPED Tools reconciliation because watcher reconciliation is conflicted"
        in supervisor
    )
    tools_lock = supervisor.index("$toolsReconciled = Invoke-WdToolsReconcileLocked")
    tools_snapshot = supervisor.index("$toolsProcesses = @(", tools_lock)
    tools_decision = supervisor.index("$wrapperProcesses = @(", tools_snapshot)
    tools_contention = supervisor.index(
        "CONFLICT Tools reconciliation mutex is owned by another supervisor",
        tools_decision,
    )
    assert tools_lock < tools_snapshot < tools_decision < tools_contention
    assert "--no-replace-objects" in supervisor
    assert "Get-WdCanonicalTextGitBlobId" in supervisor
    assert "hash-object" not in supervisor
    assert "$env:GIT_CONFIG_NOSYSTEM = '1'" in supervisor
    assert "VERIFIED watcher:$agent" in watcher_lock
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
    assert "-InitialProcesses $toolsProcesses `" in supervisor
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


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_child_host_policy_ignores_path_applications(
    tmp_path: Path,
) -> None:
    fake_path = tmp_path / "fake-path"
    fake_path.mkdir()
    for name in ("pwsh.exe", "powershell.exe"):
        (fake_path / name).write_text("not an executable\n", encoding="utf-8")
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    fake_path_ps = str(fake_path).replace("'", "''")
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
    'Assert-WdSupervisorPathWithoutReparse',
    'Test-WdTrustedInstalledPowerShellExecutable',
    'Resolve-PowerShellChildHost'
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
$resolverAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Resolve-PowerShellChildHost'
  }},
  $true
)
$oldPath = $env:PATH
try {{
  $env:PATH = '{fake_path_ps};' + $oldPath
  $resolved = Resolve-PowerShellChildHost `
    -Policy 'system_windows_powershell_v1'
  $unsupportedFailed = $false
  try {{
    [void](Resolve-PowerShellChildHost -Policy 'path_auto')
  }}
  catch {{
    $unsupportedFailed = $_.Exception.Message -like (
      'unsupported watcher child-host policy*'
    )
  }}
  [pscustomobject]@{{
    resolved = $resolved
    fake_pwsh_selected = $resolved.Equals(
      (Join-Path '{fake_path_ps}' 'pwsh.exe'),
      [StringComparison]::OrdinalIgnoreCase
    )
    fake_powershell_selected = $resolved.Equals(
      (Join-Path '{fake_path_ps}' 'powershell.exe'),
      [StringComparison]::OrdinalIgnoreCase
    )
    resolver_uses_get_command = $resolverAst.Extent.Text -match 'Get-Command'
    unsupported_failed = $unsupportedFailed
  }} | ConvertTo-Json -Compress
}}
finally {{
  $env:PATH = $oldPath
}}
""",
        executable=WINDOWS_POWERSHELL,
    )
    payload = json.loads(result.stdout)
    assert WINDOWS_POWERSHELL_PATH is not None
    assert Path(payload["resolved"]) == WINDOWS_POWERSHELL_PATH
    assert payload["fake_pwsh_selected"] is False
    assert payload["fake_powershell_selected"] is False
    assert payload["resolver_uses_get_command"] is False
    assert payload["unsupported_failed"] is True


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
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
    'Write-SupervisorLogLine',
    'Assert-WdSupervisorPathWithoutReparse'
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
$afterApplyPreflightBytes = [Convert]::ToBase64String(
  [IO.File]::ReadAllBytes($path)
)
$applyMissingPath = Join-Path $parent 'apply-missing.log'
Initialize-SupervisorLogParent -Path $applyMissingPath -Apply
$applyMissingLength = (Get-Item -LiteralPath $applyMissingPath).Length
Write-SupervisorLogLine -Path $path -Line 'apply' -Apply
$junctionTarget = Join-Path (Split-Path -Parent $parent) 'junction-target'
$junctionParent = Join-Path (Split-Path -Parent $parent) 'junction-parent'
[void](New-Item -ItemType Directory -Path $junctionTarget)
[void](New-Item -ItemType Junction -Path $junctionParent -Target $junctionTarget)
$junctionExistingTarget = Join-Path $junctionTarget 'existing.log'
Set-Content -LiteralPath $junctionExistingTarget -Value 'junction-sentinel' -Encoding UTF8
$junctionExistingPath = Join-Path $junctionParent 'existing.log'
$junctionMissingPath = Join-Path $junctionParent 'missing.log'
$junctionBefore = [Convert]::ToBase64String(
  [IO.File]::ReadAllBytes($junctionExistingTarget)
)
$junctionInitRejected = $false
try {{ Initialize-SupervisorLogParent -Path $junctionMissingPath -Apply }}
catch {{ $junctionInitRejected = $true }}
$junctionAppendRejected = $false
try {{ Write-SupervisorLogLine -Path $junctionExistingPath -Line 'forbidden' -Apply }}
catch {{ $junctionAppendRejected = $true }}
$junctionAfter = [Convert]::ToBase64String(
  [IO.File]::ReadAllBytes($junctionExistingTarget)
)
[pscustomobject]@{{
  dry_parent_exists = $dryParentExists
  dry_file_exists = $dryFileExists
  dry_existing_bytes_equal = $beforeDryBytes -ceq $afterDryBytes
  apply_preflight_bytes_equal = $afterDryBytes -ceq $afterApplyPreflightBytes
  apply_missing_created_empty = (
    (Test-Path -LiteralPath $applyMissingPath -PathType Leaf) -and
    $applyMissingLength -eq 0
  )
  dry_existing_lines = @($dryExistingLines) -join "`n"
  apply_file_exists = Test-Path -LiteralPath $path -PathType Leaf
  lines = @(Get-Content -LiteralPath $path) -join "`n"
  junction_init_rejected = $junctionInitRejected
  junction_missing_not_created = -not (
    Test-Path -LiteralPath (Join-Path $junctionTarget 'missing.log')
  )
  junction_append_rejected = $junctionAppendRejected
  junction_existing_bytes_equal = $junctionBefore -ceq $junctionAfter
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "dry_parent_exists": False,
        "dry_file_exists": False,
        "dry_existing_bytes_equal": True,
        "apply_preflight_bytes_equal": True,
        "apply_missing_created_empty": True,
        "dry_existing_lines": "sentinel",
        "apply_file_exists": True,
        "lines": "sentinel\napply",
        "junction_init_rejected": True,
        "junction_missing_not_created": True,
        "junction_append_rejected": True,
        "junction_existing_bytes_equal": True,
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
def test_supervisor_watcher_reconcile_disposition_is_fail_closed() -> None:
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
      $node.Name -eq 'Get-WdWatcherReconcileDisposition'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing disposition function' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
[pscustomobject]@{{
  current = Get-WdWatcherReconcileDisposition 1 1 0 $false
  current_marked = Get-WdWatcherReconcileDisposition 1 1 0 $true
  missing = Get-WdWatcherReconcileDisposition 0 0 0 $false
  missing_marked = Get-WdWatcherReconcileDisposition 0 0 0 $true
  stale = Get-WdWatcherReconcileDisposition 1 0 1 $false
  stale_marked = Get-WdWatcherReconcileDisposition 1 0 1 $true
  unverified = Get-WdWatcherReconcileDisposition 1 0 0 $false
  duplicate_current = Get-WdWatcherReconcileDisposition 2 1 0 $false
  duplicate_stale = Get-WdWatcherReconcileDisposition 2 0 2 $false
  impossible_counts = Get-WdWatcherReconcileDisposition 0 1 0 $false
}} | ConvertTo-Json -Compress
"""
    )
    assert json.loads(result.stdout) == {
        "current": "current",
        "current_marked": "conflict",
        "missing": "launch",
        "missing_marked": "conflict",
        "stale": "replace",
        "stale_marked": "conflict",
        "unverified": "conflict",
        "duplicate_current": "conflict",
        "duplicate_stale": "conflict",
        "impossible_counts": "conflict",
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_launcher_mode_and_supervisor_invocation_plan_are_behavioral() -> None:
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'launcher parse failed' }}
foreach ($name in @(
    'Resolve-WdLauncherMode',
    'Assert-WdLauncherBundleMode',
    'Get-WdSupervisorInvocationPlan'
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
$bothRejected = $false
try {{ [void](Resolve-WdLauncherMode $true $true) }}
catch {{ $bothRejected = $true }}
$sourceApplyRejected = $false
try {{ Assert-WdLauncherBundleMode source Apply }}
catch {{ $sourceApplyRejected = $true }}
Assert-WdLauncherBundleMode source DryRun
Assert-WdLauncherBundleMode deployed Apply
$source = Get-WdSupervisorInvocationPlan `
  -BundleMode source `
  -SourceScript 'C:\\source\\wd_supervisor.ps1' `
  -SourceConfig 'C:\\source\\config.json' `
  -DeployedScript 'C:\\machine\\wd_supervisor.ps1'
$deployed = Get-WdSupervisorInvocationPlan `
  -BundleMode deployed `
  -SourceScript 'C:\\source\\wd_supervisor.ps1' `
  -SourceConfig 'C:\\source\\config.json' `
  -DeployedScript 'C:\\machine\\wd_supervisor.ps1'
[pscustomobject]@{{
  default_mode = Resolve-WdLauncherMode $false $false
  dry_mode = Resolve-WdLauncherMode $false $true
  apply_mode = Resolve-WdLauncherMode $true $false
  both_rejected = $bothRejected
  source_apply_rejected = $sourceApplyRejected
  source_preflight_script = [string]$source.preflight_script
  source_preflight_config = [string]$source.preflight_parameters.ConfigPath
  source_apply_script = [string]$source.apply_script
  deployed_preflight_script = [string]$deployed.preflight_script
  deployed_apply = [bool]$deployed.apply_parameters.Apply
  deployed_verify_count = [int]$deployed.verify_parameters.Count
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "default_mode": "DryRun",
        "dry_mode": "DryRun",
        "apply_mode": "Apply",
        "both_rejected": True,
        "source_apply_rejected": True,
        "source_preflight_script": r"C:\source\wd_supervisor.ps1",
        "source_preflight_config": r"C:\source\config.json",
        "source_apply_script": "",
        "deployed_preflight_script": r"C:\machine\wd_supervisor.ps1",
        "deployed_apply": True,
        "deployed_verify_count": 0,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_watcher_fleet_plan_is_atomic_and_fail_closed() -> None:
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
    'Assert-WdExactWatcherAgentSet',
    'Assert-WdRecoveryStatePaths',
    'Invoke-WdWatcherFleetPlan',
    'Test-WdWatcherPostReconcileState'
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
$agents = @(
  'codex-lead-1', 'codex-tools-1', 'claude-rco-1',
  'claude-rco-2', 'fable-5'
)
$exactSet = $true
try {{ Assert-WdExactWatcherAgentSet $agents }}
catch {{ $exactSet = $false }}
$duplicateRejected = $false
try {{ Assert-WdExactWatcherAgentSet @($agents[0], $agents[0], $agents[2..4]) }}
catch {{ $duplicateRejected = $true }}
$disabledToolsEscapeRejected = $false
try {{
  Assert-WdRecoveryStatePaths `
    -RecoveryStateRoot 'C:\\state' `
    -WatcherConflictRoot 'C:\\escaped\\watchers' `
    -ToolsEnabled $false
}}
catch {{ $disabledToolsEscapeRejected = $true }}
Assert-WdRecoveryStatePaths `
  -RecoveryStateRoot 'C:\\state' `
  -WatcherConflictRoot 'C:\\state\\watchers' `
  -ToolsEnabled $false
$plans = @([pscustomobject]@{{ action = 'replace'; agent = 'codex-lead-1' }})
$state = @{{ prepare = 0; report = 0; stop = 0; launch = 0 }}
$conflicted = Invoke-WdWatcherFleetPlan `
  -Plans $plans `
  -ConflictMessages @('one lane conflicted') `
  -Apply `
  -PrepareAction {{ param($items) $state.prepare += 1 }} `
  -ReportAction {{ param($item) $state.report += 1 }} `
  -StopAction {{ param($item) $state.stop += 1 }} `
  -LaunchAction {{ param($item) $state.launch += 1 }}
$conflictCallbacks = "$($state.prepare)/$($state.report)/$($state.stop)/$($state.launch)"
$state = @{{ prepare = 0; report = 0; stop = 0; launch = 0 }}
$replaced = Invoke-WdWatcherFleetPlan `
  -Plans $plans `
  -Apply `
  -PrepareAction {{ param($items) $state.prepare += 1 }} `
  -ReportAction {{ param($item) $state.report += 1 }} `
  -StopAction {{ param($item) $state.stop += 1 }} `
  -LaunchAction {{ param($item) $state.launch += 1 }}
$replaceCallbacks = "$($state.prepare)/$($state.report)/$($state.stop)/$($state.launch)"
$state = @{{ prepare = 0; stop = 0; launch = 0 }}
$stopFailed = $false
try {{
  [void](Invoke-WdWatcherFleetPlan `
    -Plans $plans `
    -Apply `
    -PrepareAction {{ param($items) $state.prepare += 1 }} `
    -ReportAction {{ param($item) }} `
    -StopAction {{ param($item) $state.stop += 1; throw 'stop failure' }} `
    -LaunchAction {{ param($item) $state.launch += 1 }})
}}
catch {{ $stopFailed = $true }}
[pscustomobject]@{{
  exact_set = $exactSet
  duplicate_rejected = $duplicateRejected
  disabled_tools_escape_rejected = $disabledToolsEscapeRejected
  conflicted = [bool]$conflicted
  conflict_callbacks = $conflictCallbacks
  replaced = [bool]$replaced
  replace_callbacks = $replaceCallbacks
  stop_failed = $stopFailed
  stop_failure_callbacks = "$($state.prepare)/$($state.stop)/$($state.launch)"
  post_exact = Test-WdWatcherPostReconcileState 1 1
  post_missing = Test-WdWatcherPostReconcileState 0 0
  post_duplicate = Test-WdWatcherPostReconcileState 2 1
  post_wrong = Test-WdWatcherPostReconcileState 1 0
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "exact_set": True,
        "duplicate_rejected": True,
        "disabled_tools_escape_rejected": True,
        "conflicted": False,
        "conflict_callbacks": "0/0/0/0",
        "replaced": True,
        "replace_callbacks": "1/0/1/1",
        "stop_failed": True,
        "stop_failure_callbacks": "1/1/0",
        "post_exact": True,
        "post_missing": False,
        "post_duplicate": False,
        "post_wrong": False,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_launcher_recovers_and_releases_an_abandoned_fleet_mutex() -> None:
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'launcher parse failed' }}
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Enter-WdFleetRebootMutex'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing fleet mutex function' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
Add-Type -TypeDefinition @'
using System;
using System.Threading;
public static class WdAbandonedMutexOwner {{
    public static void Abandon(string name) {{
        Exception failure = null;
        ManualResetEventSlim acquired = new ManualResetEventSlim(false);
        Thread owner = new Thread(delegate() {{
            try {{
                Mutex mutex = Mutex.OpenExisting(name);
                mutex.WaitOne();
                acquired.Set();
            }}
            catch (Exception error) {{
                failure = error;
                acquired.Set();
            }}
        }});
        owner.IsBackground = true;
        owner.Start();
        if (!acquired.Wait(5000)) {{
            throw new TimeoutException("mutex owner did not acquire");
        }}
        owner.Join();
        if (failure != null) {{
            throw failure;
        }}
    }}
}}
'@
$name = 'Local\\WdFleetMutexTest-' + [Guid]::NewGuid().ToString('N')
$mutex = [Threading.Mutex]::new($false, $name)
$recovered = $false
try {{
  [WdAbandonedMutexOwner]::Abandon($name)
  $recovered = Enter-WdFleetRebootMutex -Mutex $mutex
  if (-not $recovered) {{ throw 'abandoned mutex was not acquired' }}
  $mutex.ReleaseMutex()
}}
finally {{
  $mutex.Dispose()
}}
$probe = [Threading.Mutex]::new($false, $name)
try {{
  $reacquired = $probe.WaitOne(0)
  if ($reacquired) {{ $probe.ReleaseMutex() }}
}}
finally {{
  $probe.Dispose()
}}
[pscustomobject]@{{ recovered = $recovered; reacquired = $reacquired }} |
  ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {"recovered": True, "reacquired": True}


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
@pytest.mark.skipif(
    os.name != "nt",
    reason="watcher parser requires Windows CommandLineToArgvW",
)
def test_supervisor_only_replaces_hash_bound_stale_bundle_watcher(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
    subprocess.run(
        ["git", "-C", str(source_repo), "config", "core.longpaths", "true"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "config", "user.name", "WD Test"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source_repo),
            "config",
            "user.email",
            "wd-test@example.invalid",
        ],
        check=True,
    )
    source_script = source_repo / ".agent-bridge" / "bin" / "Watch-Bridge.ps1"
    source_script.parent.mkdir(parents=True)
    source_script.write_text("Write-Output 'watcher'\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source_repo), "add", ".agent-bridge/bin/Watch-Bridge.ps1"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-q", "-m", "watcher fixture"],
        check=True,
    )
    generation = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", generation)
    source_bytes = source_script.read_bytes()
    source_script.write_text("Write-Output 'replacement'\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source_repo), "add", ".agent-bridge/bin/Watch-Bridge.ps1"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-q", "-m", "replacement fixture"],
        check=True,
    )
    replacement_generation = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(source_repo), "replace", generation, replacement_generation],
        check=True,
    )
    info_attributes = source_repo / ".git" / "info" / "attributes"
    info_attributes.write_text(
        ".agent-bridge/bin/Watch-Bridge.ps1 filter=wdtest\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source_repo),
            "config",
            "filter.wdtest.clean",
            "printf filtered",
        ],
        check=True,
    )
    poison_repo = tmp_path / "poison-repo"
    poison_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(poison_repo)], check=True)
    bundle_parent = tmp_path / "wd-reboot-bundles"
    bundle_root = bundle_parent / generation
    stale_script = (
        bundle_root
        / "tools-bootstrap"
        / ".agent-bridge"
        / "bin"
        / "Watch-Bridge.ps1"
    )
    stale_script.parent.mkdir(parents=True)
    stale_script.write_bytes(source_bytes)
    relative = "tools-bootstrap/.agent-bridge/bin/Watch-Bridge.ps1"
    manifest_path = bundle_root / "deployment-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_commit": generation,
                "files": {
                    relative: hashlib.sha256(stale_script.read_bytes())
                    .hexdigest()
                    .upper()
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    current_script = (
        bundle_parent
        / ("b" * 40)
        / "tools-bootstrap"
        / ".agent-bridge"
        / "bin"
        / "Watch-Bridge.ps1"
    )
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    bundle_parent_ps = str(bundle_parent).replace("'", "''")
    stale_script_ps = str(stale_script).replace("'", "''")
    current_script_ps = str(current_script).replace("'", "''")
    manifest_ps = str(manifest_path).replace("'", "''")
    source_repo_ps = str(source_repo).replace("'", "''")
    poison_git_dir_ps = str(poison_repo / ".git").replace("'", "''")
    git_executable = shutil.which("git.exe") or shutil.which("git")
    assert git_executable is not None
    git_executable_ps = git_executable.replace("'", "''")
    pwsh_launcher = shutil.which("pwsh.exe") or shutil.which("pwsh")
    trusted_pwsh_path = ""
    if pwsh_launcher is not None:
        pwsh_probe = subprocess.run(
            [
                pwsh_launcher,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::Write((Get-Process -Id $PID).Path)",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        trusted_pwsh_path = pwsh_probe.stdout.strip()
    trusted_pwsh_path_ps = trusted_pwsh_path.replace("'", "''")
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $env:SystemRoot (
  'System32\\WindowsPowerShell\\v1.0\\Modules\\' +
  'Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1'
)) -Force -ErrorAction Stop
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
foreach ($name in @(
  'Read-Utf8SupervisorSnapshot',
    'Resolve-WdSupervisorGitApplication',
    'Invoke-WdSupervisorGitCapture',
    'Get-WdCanonicalTextGitBlobId',
    'Assert-WdSupervisorPathWithoutReparse',
    'Initialize-WdSupervisorCommandLineParser',
    'ConvertFrom-WdWindowsCommandLine',
    'Test-WdPowerShellSwitchToken',
    'Test-WdPowerShellHostOptionToken',
    'Test-WdPowerShellFileSwitchToken',
    'Get-WdPowerShellHostKind',
    'Test-WdEncodedCommandValue',
    'Get-WdPowerShellFileInvocation',
    'Test-WdTrustedInstalledPowerShellExecutable',
    'Test-WdReplaceableStaleWatcherProcess'
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
$gitPath = '{git_executable_ps}'
$hostPath = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'
$runtime = 'C:\\runtime'
$command = (
  'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{stale_script_ps}" ' +
  '-Agent codex-lead-1 -RuntimeRoot ' + $runtime
)
$process = [pscustomobject]@{{
  ProcessId = 101
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = $command
}}
$valid = Test-WdReplaceableStaleWatcherProcess `
  -Process $process `
  -ExpectedExecutable $hostPath `
  -CurrentScriptPath '{current_script_ps}' `
  -Agent 'codex-lead-1' `
  -RuntimeRoot $runtime `
  -BundleParent '{bundle_parent_ps}' `
  -SourceRepositoryRoot '{source_repo_ps}' `
  -GitExecutable $gitPath
$migratedHost = $null
if ('{trusted_pwsh_path_ps}') {{
  $migratedProcess = [pscustomobject]@{{
    ProcessId = 104
    Name = 'pwsh.exe'
    ExecutablePath = '{trusted_pwsh_path_ps}'
    CommandLine = $command.Replace('powershell.exe', 'pwsh.exe')
  }}
  $migratedHost = Test-WdReplaceableStaleWatcherProcess `
    -Process $migratedProcess `
    -ExpectedExecutable $hostPath `
    -CurrentScriptPath '{current_script_ps}' `
    -Agent 'codex-lead-1' `
    -RuntimeRoot $runtime `
    -BundleParent '{bundle_parent_ps}' `
    -SourceRepositoryRoot '{source_repo_ps}' `
    -GitExecutable $gitPath
}}
$env:GIT_DIR = '{poison_git_dir_ps}'
try {{
  $poisonedEnvironment = Test-WdReplaceableStaleWatcherProcess `
    -Process $process `
    -ExpectedExecutable $hostPath `
    -CurrentScriptPath '{current_script_ps}' `
    -Agent 'codex-lead-1' `
    -RuntimeRoot $runtime `
    -BundleParent '{bundle_parent_ps}' `
    -SourceRepositoryRoot '{source_repo_ps}' `
    -GitExecutable $gitPath
}}
finally {{
  Remove-Item Env:GIT_DIR -ErrorAction SilentlyContinue
}}
$wrongAgent = Test-WdReplaceableStaleWatcherProcess `
  -Process $process `
  -ExpectedExecutable $hostPath `
  -CurrentScriptPath '{current_script_ps}' `
  -Agent 'claude-rco-1' `
  -RuntimeRoot $runtime `
  -BundleParent '{bundle_parent_ps}' `
  -SourceRepositoryRoot '{source_repo_ps}' `
  -GitExecutable $gitPath
$currentProcess = [pscustomobject]@{{
  ProcessId = 102
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = $command.Replace('{stale_script_ps}', '{current_script_ps}')
}}
$current = Test-WdReplaceableStaleWatcherProcess `
  -Process $currentProcess `
  -ExpectedExecutable $hostPath `
  -CurrentScriptPath '{current_script_ps}' `
  -Agent 'codex-lead-1' `
  -RuntimeRoot $runtime `
  -BundleParent '{bundle_parent_ps}' `
  -SourceRepositoryRoot '{source_repo_ps}' `
  -GitExecutable $gitPath
$extraProcess = [pscustomobject]@{{
  ProcessId = 103
  Name = 'powershell.exe'
  ExecutablePath = $hostPath
  CommandLine = $command + ' -Unexpected value'
}}
$extra = Test-WdReplaceableStaleWatcherProcess `
  -Process $extraProcess `
  -ExpectedExecutable $hostPath `
  -CurrentScriptPath '{current_script_ps}' `
  -Agent 'codex-lead-1' `
  -RuntimeRoot $runtime `
  -BundleParent '{bundle_parent_ps}' `
  -SourceRepositoryRoot '{source_repo_ps}' `
  -GitExecutable $gitPath
$manifest = Get-Content -LiteralPath '{manifest_ps}' -Raw | ConvertFrom-Json
$manifest.files.'{relative}' = ('0' * 64)
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '{manifest_ps}' -Encoding UTF8
$forged = Test-WdReplaceableStaleWatcherProcess `
  -Process $process `
  -ExpectedExecutable $hostPath `
  -CurrentScriptPath '{current_script_ps}' `
  -Agent 'codex-lead-1' `
  -RuntimeRoot $runtime `
  -BundleParent '{bundle_parent_ps}' `
  -SourceRepositoryRoot '{source_repo_ps}' `
  -GitExecutable $gitPath
$manifest.files.'{relative}' = (
  Get-FileHash -LiteralPath '{stale_script_ps}' -Algorithm SHA256
).Hash
Set-Content -LiteralPath '{stale_script_ps}' -Value 'self-signed tamper' -Encoding UTF8
$manifest.files.'{relative}' = (
  Get-FileHash -LiteralPath '{stale_script_ps}' -Algorithm SHA256
).Hash
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '{manifest_ps}' -Encoding UTF8
$selfSignedTamper = Test-WdReplaceableStaleWatcherProcess `
  -Process $process `
  -ExpectedExecutable $hostPath `
  -CurrentScriptPath '{current_script_ps}' `
  -Agent 'codex-lead-1' `
  -RuntimeRoot $runtime `
  -BundleParent '{bundle_parent_ps}' `
  -SourceRepositoryRoot '{source_repo_ps}' `
  -GitExecutable $gitPath
[pscustomobject]@{{
  valid = $valid
  migrated_host = $migratedHost
  poisoned_environment = $poisonedEnvironment
  wrong_agent = $wrongAgent
  current = $current
  extra = $extra
  forged = $forged
  self_signed_tamper = $selfSignedTamper
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "valid": True,
        "migrated_host": True if trusted_pwsh_path else None,
        "poisoned_environment": True,
        "wrong_agent": False,
        "current": False,
        "extra": False,
        "forged": False,
        "self_signed_tamper": False,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_watcher_marker_precedes_stop_and_preserves_evidence(
    tmp_path: Path,
) -> None:
    recovery_root = tmp_path / "recovery"
    recovery_root.mkdir()
    marker = recovery_root / "watchers" / "codex-lead-1.json"
    marker_ps = str(marker).replace("'", "''")
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
    'Assert-WdSupervisorPathWithoutReparse',
    'New-WdWatcherReplacementMarker',
    'Remove-WdWatcherReplacementMarker',
    'Write-ToolsReplacementConflict'
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
$process = [pscustomobject]@{{
  ProcessId = 101
  CreationDate = '20260815120000.000000+000'
}}
New-WdWatcherReplacementMarker `
  -Path '{marker_ps}' `
  -Agent codex-lead-1 `
  -StaleProcess $process `
  -StaleGeneration ('a' * 40) `
  -TargetGeneration ('b' * 40)
$record = Get-Content -LiteralPath '{marker_ps}' -Raw | ConvertFrom-Json
$before = [Convert]::ToBase64String([IO.File]::ReadAllBytes('{marker_ps}'))
Write-ToolsReplacementConflict `
  -Path '{marker_ps}' `
  -RootPid 999 `
  -Reason 'must not overwrite watcher marker'
$after = [Convert]::ToBase64String([IO.File]::ReadAllBytes('{marker_ps}'))
$duplicateRejected = $false
try {{
  New-WdWatcherReplacementMarker `
    -Path '{marker_ps}' `
    -Agent codex-lead-1 `
    -StaleProcess $process `
    -StaleGeneration ('a' * 40) `
    -TargetGeneration ('b' * 40)
}}
catch {{ $duplicateRejected = $true }}
Remove-WdWatcherReplacementMarker -Path '{marker_ps}'
[pscustomobject]@{{
  schema = [string]$record.schema
  agent = [string]$record.agent
  stale_generation = [string]$record.stale_generation
  target_generation = [string]$record.target_generation
  stale_pid = [int]$record.stale_pid
  evidence_preserved = $before -ceq $after
  duplicate_rejected = $duplicateRejected
  removed = -not (Test-Path -LiteralPath '{marker_ps}')
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "schema": "wd.watcher-replacement-in-progress.v1",
        "agent": "codex-lead-1",
        "stale_generation": "a" * 40,
        "target_generation": "b" * 40,
        "stale_pid": 101,
        "evidence_preserved": True,
        "duplicate_rejected": True,
        "removed": True,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_driver_hold_disables_then_stops_and_verifies() -> None:
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
      $node.Name -eq 'Invoke-TaskContainment'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing containment function' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
$script:enabled = $true
$script:running = $true
$script:leaveRunning = $false
$script:order = [Collections.Generic.List[string]]::new()
function Get-OptionalScheduledTask {{
  param([string] $TaskName)
  return [pscustomobject]@{{
    State = $(if ($script:running) {{ 'Running' }} else {{ 'Disabled' }})
    Settings = [pscustomobject]@{{ Enabled = $script:enabled }}
  }}
}}
function Disable-ScheduledTask {{
  [CmdletBinding()] param([string] $TaskName)
  $script:order.Add('disable')
  $script:enabled = $false
}}
function Stop-ScheduledTask {{
  [CmdletBinding()] param([string] $TaskName)
  $script:order.Add('stop')
  if (-not $script:leaveRunning) {{ $script:running = $false }}
}}
$actions = [Collections.Generic.List[string]]::new()
$Apply = $true
Invoke-TaskContainment WD-Test 'test HOLD'
$successOrder = $script:order -join '|'
$successVerified = @($actions | Where-Object {{
    [string]$_ -like 'HOLD verified*'
  }}).Count -eq 1
$script:enabled = $true
$script:running = $true
$script:leaveRunning = $true
$script:order.Clear()
$verificationRejected = $false
try {{ Invoke-TaskContainment WD-Test 'test HOLD' }}
catch {{ $verificationRejected = $true }}
[pscustomobject]@{{
  success_order = $successOrder
  success_verified = $successVerified
  verification_rejected = $verificationRejected
  failed_order = $script:order -join '|'
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "success_order": "disable|stop",
        "success_verified": True,
        "verification_rejected": True,
        "failed_order": "disable|stop",
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_driver_hold_dominates_reconciliation_failures() -> None:
    supervisor = str(REBOOT / "wd_supervisor.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
$requiredFunctions = @(
  'Invoke-TaskContainment',
  'Invoke-WdReconciliationUnderDriverHold'
)
foreach ($functionName in $requiredFunctions) {{
  $functionAst = $ast.Find(
    {{
      param($node)
      $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $functionName
    }},
    $true
  )
  if ($null -eq $functionAst) {{ throw "missing function: $functionName" }}
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}
function Get-RequiredText {{
  param([object] $Object, [string] $Name)
  return [string]$Object.$Name
}}
function Get-OptionalScheduledTask {{
  param([string] $TaskName)
  $state = $script:taskStates[$TaskName]
  if ($null -eq $state) {{ return $null }}
  return [pscustomobject]@{{
    State = $(if ($state.running) {{ 'Running' }} else {{ 'Disabled' }})
    Settings = [pscustomobject]@{{ Enabled = [bool]$state.enabled }}
  }}
}}
function Disable-ScheduledTask {{
  [CmdletBinding()] param([string] $TaskName)
  $script:taskStates[$TaskName].enabled = $false
  $script:taskOrder.Add("disable:$TaskName")
}}
function Stop-ScheduledTask {{
  [CmdletBinding()] param([string] $TaskName)
  $script:taskStates[$TaskName].running = $false
  $script:taskOrder.Add("stop:$TaskName")
}}
function Test-LegacyDriverProvenNonApply {{ return $false }}
$Apply = $true
$actions = [Collections.Generic.List[string]]::new()
$driver = [pscustomobject]@{{
  standing_task = 'WD-Standing'
  legacy_task = 'WD-Legacy'
  legacy_script_path = 'C:\\Python\\legacy-driver.ps1'
}}
$failureStages = @(
  'marker',
  'watcher-stop',
  'watcher-launch',
  'tools-stop',
  'postverify'
)
$results = [Collections.Generic.List[object]]::new()
foreach ($failureStage in $failureStages) {{
  $script:taskStates = @{{
    'WD-Standing' = [pscustomobject]@{{ enabled = $true; running = $true }}
    'WD-Legacy' = [pscustomobject]@{{ enabled = $true; running = $true }}
  }}
  $script:taskOrder = [Collections.Generic.List[string]]::new()
  $script:reconcileOrder = [Collections.Generic.List[string]]::new()
  $script:failureStage = $failureStage
  $caught = ''
  try {{
    Invoke-WdReconciliationUnderDriverHold `
      -Driver $driver `
      -ReconciliationAction {{
        foreach ($stage in $failureStages) {{
          $script:reconcileOrder.Add($stage)
          if ($stage -ceq $script:failureStage) {{
            throw "injected:$stage"
          }}
        }}
      }}
  }}
  catch {{ $caught = [string]$_.Exception.Message }}
  $results.Add([pscustomobject]@{{
    stage = $failureStage
    caught = $caught
    task_order = $script:taskOrder -join '|'
    reconcile_order = $script:reconcileOrder -join '|'
    standing_held = (
      -not $script:taskStates['WD-Standing'].enabled -and
      -not $script:taskStates['WD-Standing'].running
    )
    legacy_held = (
      -not $script:taskStates['WD-Legacy'].enabled -and
      -not $script:taskStates['WD-Legacy'].running
    )
  }})
}}
$results | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    payload = json.loads(result.stdout)
    assert [item["stage"] for item in payload] == [
        "marker",
        "watcher-stop",
        "watcher-launch",
        "tools-stop",
        "postverify",
    ]
    for item in payload:
        assert item["caught"] == f'injected:{item["stage"]}'
        assert item["task_order"] == (
            "disable:WD-Standing|stop:WD-Standing|"
            "disable:WD-Legacy|stop:WD-Legacy"
        )
        assert item["standing_held"] is True
        assert item["legacy_held"] is True
        assert item["reconcile_order"].split("|")[-1] == item["stage"]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize(
    ("mutex_name_function", "invoke_function", "prefix"),
    [
        (
            "Get-WdWatcherReconcileMutexName",
            "Invoke-WdWatcherReconcileLocked",
            r"Global\WaggleDanceWatcherReconcileV1-",
        ),
        (
            "Get-WdToolsReconcileMutexName",
            "Invoke-WdToolsReconcileLocked",
            r"Global\WaggleDanceToolsReconcileV1-",
        ),
    ],
)
def test_supervisor_reconcile_mutex_is_scoped_and_nonblocking(
    tmp_path: Path,
    mutex_name_function: str,
    invoke_function: str,
    prefix: str,
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
    '{mutex_name_function}',
    '{invoke_function}'
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
$nameA = {mutex_name_function} -RuntimeRoot $rootA
$nameAlias = {mutex_name_function} `
  -RuntimeRoot ($rootA.ToLowerInvariant() + '\\')
$nameB = {mutex_name_function} -RuntimeRoot $rootB
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
  $contended = {invoke_function} -RuntimeRoot $rootA -Action {{
    $stateAWhileHeld.ran = $true
  }}
  $stopwatch.Stop()
  $stateB = @{{ ran = $false }}
  $isolated = {invoke_function} -RuntimeRoot $rootB -Action {{
    $stateB.ran = $true
  }}
  [IO.File]::WriteAllText('{release}', 'release')
  Wait-Job -Job $job -Timeout 10 | Out-Null
  if ($job.State -ne 'Completed') {{ throw "holder state: $($job.State)" }}
  Receive-Job -Job $job -ErrorAction Stop | Out-Null
  $stateAAfter = @{{ ran = $false }}
  $afterRelease = {invoke_function} -RuntimeRoot $rootA -Action {{
    $stateAAfter.ran = $true
  }}
  [pscustomobject]@{{
    name_alias_equal = $nameA -ceq $nameAlias
    names_distinct = $nameA -cne $nameB
    name_shape = (
      $nameA.StartsWith(
        '{prefix}',
        [StringComparison]::Ordinal
      ) -and
      $nameA.Substring('{prefix}'.Length) `
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
        REBOOT / "start-wd-all.ps1": ["Assert-WdFleetPathWithoutReparse"],
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
$danglingTarget = Join-Path $outside 'dangling-target'
[void](New-Item -ItemType Directory -Path $danglingTarget)
$dangling = Join-Path $root 'dangling'
[void](New-Item -ItemType Junction -Path $dangling -Target $danglingTarget)
Remove-Item -LiteralPath $danglingTarget -Force
$danglingFuture = Join-Path $dangling 'future.txt'

[void](Assert-WdPathWithoutReparse -Path $plain -TrustedRoot $root -ExpectedType Leaf)
[void](Assert-WdFleetPathWithoutReparse -Path $plain -TrustedRoot $root -ExpectedType Leaf)
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
    name = 'fleet'
    action = {{ Assert-WdFleetPathWithoutReparse -Path $escaped -TrustedRoot $root -ExpectedType Leaf }}
  }},
  [pscustomobject]@{{
    name = 'fleet_dangling'
    action = {{ Assert-WdFleetPathWithoutReparse -Path $danglingFuture -TrustedRoot $root -ExpectedType Any -AllowMissing }}
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
        "fleet": True,
        "fleet_dangling": True,
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


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_launcher_revalidates_process_identity_sets_after_fleet_mutex() -> None:
    launcher_path = REBOOT / "start-wd-all.ps1"
    launcher = str(launcher_path).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'launcher parse failed' }}
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Test-WdProcessIdentitySetExact'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing identity-set function' }}
. ([scriptblock]::Create($functionAst.Extent.Text))
$one = [pscustomobject]@{{
  ProcessId = 101
  CreationDate = '20260816120000.000000+000'
  CommandLine = 'powershell.exe -File C:\\Python\\start-wd-agent.ps1 -Agent codex-lead-1'
}}
$same = [pscustomobject]@{{
  ProcessId = 101
  CreationDate = '20260816120000.000000+000'
  CommandLine = 'powershell.exe -File C:\\Python\\start-wd-agent.ps1 -Agent codex-lead-1'
}}
$pidChanged = [pscustomobject]@{{
  ProcessId = 102
  CreationDate = $one.CreationDate
  CommandLine = $one.CommandLine
}}
$startChanged = [pscustomobject]@{{
  ProcessId = $one.ProcessId
  CreationDate = '20260816120100.000000+000'
  CommandLine = $one.CommandLine
}}
$commandChanged = [pscustomobject]@{{
  ProcessId = $one.ProcessId
  CreationDate = $one.CreationDate
  CommandLine = $one.CommandLine + ' -Different'
}}
[pscustomobject]@{{
  exact = Test-WdProcessIdentitySetExact -Expected @($one) -Actual @($same)
  reorder = Test-WdProcessIdentitySetExact -Expected @($one, $pidChanged) -Actual @($pidChanged, $same)
  pid_changed = Test-WdProcessIdentitySetExact -Expected @($one) -Actual @($pidChanged)
  start_changed = Test-WdProcessIdentitySetExact -Expected @($one) -Actual @($startChanged)
  command_changed = Test-WdProcessIdentitySetExact -Expected @($one) -Actual @($commandChanged)
  added = Test-WdProcessIdentitySetExact -Expected @($one) -Actual @($same, $pidChanged)
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "exact": True,
        "reorder": True,
        "pid_changed": False,
        "start_changed": False,
        "command_changed": False,
        "added": False,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_supervisor_task_activation_is_exact_proven_and_fail_closed() -> None:
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'launcher parse failed' }}
foreach ($name in @(
    'Test-WdSupervisorTaskActionExact',
    'Get-WdSingleScheduledTask',
    'Get-WdAccountSid',
    'Test-WdSupervisorTaskEnvelopeExact',
    'Get-WdSupervisorTaskActivationPlan',
    'Set-WdSupervisorTaskHeld',
    'Enable-WdSupervisorTaskAfterRestore'
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
$script:Enabled = $false
$script:State = 'Disabled'
$script:EnableCalls = 0
$script:DisableCalls = 0
$script:StartCalls = 0
$script:StopCalls = 0
$script:ActiveInstance = $false
$script:Result = 0
$script:LastRun = [DateTime]'2000-01-01T00:00:00Z'
$script:DriftWhenEnabled = $false
$script:TerminalState = 'Ready'
$script:Clock = [DateTime]::UtcNow
$script:ObservedTaskPaths = New-Object 'System.Collections.Generic.List[string]'
$expectedExe = 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
$expectedArgs = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\\Python\\wd_supervisor.ps1" -Apply'
$expectedSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$expectedBoundary = '2026-06-30T08:28:36+03:00'
function New-FakeTask {{
  $arguments = if ($script:DriftWhenEnabled -and $script:Enabled) {{ 'drift' }} else {{ $expectedArgs }}
  return [pscustomobject]@{{
    TaskName = 'WD-Supervisor'
    TaskPath = '\\'
    State = $script:State
    Actions = @([pscustomobject]@{{
      Execute = $expectedExe
      Arguments = $arguments
      WorkingDirectory = 'C:\\Python'
    }})
    Principal = [pscustomobject]@{{
      UserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
      LogonType = 'Interactive'
      RunLevel = 'Limited'
    }}
    Settings = [pscustomobject]@{{
      Enabled = $script:Enabled
      MultipleInstances = 'IgnoreNew'
      AllowDemandStart = $true
      StartWhenAvailable = $true
      Hidden = $true
      ExecutionTimeLimit = 'PT5M'
    }}
    Triggers = @([pscustomobject]@{{
      CimClass = [pscustomobject]@{{ CimClassName = 'MSFT_TaskTimeTrigger' }}
      Enabled = $true
      StartBoundary = $expectedBoundary
      Repetition = [pscustomobject]@{{
        Interval = 'PT30M'
        Duration = 'P3650D'
        StopAtDurationEnd = $true
      }}
    }})
  }}
}}
function Add-ObservedTaskPath {{
  param([AllowEmptyString()] [string] $TaskPath)
  [void]$script:ObservedTaskPaths.Add($TaskPath)
}}
function Get-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName, [string] $TaskPath)
  Add-ObservedTaskPath -TaskPath $TaskPath
  return (New-FakeTask)
}}
function Get-ScheduledTaskInfo {{
  [CmdletBinding()]
  param([string] $TaskName, [string] $TaskPath)
  Add-ObservedTaskPath -TaskPath $TaskPath
  return [pscustomobject]@{{
    LastRunTime = $script:LastRun
    LastTaskResult = $script:Result
  }}
}}
function Enable-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName, [string] $TaskPath)
  Add-ObservedTaskPath -TaskPath $TaskPath
  $script:EnableCalls++
  $script:Enabled = $true
  $script:State = 'Ready'
}}
function Disable-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName, [string] $TaskPath)
  Add-ObservedTaskPath -TaskPath $TaskPath
  $script:DisableCalls++
  $script:Enabled = $false
  $script:State = 'Disabled'
}}
function Start-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName, [string] $TaskPath)
  Add-ObservedTaskPath -TaskPath $TaskPath
  $script:StartCalls++
  $script:State = 'Running'
}}
function Stop-ScheduledTask {{
  [CmdletBinding()]
  param([string] $TaskName, [string] $TaskPath)
  Add-ObservedTaskPath -TaskPath $TaskPath
  $script:StopCalls++
  $script:ActiveInstance = $false
  $script:State = if ($script:Enabled) {{ 'Ready' }} else {{ 'Disabled' }}
}}
function Start-Sleep {{
  if ($script:State -eq 'Running') {{
    $script:State = $script:TerminalState
    $script:LastRun = [DateTime]::UtcNow
  }}
  $script:Clock = $script:Clock.AddSeconds(1)
}}
function Get-Date {{
  $script:Clock = $script:Clock.AddSeconds(1)
  return $script:Clock
}}
$disabledPlan = Get-WdSupervisorTaskActivationPlan (New-FakeTask)
$first = Enable-WdSupervisorTaskAfterRestore `
  -TaskName WD-Supervisor `
  -ExpectedExecutable $expectedExe `
  -ExpectedArguments $expectedArgs `
  -ExpectedWorkingDirectory 'C:\\Python' `
  -ExpectedPrincipalSid $expectedSid `
  -ExpectedStartBoundary $expectedBoundary `
  -WaitSeconds 10
$enabledPlan = Get-WdSupervisorTaskActivationPlan (New-FakeTask)
$second = Enable-WdSupervisorTaskAfterRestore `
  -TaskName WD-Supervisor `
  -ExpectedExecutable $expectedExe `
  -ExpectedArguments $expectedArgs `
  -ExpectedWorkingDirectory 'C:\\Python' `
  -ExpectedPrincipalSid $expectedSid `
  -ExpectedStartBoundary $expectedBoundary `
  -WaitSeconds 10
$script:Enabled = $false
$script:State = 'Disabled'
$script:DriftWhenEnabled = $true
$driftRejected = $false
try {{
  [void](Enable-WdSupervisorTaskAfterRestore `
    -TaskName WD-Supervisor `
    -ExpectedExecutable $expectedExe `
    -ExpectedArguments $expectedArgs `
    -ExpectedWorkingDirectory 'C:\\Python' `
    -ExpectedPrincipalSid $expectedSid `
    -ExpectedStartBoundary $expectedBoundary `
    -WaitSeconds 10)
}} catch {{ $driftRejected = $true }}
$driftHeld = -not $script:Enabled -and $script:State -eq 'Disabled'
$script:DriftWhenEnabled = $false
$script:Enabled = $false
$script:State = 'Disabled'
$script:Result = 9
$nonzeroRejected = $false
try {{
  [void](Enable-WdSupervisorTaskAfterRestore `
    -TaskName WD-Supervisor `
    -ExpectedExecutable $expectedExe `
    -ExpectedArguments $expectedArgs `
    -ExpectedWorkingDirectory 'C:\\Python' `
    -ExpectedPrincipalSid $expectedSid `
    -ExpectedStartBoundary $expectedBoundary `
    -WaitSeconds 10)
}} catch {{ $nonzeroRejected = $true }}
$nonzeroHeld = -not $script:Enabled -and $script:State -eq 'Disabled'
$script:Result = 0
$script:Enabled = $true
$script:State = 'Ready'
$script:DriftWhenEnabled = $true
$entryDriftRejected = $false
try {{
  [void](Enable-WdSupervisorTaskAfterRestore `
    -TaskName WD-Supervisor `
    -ExpectedExecutable $expectedExe `
    -ExpectedArguments $expectedArgs `
    -ExpectedWorkingDirectory 'C:\\Python' `
    -ExpectedPrincipalSid $expectedSid `
    -ExpectedStartBoundary $expectedBoundary `
    -WaitSeconds 10)
}} catch {{ $entryDriftRejected = $true }}
$entryDriftHeld = -not $script:Enabled -and $script:State -eq 'Disabled'
$script:DriftWhenEnabled = $false
$inconsistentRejected = $false
$script:Enabled = $true
$script:State = 'Disabled'
try {{ [void](Get-WdSupervisorTaskActivationPlan (New-FakeTask)) }}
catch {{ $inconsistentRejected = $true }}
$inconsistentContained = $false
try {{
  [void](Enable-WdSupervisorTaskAfterRestore `
    -TaskName WD-Supervisor `
    -ExpectedExecutable $expectedExe `
    -ExpectedArguments $expectedArgs `
    -ExpectedWorkingDirectory 'C:\\Python' `
    -ExpectedPrincipalSid $expectedSid `
    -ExpectedStartBoundary $expectedBoundary `
    -WaitSeconds 10)
}} catch {{
  $inconsistentContained = -not $script:Enabled -and $script:State -eq 'Disabled'
}}
$script:Enabled = $false
$script:State = 'Disabled'
$script:TerminalState = 'Unknown'
$unknownRejected = $false
try {{
  [void](Enable-WdSupervisorTaskAfterRestore `
    -TaskName WD-Supervisor `
    -ExpectedExecutable $expectedExe `
    -ExpectedArguments $expectedArgs `
    -ExpectedWorkingDirectory 'C:\\Python' `
    -ExpectedPrincipalSid $expectedSid `
    -ExpectedStartBoundary $expectedBoundary `
    -WaitSeconds 10)
}} catch {{ $unknownRejected = $true }}
$unknownHeld = -not $script:Enabled -and $script:State -eq 'Disabled'
$stopBeforeMasked = $script:StopCalls
$script:Enabled = $true
$script:State = 'Running'
$script:ActiveInstance = $true
Set-WdSupervisorTaskHeld `
  -TaskName WD-Supervisor `
  -ExpectedExecutable $expectedExe `
  -ExpectedArguments $expectedArgs `
  -ExpectedWorkingDirectory 'C:\\Python' `
  -ExpectedPrincipalSid $expectedSid `
  -ExpectedStartBoundary $expectedBoundary
$maskedActiveStopped = (
  $script:StopCalls -eq ($stopBeforeMasked + 1) -and
  -not $script:ActiveInstance -and
  -not $script:Enabled -and
  $script:State -eq 'Disabled'
)
$stopBeforeInactive = $script:StopCalls
$script:Enabled = $false
$script:State = 'Disabled'
$script:ActiveInstance = $false
Set-WdSupervisorTaskHeld `
  -TaskName WD-Supervisor `
  -ExpectedExecutable $expectedExe `
  -ExpectedArguments $expectedArgs `
  -ExpectedWorkingDirectory 'C:\\Python' `
  -ExpectedPrincipalSid $expectedSid `
  -ExpectedStartBoundary $expectedBoundary
$inactiveDidNotStop = $script:StopCalls -eq $stopBeforeInactive
[pscustomobject]@{{
  disabled_plan = [bool]$disabledPlan.enable_after_restore
  first_changed = [bool]$first.changed
  first_result = [int64]$first.last_task_result
  enabled_plan = [bool]$enabledPlan.enable_after_restore
  second_changed = [bool]$second.changed
  enable_calls = $script:EnableCalls
  start_calls = $script:StartCalls
  drift_rejected = $driftRejected
  drift_held = $driftHeld
  nonzero_rejected = $nonzeroRejected
  nonzero_held = $nonzeroHeld
  entry_drift_rejected = $entryDriftRejected
  entry_drift_held = $entryDriftHeld
  inconsistent_rejected = $inconsistentRejected
  inconsistent_contained = $inconsistentContained
  unknown_rejected = $unknownRejected
  unknown_held = $unknownHeld
  masked_active_stopped = $maskedActiveStopped
  inactive_did_not_stop = $inactiveDidNotStop
  disable_calls = $script:DisableCalls
  task_paths_exact = (
    $script:ObservedTaskPaths.Count -gt 0 -and
    @($script:ObservedTaskPaths | Where-Object {{
          -not ([string]$_).Equals(
            [string][IO.Path]::DirectorySeparatorChar,
            [StringComparison]::Ordinal
          )
        }}).Count -eq 0
  )
}} | ConvertTo-Json -Compress
""",
        check=False,
        executable=WINDOWS_POWERSHELL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "disabled_plan": True,
        "first_changed": True,
        "first_result": 0,
        "enabled_plan": False,
        "second_changed": False,
        "enable_calls": 4,
        "start_calls": 4,
        "drift_rejected": True,
        "drift_held": True,
        "nonzero_rejected": True,
        "nonzero_held": True,
        "entry_drift_rejected": True,
        "entry_drift_held": True,
        "inconsistent_rejected": True,
        "inconsistent_contained": True,
        "unknown_rejected": True,
        "unknown_held": True,
        "masked_active_stopped": True,
        "inactive_did_not_stop": True,
        "disable_calls": 7,
        "task_paths_exact": True,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_launcher_bridge_safety_baseline_is_append_only_and_spool_exact(
    tmp_path: Path,
) -> None:
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    temp = str(tmp_path).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'launcher parse failed' }}
foreach ($name in @(
    'Resolve-NormalizedPath',
    'Assert-WdFleetPathWithoutReparse',
    'Initialize-WdFleetFileIdentityNative',
    'Get-WdFleetOpenFileIdentity',
    'Get-WdFleetOpenPrefixHash',
    'Get-WdBridgePrefixSnapshot',
    'Assert-WdBridgePrefixPreserved',
    'Get-WdSpoolInventory',
    'Assert-WdSpoolInventoryExact',
    'New-WdBridgeSafetyBaseline',
    'Assert-WdBridgeSafetyBaseline'
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
$root = '{temp}'
$runtime = Join-Path $root 'runtime'
$shared = Join-Path $runtime 'shared'
$spool = Join-Path $runtime 'spool'
$recovery = Join-Path $root 'recovery'
$watcher = Join-Path $recovery 'watcher-conflicts'
$tools = Join-Path $recovery 'tools-conflict.json'
[void](New-Item -ItemType Directory -Path $shared,$spool,$recovery -Force)
$canonical = Join-Path $shared 'events.jsonl'
$bytes = New-Object byte[] 2097173
for ($i = 0; $i -lt $bytes.Length; $i++) {{ $bytes[$i] = [byte]($i % 251) }}
[IO.File]::WriteAllBytes($canonical, $bytes)
[IO.File]::WriteAllText((Join-Path $spool 'a.jsonl'), "a`n")
[IO.File]::WriteAllText((Join-Path $spool 'b.jsonl'), "b`n")
$archive = Join-Path $spool 'archive'
$replayed = Join-Path $archive 'replayed'
$delivered = Join-Path $spool 'delivered'
[void](New-Item -ItemType Directory -Path $replayed,$delivered -Force)
[IO.File]::WriteAllText((Join-Path $archive 'old.jsonl'), "old`n")
$baseline = New-WdBridgeSafetyBaseline `
  -RuntimeRoot $runtime `
  -SnapshotRuntimeRoot $runtime `
  -RecoveryStateRoot $recovery `
  -ToolsConflictPath $tools `
  -WatcherConflictRoot $watcher
[IO.File]::AppendAllText($canonical, "append`n")
Assert-WdBridgeSafetyBaseline -Baseline $baseline
$prefixRejected = $false
$stream = [IO.File]::Open($canonical, 'Open', 'ReadWrite', 'Read')
try {{
  $original = $stream.ReadByte()
  $stream.Position = 0
  $stream.WriteByte([byte](($original + 1) % 255))
}} finally {{ $stream.Dispose() }}
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $prefixRejected = $true }}
$stream = [IO.File]::Open($canonical, 'Open', 'ReadWrite', 'Read')
try {{ $stream.WriteByte([byte]$original) }} finally {{ $stream.Dispose() }}
$spoolAddRejected = $false
$extra = Join-Path $spool 'c.jsonl'
[IO.File]::WriteAllText($extra, "c`n")
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $spoolAddRejected = $true }}
Remove-Item -LiteralPath $extra -Force
$nestedChangeRejected = $false
$nested = Join-Path $archive 'old.jsonl'
[IO.File]::AppendAllText($nested, "changed`n")
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $nestedChangeRejected = $true }}
[IO.File]::WriteAllText($nested, "old`n")
$pendingRejected = $false
$pending = Join-Path $replayed '.failed.PENDING'
[IO.File]::WriteAllText($pending, "pending`n")
try {{ [void](Get-WdSpoolInventory -Path $spool) }}
catch {{ $pendingRejected = $true }}
Remove-Item -LiteralPath $pending -Force
$pendingDirectoryRejected = $false
$pendingDirectory = Join-Path $archive 'directory.pending'
[void](New-Item -ItemType Directory -Path $pendingDirectory)
try {{ [void](Get-WdSpoolInventory -Path $spool) }}
catch {{ $pendingDirectoryRejected = $true }}
Remove-Item -LiteralPath $pendingDirectory -Recurse -Force
$nestedReparseRejected = $false
$alternateNested = Join-Path $root 'alternate-nested'
$nestedLink = Join-Path $archive 'linked'
[void](New-Item -ItemType Directory -Path $alternateNested)
[void](New-Item -ItemType Junction -Path $nestedLink -Target $alternateNested)
try {{ [void](Get-WdSpoolInventory -Path $spool) }}
catch {{ $nestedReparseRejected = $true }}
Remove-Item -LiteralPath $nestedLink -Force
Remove-Item -LiteralPath $alternateNested -Recurse -Force
$spoolReparseRejected = $false
$originalSpool = "$spool.original"
$alternateSpool = "$spool.alternate"
Move-Item -LiteralPath $spool -Destination $originalSpool
[void](New-Item -ItemType Directory -Path $alternateSpool)
[IO.File]::WriteAllText((Join-Path $alternateSpool 'a.jsonl'), "a`n")
[IO.File]::WriteAllText((Join-Path $alternateSpool 'b.jsonl'), "b`n")
[void](New-Item -ItemType Junction -Path $spool -Target $alternateSpool)
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $spoolReparseRejected = $true }}
Remove-Item -LiteralPath $spool -Force
Move-Item -LiteralPath $originalSpool -Destination $spool
Remove-Item -LiteralPath $alternateSpool -Recurse -Force
$recoveryReparseRejected = $false
$originalRecovery = "$recovery.original"
$alternateRecovery = "$recovery.alternate"
Move-Item -LiteralPath $recovery -Destination $originalRecovery
[void](New-Item -ItemType Directory -Path $alternateRecovery)
[void](New-Item -ItemType Junction -Path $recovery -Target $alternateRecovery)
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $recoveryReparseRejected = $true }}
Remove-Item -LiteralPath $recovery -Force
Move-Item -LiteralPath $originalRecovery -Destination $recovery
Remove-Item -LiteralPath $alternateRecovery -Recurse -Force
$watcherRejected = $false
[void](New-Item -ItemType Directory -Path $watcher -Force)
[IO.File]::WriteAllText((Join-Path $watcher 'marker.json'), '{{}}')
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $watcherRejected = $true }}
Remove-Item -LiteralPath $watcher -Recurse -Force
$toolsRejected = $false
[void](New-Item -ItemType Directory -Path $tools -Force)
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $toolsRejected = $true }}
Remove-Item -LiteralPath $tools -Recurse -Force
$identityRejected = $false
$replacement = "$canonical.replacement"
[IO.File]::WriteAllBytes($replacement, [IO.File]::ReadAllBytes($canonical))
Move-Item -LiteralPath $replacement -Destination $canonical -Force
try {{ Assert-WdBridgeSafetyBaseline -Baseline $baseline }}
catch {{ $identityRejected = $true }}
[pscustomobject]@{{
  append_only = $true
  prefix_rejected = $prefixRejected
  spool_add_rejected = $spoolAddRejected
  nested_change_rejected = $nestedChangeRejected
  pending_rejected = $pendingRejected
  pending_directory_rejected = $pendingDirectoryRejected
  nested_reparse_rejected = $nestedReparseRejected
  spool_reparse_rejected = $spoolReparseRejected
  recovery_reparse_rejected = $recoveryReparseRejected
  watcher_rejected = $watcherRejected
  tools_rejected = $toolsRejected
  identity_rejected = $identityRejected
  spool_count = @($baseline.spool).Count
  prefix_length = [int64]$baseline.canonical.prefix_length
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "append_only": True,
        "prefix_rejected": True,
        "spool_add_rejected": True,
        "nested_change_rejected": True,
        "pending_rejected": True,
        "pending_directory_rejected": True,
        "nested_reparse_rejected": True,
        "spool_reparse_rejected": True,
        "recovery_reparse_rejected": True,
        "watcher_rejected": True,
        "tools_rejected": True,
        "identity_rejected": True,
        "spool_count": 6,
        "prefix_length": 2097173,
    }


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
$exactLive = Resolve-LanePinState `
  -Lane $lane `
  -PrimaryRepoRoot 'C:\\Python\\project2' `
  -ActualBranch $lane.branch `
  -ActualHead $lane.head `
  -LiveCount 1 `
  -LiveGenerationAttested:$true
try {{
  [void](Resolve-LanePinState `
    -Lane $lane `
    -PrimaryRepoRoot 'C:\\Python\\project2' `
    -ActualBranch $lane.branch `
    -ActualHead $lane.head `
    -LiveCount 1 `
    -LiveGenerationAttested:$false)
  $unattestedExactRejected = $false
}}
catch {{
  $unattestedExactRejected = $_.Exception.Message -like '*unattested live generation*'
}}
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
  $unattestedLiveDriftRejected = $_.Exception.Message -like '*unattested live generation*'
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
  exact_live = [bool]$exactLive.exact
  unattested_exact_rejected = $unattestedExactRejected
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
        "exact_live": True,
        "unattested_exact_rejected": True,
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


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell junction semantics are unavailable",
)
def test_lane_generation_attestation_rejects_reparse_bundle_before_read(
    tmp_path: Path,
) -> None:
    launcher_path = REBOOT / "start-wd-all.ps1"
    launcher_text = launcher_path.read_text(encoding="utf-8")
    attestation_text = launcher_text.split(
        "function Test-LaneGenerationAttestation", 1
    )[1].split("function Test-ToolsProcessReadiness", 1)[0]
    assert attestation_text.index("Assert-WdFleetPathWithoutReparse") < (
        attestation_text.index("Read-Utf8FleetSnapshot")
    )
    launcher = str(launcher_path).replace("'", "''")
    temp = str(tmp_path).replace("'", "''")
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'fleet launcher parse failed' }}
foreach ($name in @(
    'Assert-WdFleetPathWithoutReparse',
    'Test-NamedCommandLineArgument',
    'Get-NamedCommandLineArgumentValue',
    'Test-LaneGenerationAttestation'
  )) {{
  $functionAst = $ast.Find(
    {{
      param($node)
      $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq $name
    }},
    $true
  )
  if ($null -eq $functionAst) {{ throw "missing fleet function: $name" }}
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}

$root = [IO.Path]::GetFullPath('{temp}')
$store = Join-Path $root 'wd-reboot-bundles'
$handshakeRoot = Join-Path $root 'handshakes'
[void](New-Item -ItemType Directory -Path $store)
[void](New-Item -ItemType Directory -Path $handshakeRoot)
$script:Store = $store
$script:HandshakeRoot = $handshakeRoot
$script:ReadCalls = 0

function Resolve-NormalizedPath {{
  param([Parameter(Mandatory)] [string] $Path)
  if ($Path -ceq 'C:\\Python\\wd-reboot-bundles') {{ return $script:Store }}
  if ($Path -ceq 'C:\\Python\\start-wd-agent.ps1') {{
    return (Join-Path $root 'machine-start-wd-agent.ps1')
  }}
  if ($Path -ceq 'C:\\Python\\wd-reboot-runtime\\handshakes') {{
    return $script:HandshakeRoot
  }}
  return [IO.Path]::GetFullPath($Path).TrimEnd('\\')
}}
function Read-Utf8FleetSnapshot {{
  param([Parameter(Mandatory)] [string] $Path)
  $script:ReadCalls++
  throw "authority read boundary reached: $Path"
}}
function New-PlainBundle {{
  param([Parameter(Mandatory)] [string] $Generation)
  $bundle = Join-Path $store $Generation
  [void](New-Item -ItemType Directory -Path $bundle)
  foreach ($name in @(
      'start-wd-agent.ps1',
      'wd-fleet.json',
      'deployment-manifest.json'
    )) {{
    [IO.File]::WriteAllText(
      (Join-Path $bundle $name),
      'fixture',
      (New-Object Text.UTF8Encoding($false))
    )
  }}
  return $bundle
}}
function New-LaneProcess {{
  param([Parameter(Mandatory)] [string] $Bundle)
  $commandLine = (
    'powershell.exe -NoProfile -File "' +
    (Join-Path $Bundle 'start-wd-agent.ps1') +
    '" -ManifestPath "' +
    (Join-Path $Bundle 'wd-fleet.json') +
    '" -Agent codex-lead-1'
  )
  return [pscustomobject]@{{
    Name = 'powershell.exe'
    CommandLine = $commandLine
    ProcessId = 4242
  }}
}}
function Invoke-AttestationCase {{
  param([Parameter(Mandatory)] [string] $Bundle)
  $before = $script:ReadCalls
  $accepted = Test-LaneGenerationAttestation `
    -Lane ([pscustomobject]@{{ agent = 'codex-lead-1' }}) `
    -Process (New-LaneProcess -Bundle $Bundle)
  return [pscustomobject]@{{
    accepted = [bool]$accepted
    reads = $script:ReadCalls - $before
  }}
}}

$plain = New-PlainBundle -Generation ('b' * 40 -join '')
$outside = Join-Path $root 'outside-root-junction'
[void](New-Item -ItemType Directory -Path $outside)
foreach ($name in @(
    'start-wd-agent.ps1',
    'wd-fleet.json',
    'deployment-manifest.json'
  )) {{
  [IO.File]::WriteAllText(
    (Join-Path $outside $name),
    'fixture',
    (New-Object Text.UTF8Encoding($false))
  )
}}
$rootJunction = Join-Path $store ('a' * 40 -join '')
[void](New-Item -ItemType Junction -Path $rootJunction -Target $outside)

$leafCases = [ordered]@{{}}
$leafNames = @(
  'deployment-manifest.json',
  'wd-fleet.json',
  'start-wd-agent.ps1'
)
for ($i = 0; $i -lt $leafNames.Count; $i++) {{
  $generation = ([char]([int][char]'c' + $i)).ToString() * 40 -join ''
  $bundle = New-PlainBundle -Generation $generation
  $leaf = Join-Path $bundle $leafNames[$i]
  Remove-Item -LiteralPath $leaf -Force
  $leafTarget = Join-Path $root ("outside-leaf-$i")
  [void](New-Item -ItemType Directory -Path $leafTarget)
  [void](New-Item -ItemType Junction -Path $leaf -Target $leafTarget)
  $leafCases[$leafNames[$i]] = Invoke-AttestationCase -Bundle $bundle
}}

$plainResult = Invoke-AttestationCase -Bundle $plain
$rootResult = Invoke-AttestationCase -Bundle $rootJunction
[pscustomobject]@{{
  plain_accepted = [bool]$plainResult.accepted
  plain_reads = [int]$plainResult.reads
  root_accepted = [bool]$rootResult.accepted
  root_reads = [int]$rootResult.reads
  deployment_reads = [int]$leafCases['deployment-manifest.json'].reads
  fleet_reads = [int]$leafCases['wd-fleet.json'].reads
  launcher_reads = [int]$leafCases['start-wd-agent.ps1'].reads
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "plain_accepted": False,
        "plain_reads": 1,
        "root_accepted": False,
        "root_reads": 0,
        "deployment_reads": 0,
        "fleet_reads": 0,
        "launcher_reads": 0,
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
    fleet = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    python_executable = fleet["tools_supervisor"]["python_executable"].replace(
        "'", "''"
    )
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
    'Assert-WdFleetPathWithoutReparse',
    'Resolve-WdNpmNativeApplication',
    'Resolve-ApplicationPath',
    'ConvertTo-UtcDateTimeOffset',
    'Test-WdJsonBooleanTrue',
    'Test-WdJsonIntegerRange',
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
  python_executable = '{python_executable}'
}}
$bundleGeneration = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$processStarted = [DateTimeOffset]::UtcNow.AddSeconds(-1)
$toolsRunId = 'wd-reboot-codex-tools-1-test-101'
$codexExecutable = Resolve-ApplicationPath -Name 'codex.cmd'
$codexHash = (Get-FileHash -LiteralPath $codexExecutable -Algorithm SHA256).Hash
$pythonHash = (Get-FileHash -LiteralPath $config.python_executable -Algorithm SHA256).Hash
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
  codex_command = $codexExecutable
  codex_command_sha256 = $codexHash
  python_executable = $config.python_executable
  python_executable_sha256 = $pythonHash
  target_state_manifested = $true
  target_state_id = 'wd-swarm-target-state-v1'
  run_id = $toolsRunId
  session_id = $toolsRunId
  append_canary = $true
  append_canary_task_id = "wd-append-canary-$toolsRunId"
  append_canary_event_utc = $processStarted.AddMilliseconds(100).ToString('o')
  append_canary_latency_ms = 100
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
$ready.target_state_manifested = 'false'
$ready | ConvertTo-Json | Set-Content -LiteralPath '{readiness_path}' -Encoding UTF8
$stringTargetRejected = -not (Test-ToolsProcessReadiness $current $config $bundleGeneration)
$ready.target_state_manifested = $true
$ready.append_canary = 'false'
$ready | ConvertTo-Json | Set-Content -LiteralPath '{readiness_path}' -Encoding UTF8
$stringCanaryRejected = -not (Test-ToolsProcessReadiness $current $config $bundleGeneration)
$ready.append_canary = $true
$ready.append_canary_latency_ms = '100'
$ready | ConvertTo-Json | Set-Content -LiteralPath '{readiness_path}' -Encoding UTF8
$stringLatencyRejected = -not (Test-ToolsProcessReadiness $current $config $bundleGeneration)
[pscustomobject]@{{
  current = @($state.current).Count
  current_pid = [int](@($state.current)[0].ProcessId)
  starting = @($state.starting).Count
  stale = @($state.stale).Count
  stale_pid = [int](@($state.stale)[0].ProcessId)
  legacy = @($state.legacy).Count
  legacy_pid = [int](@($state.legacy)[0].ProcessId)
  string_target_rejected = $stringTargetRejected
  string_canary_rejected = $stringCanaryRejected
  string_latency_rejected = $stringLatencyRejected
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
        "string_target_rejected": True,
        "string_canary_rejected": True,
        "string_latency_rejected": True,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell 5.1 is unavailable",
)
def test_launcher_waits_for_fresh_tools_state_after_supervisor_replacement() -> None:
    launcher_path = REBOOT / "start-wd-all.ps1"
    launcher_text = launcher_path.read_text(encoding="utf-8")
    supervisor_verify = launcher_text.index("$supervisorVerifyOutput = @(")
    tools_wait = launcher_text.index("$toolsNowState = Wait-WdToolsCurrentProcess")
    grok_resolve = launcher_text.index("Resolving the current Grok model")
    assert supervisor_verify < tools_wait < grok_resolve
    assert "if ($toolsLive.Count -eq 0)" not in launcher_text[
        supervisor_verify:grok_resolve
    ]

    launcher = str(launcher_path).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'launcher parse failed' }}
$functionAst = $ast.Find(
  {{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Wait-WdToolsCurrentProcess'
  }},
  $true
)
if ($null -eq $functionAst) {{ throw 'missing Tools wait helper' }}
. ([scriptblock]::Create($functionAst.Extent.Text))

$script:mode = 'converge'
$script:stateCalls = 0
function Get-ToolsProcessState {{
  param($ToolsConfig, $Generation, $Processes)
  $script:stateCalls += 1
  if ($script:mode -ceq 'converge' -and $script:stateCalls -ge 2) {{
    return [pscustomobject]@{{
      current = @([pscustomobject]@{{ ProcessId = 702 }})
      starting = @()
      stale = @()
      legacy = @()
    }}
  }}
  return [pscustomobject]@{{
    current = @()
    starting = @([pscustomobject]@{{ ProcessId = 701 }})
    stale = @()
    legacy = @()
  }}
}}
$snapshotAction = {{ [pscustomobject]@{{ ProcessId = 700 }} }}
$converged = Wait-WdToolsCurrentProcess `
  -ToolsConfig ([pscustomobject]@{{}}) `
  -Generation 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' `
  -TimeoutSeconds 2 `
  -ProcessSnapshotAction $snapshotAction `
  -PollMilliseconds 0
$convergeCalls = $script:stateCalls

$script:mode = 'timeout'
$script:stateCalls = 0
$timeoutFailed = $false
$timeoutMessage = ''
try {{
  [void](Wait-WdToolsCurrentProcess `
      -ToolsConfig ([pscustomobject]@{{}}) `
      -Generation 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' `
      -TimeoutSeconds 0 `
      -ProcessSnapshotAction $snapshotAction `
      -PollMilliseconds 0)
}} catch {{
  $timeoutFailed = $true
  $timeoutMessage = $_.Exception.Message
}}
[pscustomobject]@{{
  converged = @($converged.current).Count
  converge_calls = $convergeCalls
  timeout_failed = $timeoutFailed
  timeout_calls = $script:stateCalls
  timeout_message = $timeoutMessage
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "converged": 1,
        "converge_calls": 2,
        "timeout_failed": True,
        "timeout_calls": 1,
        "timeout_message": (
            "Tools supervisor did not establish exactly one current-generation "
            "consumer; current/starting/stale/legacy=0/1/0/0"
        ),
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell 5.1 is unavailable",
)
def test_supervisor_and_launcher_share_tools_readiness_authority(
    tmp_path: Path,
) -> None:
    supervisor_path = REBOOT / "wd_supervisor.ps1"
    supervisor_text = supervisor_path.read_text(encoding="utf-8")
    assert "-Validation $toolsValidation" in supervisor_text
    assert "$invalidReadinessWrapperIds" in supervisor_text
    assert "-notin $invalidReadinessWrapperIds" in supervisor_text

    codex = tmp_path / "codex.exe"
    python = tmp_path / "python.exe"
    readiness = tmp_path / "tools-ready.json"
    codex.write_bytes(b"trusted codex bytes\n")
    python.write_bytes(b"trusted python bytes\n")

    supervisor = str(supervisor_path).replace("'", "''")
    codex_quoted = str(codex).replace("'", "''")
    python_quoted = str(python).replace("'", "''")
    readiness_quoted = str(readiness).replace("'", "''")
    result = _run_powershell(
        f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{supervisor}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'supervisor parse failed' }}
function Get-FileHash {{
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$LiteralPath,
    [string]$Algorithm = 'SHA256'
  )
  if ($Algorithm -cne 'SHA256') {{ throw "unsupported hash algorithm: $Algorithm" }}
  $stream = [IO.File]::Open(
    $LiteralPath,
    [IO.FileMode]::Open,
    [IO.FileAccess]::Read,
    [IO.FileShare]::Read
  )
  $sha = [Security.Cryptography.SHA256]::Create()
  try {{
    $hash = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '')
  }} finally {{
    $sha.Dispose()
    $stream.Dispose()
  }}
  return [pscustomobject]@{{ Hash = $hash }}
}}
foreach ($name in @(
    'Test-WdSupervisorJsonBooleanTrue',
    'Test-WdSupervisorJsonIntegerRange',
    'ConvertTo-SupervisorUtc',
    'Test-ToolsWrapperReadiness',
    'Test-ToolsReadinessTargetsProcess'
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
$generation = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$started = [DateTimeOffset]::UtcNow.AddSeconds(-2)
$process = [pscustomobject]@{{
  ProcessId = 404
  CreationDate = $started
}}
$tools = [pscustomobject]@{{
  resume_policy = 'current_worktree'
  expected_branch = 'tools/baseline'
  expected_head = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
  model = 'gpt-5.6-terra'
  reasoning_effort = 'high'
  worktree = 'C:\\Python\\tools-worktree'
}}
$validation = [pscustomobject]@{{
  codex_command = '{codex_quoted}'
  codex_command_sha256 = (
    Get-FileHash -LiteralPath '{codex_quoted}' -Algorithm SHA256
  ).Hash
  python_executable = '{python_quoted}'
  python_executable_sha256 = (
    Get-FileHash -LiteralPath '{python_quoted}' -Algorithm SHA256
  ).Hash
}}
$runId = 'wd-tools-readiness-contract-404'
$ready = [ordered]@{{
  schema = 'wd.tools-consumer-ready.v1'
  generation = $generation
  pid = 404
  process_start_utc = $started.ToString('o')
  config_path = 'C:\\Python\\wd_supervisor_loop.json'
  worktree = $tools.worktree
  branch = 'tools/current'
  head = 'cccccccccccccccccccccccccccccccccccccccc'
  baseline_branch = $tools.expected_branch
  baseline_head = $tools.expected_head
  resume_policy = $tools.resume_policy
  model = $tools.model
  reasoning_effort = $tools.reasoning_effort
  codex_command = $validation.codex_command
  codex_command_sha256 = $validation.codex_command_sha256
  python_executable = $validation.python_executable
  python_executable_sha256 = $validation.python_executable_sha256
  target_state_manifested = $true
  target_state_id = 'wd-swarm-target-state-v1'
  run_id = $runId
  session_id = $runId
  append_canary = $true
  append_canary_task_id = "wd-append-canary-$runId"
  append_canary_event_utc = $started.AddMilliseconds(500).ToString('o')
  append_canary_latency_ms = 100
  ready_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
}}
function Write-Ready {{
  $ready | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath '{readiness_quoted}' -Encoding UTF8
}}
function Get-Disposition {{
  if (Test-ToolsWrapperReadiness `
      -Process $process `
      -Tools $tools `
      -Validation $validation `
      -Generation $generation `
      -ConfigPath 'C:\\Python\\wd_supervisor_loop.json' `
      -ReadinessPath '{readiness_quoted}') {{
    return 'current'
  }}
  if (Test-ToolsReadinessTargetsProcess `
      -Process $process `
      -Generation $generation `
      -ReadinessPath '{readiness_quoted}') {{
    return 'replace'
  }}
  return 'starting'
}}

Write-Ready
$exact = Get-Disposition
$ready.codex_command_sha256 = '0000000000000000000000000000000000000000000000000000000000000000'
Write-Ready
$codexMismatch = Get-Disposition
$ready.codex_command_sha256 = $validation.codex_command_sha256
$ready.append_canary = 'false'
Write-Ready
$typedCanaryMismatch = Get-Disposition
$ready.append_canary = $true
$ready.append_canary_latency_ms = '100'
Write-Ready
$typedLatencyMismatch = Get-Disposition
$ready.append_canary_latency_ms = 100
Write-Ready
[IO.File]::AppendAllText('{codex_quoted}', 'changed')
$physicalMismatch = Get-Disposition
Remove-Item -LiteralPath '{readiness_quoted}' -Force
$missing = Get-Disposition
[pscustomobject]@{{
  exact = $exact
  codex_mismatch = $codexMismatch
  typed_canary_mismatch = $typedCanaryMismatch
  typed_latency_mismatch = $typedLatencyMismatch
  physical_mismatch = $physicalMismatch
  missing = $missing
}} | ConvertTo-Json -Compress
""",
        check=False,
        executable=WINDOWS_POWERSHELL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "exact": "current",
        "codex_mismatch": "replace",
        "typed_canary_mismatch": "replace",
        "typed_latency_mismatch": "replace",
        "physical_mismatch": "replace",
        "missing": "starting",
    }


def test_supervisor_snapshot_is_structured_and_version_independent() -> None:
    snapshot = json.loads(
        (REBOOT / "wd_supervisor_loop.json").read_text(encoding="utf-8")
    )
    assert snapshot["schema"] == "wd.supervisor-loop.v2"
    assert snapshot["recovery_state_root"] == r"C:\Python\wd-reboot-runtime"
    tools = snapshot["tools_consumer"]
    assert snapshot["watchers"]["script_relative"] == (
        r"tools-bootstrap\.agent-bridge\bin\Watch-Bridge.ps1"
    )
    assert snapshot["watchers"]["replacement_conflict_root"] == (
        r"C:\Python\wd-reboot-runtime\watcher-replacement-conflicts"
    )
    assert snapshot["watchers"]["source_repo_root"] == r"C:\Python\project2"
    assert snapshot["watchers"]["git_executable"] == (
        r"C:\Program Files\Git\cmd\git.exe"
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
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.autocrlf", "false"],
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
    git_executable = shutil.which("git.exe") or shutil.which("git")
    assert git_executable is not None
    git_quoted = str(Path(git_executable).resolve()).replace("'", "''")
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
    'Assert-DirectoryPathWithoutReparse',
    'Assert-FilePathWithoutReparse',
    'Resolve-ToolsGitApplication',
    'Invoke-GitText',
    'Assert-TrackedScriptsMatchHead'
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
$script:WdGitExecutable = '{git_quoted}'
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
  -GitExecutable 'C:\\Program Files\\Git\\cmd\\git.exe' `
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
            r"C:\WINDOWS\system32",
            r"C:\Tools\Python313",
            r"C:\Tools\Python313\Scripts",
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
def test_tools_consumer_ignores_path_when_resolving_native_codex(
    tmp_path: Path,
) -> None:
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    attacker_exe = attacker / "codex.exe"
    attacker_cmd = attacker / "codex.cmd"
    attacker_exe.touch()
    attacker_cmd.touch()

    wrapper_path = REBOOT / "start-wd-tools-consumer.ps1"
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "function Resolve-ToolsCodexApplication" in wrapper_text
    assert "Find-ApplicationInPath" not in wrapper_text
    wrapper = str(wrapper_path).replace("'", "''")
    attacker_quoted = str(attacker).replace("'", "''")
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
    'Assert-DirectoryPathWithoutReparse',
    'Assert-FilePathWithoutReparse',
    'Resolve-ToolsCodexApplication'
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
$oldPath = $env:Path
$oldAppData = $env:APPDATA
try {{
  $env:Path = '{attacker_quoted};' + $oldPath
  $env:APPDATA = '{attacker_quoted}'
  Resolve-ToolsCodexApplication
}}
finally {{
  $env:Path = $oldPath
  $env:APPDATA = $oldAppData
}}
"""
    )
    resolved = Path(result.stdout.strip())
    assert resolved != attacker_exe.resolve()
    assert resolved != attacker_cmd.resolve()
    assert resolved.is_file()
    assert resolved.name.casefold() == "codex.exe"
    assert "@openai" in str(resolved).casefold()


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell 5.1 is unavailable",
)
def test_reboot_launchers_ignore_path_and_git_environment_poison(
    tmp_path: Path,
) -> None:
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "git.exe").write_bytes(b"not a trusted Git executable\n")

    fleet = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    git_executable = fleet["git_executable"]
    assert Path(git_executable).is_file()
    expected_head = subprocess.check_output(
        [git_executable, "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    fleet_launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    lane_launcher = str(REBOOT / "start-wd-agent.ps1").replace("'", "''")
    tools_launcher = str(REBOOT / "start-wd-tools-consumer.ps1").replace(
        "'", "''"
    )
    attacker_quoted = str(attacker).replace("'", "''")
    root_quoted = str(ROOT).replace("'", "''")
    git_quoted = git_executable.replace("'", "''")
    result = _run_powershell(
        f"""
$oldPath = $env:Path
$env:Path = '{attacker_quoted};' + $oldPath
$poison = [ordered]@{{
  GIT_DIR = 'Z:\\poison\\repo.git'
  GIT_WORK_TREE = 'Z:\\poison\\worktree'
  GIT_OBJECT_DIRECTORY = 'Z:\\poison\\objects'
  GIT_ALTERNATE_OBJECT_DIRECTORIES = 'Z:\\poison\\alternates'
  GIT_CONFIG_GLOBAL = 'Z:\\poison\\global.gitconfig'
  GIT_CONFIG_SYSTEM = 'Z:\\poison\\system.gitconfig'
  GIT_INDEX_FILE = 'Z:\\poison\\index'
  GIT_REPLACE_REF_BASE = 'refs/poison/'
}}
foreach ($entry in $poison.GetEnumerator()) {{
  [Environment]::SetEnvironmentVariable(
    [string]$entry.Key,
    [string]$entry.Value,
    [EnvironmentVariableTarget]::Process
  )
}}
function Test-PoisonRestored {{
  foreach ($entry in $poison.GetEnumerator()) {{
    if ([Environment]::GetEnvironmentVariable(
        [string]$entry.Key,
        [EnvironmentVariableTarget]::Process
      ) -cne [string]$entry.Value) {{
      return $false
    }}
  }}
  return $true
}}

$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{fleet_launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'fleet launcher parse failed' }}
foreach ($name in @(
    'Assert-WdFleetPathWithoutReparse',
    'Resolve-WdFleetGitApplication',
    'Invoke-CheckedGit'
  )) {{
  $functionAst = $ast.Find(
    {{
      param($node)
      $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }},
    $true
  )
  if ($null -eq $functionAst) {{ throw "missing fleet function: $name" }}
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}
$script:WdGitExecutable = '{git_quoted}'
$fleetHead = Invoke-CheckedGit `
  -Worktree '{root_quoted}' `
  -Arguments @('rev-parse', 'HEAD')
$fleetSuccessRestored = Test-PoisonRestored
try {{
  [void](Invoke-CheckedGit `
      -Worktree '{root_quoted}' `
      -Arguments @('wd-deliberate-invalid-command'))
  $fleetFailureRestored = $false
}}
catch {{
  $fleetFailureRestored = Test-PoisonRestored
}}

$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{lane_launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'lane launcher parse failed' }}
foreach ($name in @(
    'Assert-LanePathWithoutReparse',
    'Resolve-WdLaneGitApplication',
    'Invoke-CheckedGit'
  )) {{
  $functionAst = $ast.Find(
    {{
      param($node)
      $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }},
    $true
  )
  if ($null -eq $functionAst) {{ throw "missing lane function: $name" }}
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}
$script:WdGitExecutable = '{git_quoted}'
$laneHead = Invoke-CheckedGit `
  -Worktree '{root_quoted}' `
  -Arguments @('rev-parse', 'HEAD')
$laneSuccessRestored = Test-PoisonRestored
try {{
  [void](Invoke-CheckedGit `
      -Worktree '{root_quoted}' `
      -Arguments @('wd-deliberate-invalid-command'))
  $laneFailureRestored = $false
}}
catch {{
  $laneFailureRestored = Test-PoisonRestored
}}

$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  '{tools_launcher}', [ref]$tokens, [ref]$errors
)
if ($errors.Count) {{ throw 'Tools launcher parse failed' }}
foreach ($name in @(
    'Test-PathAtOrBelow',
    'Assert-DirectoryPathWithoutReparse',
    'Assert-FilePathWithoutReparse',
    'Resolve-ToolsGitApplication',
    'Invoke-GitText'
  )) {{
  $functionAst = $ast.Find(
    {{
      param($node)
      $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }},
    $true
  )
  if ($null -eq $functionAst) {{ throw "missing Tools function: $name" }}
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}
$script:WdGitExecutable = '{git_quoted}'
$toolsHead = Invoke-GitText `
  -Worktree '{root_quoted}' `
  -ArgumentList @('rev-parse', 'HEAD') `
  -Operation 'poison regression'
$toolsSuccessRestored = Test-PoisonRestored
try {{
  [void](Invoke-GitText `
      -Worktree '{root_quoted}' `
      -ArgumentList @('wd-deliberate-invalid-command') `
      -Operation 'expected failure')
  $toolsFailureRestored = $false
}}
catch {{
  $toolsFailureRestored = Test-PoisonRestored
}}

[pscustomobject]@{{
  fleet_head = $fleetHead
  lane_head = $laneHead
  tools_head = $toolsHead
  fleet_success_restored = $fleetSuccessRestored
  fleet_failure_restored = $fleetFailureRestored
  lane_success_restored = $laneSuccessRestored
  lane_failure_restored = $laneFailureRestored
  tools_success_restored = $toolsSuccessRestored
  tools_failure_restored = $toolsFailureRestored
  path_preserved = $env:Path -ceq ('{attacker_quoted};' + $oldPath)
}} | ConvertTo-Json -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert json.loads(result.stdout) == {
        "fleet_head": expected_head,
        "lane_head": expected_head,
        "tools_head": expected_head,
        "fleet_success_restored": True,
        "fleet_failure_restored": True,
        "lane_success_restored": True,
        "lane_failure_restored": True,
        "tools_success_restored": True,
        "tools_failure_restored": True,
        "path_preserved": True,
    }
    for script_name in (
        "start-wd-all.ps1",
        "start-wd-agent.ps1",
        "start-wd-tools-consumer.ps1",
    ):
        source = (REBOOT / script_name).read_text(encoding="utf-8")
        assert re.search(r"&\s+git(?:\.exe)?\b", source, re.IGNORECASE) is None
        assert "--no-replace-objects" in source


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell 5.1 is unavailable",
)
def test_reboot_native_applications_ignore_path_and_environment_aliases(
    tmp_path: Path,
) -> None:
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    for name in (
        "codex.exe",
        "codex.cmd",
        "claude.exe",
        "claude.cmd",
        "py.exe",
        "python.exe",
        "wt.exe",
    ):
        (attacker / name).write_bytes(b"not a trusted application\n")

    fleet = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    configured_python = fleet["tools_supervisor"]["python_executable"]
    assert Path(configured_python).is_file()

    fleet_launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    lane_launcher = str(REBOOT / "start-wd-agent.ps1").replace("'", "''")
    tools_launcher = str(REBOOT / "start-wd-tools-consumer.ps1").replace(
        "'", "''"
    )
    attacker_quoted = str(attacker).replace("'", "''")
    python_quoted = configured_python.replace("'", "''")
    result = _run_powershell(
        f"""
$oldPath = $env:Path
$oldAppData = $env:APPDATA
$oldLocalAppData = $env:LOCALAPPDATA
try {{
  $env:Path = '{attacker_quoted};' + $oldPath
  $env:APPDATA = '{attacker_quoted}'
  $env:LOCALAPPDATA = '{attacker_quoted}'

  $tokens = $null
  $errors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile(
    '{fleet_launcher}', [ref]$tokens, [ref]$errors
  )
  if ($errors.Count) {{ throw 'fleet launcher parse failed' }}
  foreach ($name in @(
      'Assert-WdFleetPathWithoutReparse',
      'Resolve-WdNpmNativeApplication',
      'Resolve-WdWindowsTerminalApplication'
    )) {{
    $functionAst = $ast.Find(
      {{
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
          $node.Name -eq $name
      }},
      $true
    )
    if ($null -eq $functionAst) {{ throw "missing fleet function: $name" }}
    . ([scriptblock]::Create($functionAst.Extent.Text))
  }}
  $fleetCodex = Resolve-WdNpmNativeApplication -Name codex.cmd
  $fleetClaude = Resolve-WdNpmNativeApplication -Name claude.cmd
  $terminal = Resolve-WdWindowsTerminalApplication

  $tokens = $null
  $errors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile(
    '{lane_launcher}', [ref]$tokens, [ref]$errors
  )
  if ($errors.Count) {{ throw 'lane launcher parse failed' }}
  foreach ($name in @(
      'Assert-LanePathWithoutReparse',
      'Resolve-WdLaneCliApplication'
    )) {{
    $functionAst = $ast.Find(
      {{
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
          $node.Name -eq $name
      }},
      $true
    )
    if ($null -eq $functionAst) {{ throw "missing lane function: $name" }}
    . ([scriptblock]::Create($functionAst.Extent.Text))
  }}
  $laneCodex = Resolve-WdLaneCliApplication -Name codex.cmd
  $laneClaude = Resolve-WdLaneCliApplication -Name claude.cmd

  $tokens = $null
  $errors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile(
    '{tools_launcher}', [ref]$tokens, [ref]$errors
  )
  if ($errors.Count) {{ throw 'Tools launcher parse failed' }}
  foreach ($name in @(
      'Test-PathAtOrBelow',
      'Assert-DirectoryPathWithoutReparse',
      'Assert-FilePathWithoutReparse',
      'Resolve-ToolsPythonExecutable',
      'Resolve-ToolsCodexApplication'
    )) {{
    $functionAst = $ast.Find(
      {{
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
          $node.Name -eq $name
      }},
      $true
    )
    if ($null -eq $functionAst) {{ throw "missing Tools function: $name" }}
    . ([scriptblock]::Create($functionAst.Extent.Text))
  }}
  $toolsPython = Resolve-ToolsPythonExecutable `
    -ConfiguredPath '{python_quoted}' `
    -WindowsAppsRoots @(
      'C:\\Program Files\\WindowsApps',
      'C:\\Users\\janik\\AppData\\Local\\Microsoft\\WindowsApps'
    )
  $toolsCodex = Resolve-ToolsCodexApplication
  try {{
    [void](Resolve-ToolsPythonExecutable `
        -ConfiguredPath (Join-Path '{attacker_quoted}' 'python.exe') `
        -WindowsAppsRoots @())
    $attackerPythonRejected = $false
  }}
  catch {{
    $attackerPythonRejected = $true
  }}

  [pscustomobject]@{{
    fleet_codex = $fleetCodex
    fleet_claude = $fleetClaude
    lane_codex = $laneCodex
    lane_claude = $laneClaude
    tools_codex = $toolsCodex
    tools_python = $toolsPython
    terminal = $terminal
    attacker_python_rejected = $attackerPythonRejected
  }} | ConvertTo-Json -Compress
}}
finally {{
  $env:Path = $oldPath
  $env:APPDATA = $oldAppData
  $env:LOCALAPPDATA = $oldLocalAppData
}}
""",
        check=False,
        executable=WINDOWS_POWERSHELL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    attacker_resolved = attacker.resolve()
    for key in (
        "fleet_codex",
        "fleet_claude",
        "lane_codex",
        "lane_claude",
        "tools_codex",
        "tools_python",
        "terminal",
    ):
        resolved = Path(payload[key]).resolve()
        assert resolved.is_file()
        assert attacker_resolved not in resolved.parents
    assert Path(payload["fleet_codex"]).resolve() == Path(
        payload["lane_codex"]
    ).resolve()
    assert Path(payload["fleet_claude"]).resolve() == Path(
        payload["lane_claude"]
    ).resolve()
    assert Path(payload["tools_codex"]).resolve() == Path(
        payload["fleet_codex"]
    ).resolve()
    assert Path(payload["tools_python"]).resolve() == Path(
        configured_python
    ).resolve()
    terminal = Path(payload["terminal"]).resolve()
    assert terminal.name.casefold() == "wt.exe"
    assert "program files\\windowsapps" in str(terminal).casefold()
    assert payload["attacker_python_rejected"] is True


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
    assert "[switch] $Apply" in launcher
    assert "function Enter-WdFleetRebootMutex" in launcher
    assert "catch [Threading.AbandonedMutexException]" in launcher
    assert "$mutexAcquired = Enter-WdFleetRebootMutex -Mutex $mutex" in launcher
    assert launcher.index("$launcherMode = Resolve-WdLauncherMode") < launcher.index(
        "$bundleManifestAnchor = ''"
    )
    bundle_mode_gate = launcher.index("Assert-WdLauncherBundleMode `")
    assert bundle_mode_gate < launcher.index("$bundleGeneration = if")
    assert "defaulting to byte-inert DryRun" in launcher
    assert "Apply and DryRun are mutually exclusive" in launcher
    assert "codex update (once)" in launcher
    assert "claude update (once)" in launcher
    dry_return = launcher.index("DRY RUN: no updates")
    console_containment_apply = launcher.index(
        "Applying scheduled-task console containment"
    )
    supervisor_registration = launcher.index(
        "Registering the exact hidden WD-Supervisor action"
    )
    first_update = launcher.index("Updating Codex CLI once")
    assert dry_return < console_containment_apply < supervisor_registration < first_update
    assert "WD_CLI_VERSIONS_CURRENT.json" in launcher
    assert "function Resolve-ApplicationPath" in launcher
    assert "--no-replace-objects" in launcher
    assert "function Resolve-WdNpmNativeApplication" in launcher
    assert "Appx\\Get-AppxPackage" in launcher
    assert "SignatureKind -ceq 'Store'" in launcher
    assert "Microsoft\\WindowsApps\\wt.exe" not in launcher
    assert "-PreferredPath" not in launcher
    assert "Start-Process -FilePath $wtPath" in launcher
    assert "$expectedSupervisorExecutable," in launcher
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
    supervisor_preflight = launcher.index("$supervisorPreflightOutput = @(")
    whole_fleet_passed = launcher.index("whole-fleet preflight passed")
    supervisor_apply = launcher.index("$supervisorApplyOutput = &")
    supervisor_verify = launcher.index("$supervisorVerifyOutput = @(")
    cli_receipt = launcher.index(
        "Move-Item -LiteralPath $cliVersionTemporary"
    )
    grok_resolve = launcher.index("Resolving the current Grok model")
    tools_wait = launcher.index(
        "Waiting for the supervisor-managed Tools consumer"
    )
    lane_launch = launcher.index("Start-Process -FilePath $wtPath")
    final_lane_verify = launcher.index("$finalProcesses = Get-AllProcessSnapshots")
    final_supervisor_verify = launcher.index("$finalSupervisorOutput = @(")
    final_bridge_gate = launcher.index(
        "Assert-WdBridgeSafetyBaseline -Baseline $bridgeSafetyBaseline",
        final_supervisor_verify,
    )
    task_activation = launcher.index(
        "$supervisorActivationResult = Enable-WdSupervisorTaskAfterRestore"
    )
    completion = launcher.index("Fleet restore complete; run_id=")
    assert supervisor_preflight < whole_fleet_passed < dry_return
    assert dry_return < first_update < cli_receipt
    assert cli_receipt < supervisor_apply < supervisor_verify
    assert supervisor_verify < tools_wait < grok_resolve < lane_launch
    assert lane_launch < final_lane_verify < final_supervisor_verify
    assert final_supervisor_verify < final_bridge_gate < task_activation < completion
    assert "supervisor report-only preflight returned a conflict" in launcher
    assert "verified legacy visible action" in launcher
    assert "supervisor post-Apply report returned a conflict" in launcher
    assert "Tools consumer validation does not match fleet pins" in launcher
    assert "codex-tools-1 is headless and live" in launcher
    assert "-Generation $bundleGeneration" in launcher
    assert "Get-ToolsProcessState" in launcher
    assert "ask WD-Supervisor to replace stale generation" in launcher
    assert "Reconciling five bridge watchers" in launcher
    assert "supervisor reconciliation conflict" in supervisor
    containment_body = supervisor[
        supervisor.index("function Invoke-TaskContainment") : supervisor.index(
            "$configFull =", supervisor.index("function Invoke-TaskContainment")
        )
    ]
    assert containment_body.index("Disable-ScheduledTask") < containment_body.index(
        "Stop-ScheduledTask"
    ) < containment_body.index("$verifiedTask = Get-OptionalScheduledTask")
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
    assert len(bootstrap_gates) == 9
    session_call = tools_wrapper.index(". $sessionScript `")
    target_event = tools_wrapper.index("-Status target_state_manifested `")
    assert bootstrap_gates[0] < bootstrap_gates[1] < session_call
    canary_event = tools_wrapper.index("-Status append_canary `")
    assert session_call < bootstrap_gates[2] < target_event < bootstrap_gates[3]
    assert bootstrap_gates[3] < canary_event < bootstrap_gates[4]
    initial_call = tools_wrapper.index("$initialOutput = @(& $consumerScript")
    assert bootstrap_gates[5] < initial_call < bootstrap_gates[6]
    wake_call = tools_wrapper.index("$wakeOutput = @(& $consumerScript @wakeArguments)")
    assert bootstrap_gates[7] < wake_call < bootstrap_gates[8]
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
    assert "[switch] $Auto" in text
    assert "Auto cannot be combined with Apply or DryRun" in text
    assert text.index("$dryRunParameters['DryRun'] = $true") < text.index(
        "$applyParameters['Apply'] = $true"
    )
    assert "'Set-WdTaskConsoleContainment.ps1'," in text
    assert "Name = 'Set-WdTaskConsoleContainment.ps1'" in text
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


def test_scheduled_task_console_containment_is_dry_by_default_and_fail_closed() -> None:
    text = (REBOOT / "Set-WdTaskConsoleContainment.ps1").read_text(
        encoding="utf-8"
    )
    assert "param([switch] $Apply)" in text
    assert "wd.task-console-containment.v1" in text
    assert "4CD4FBED01E3EAD1C999493212F7499137C0937F597BDD5172C0EFEEDA3F509F" in text
    assert "if (-not $Apply)" in text
    assert text.index("if (-not $Apply)") < text.index(
        "Disable-ScheduledTask"
    ) < text.index("Stop-ScheduledTask")
    assert "Enable-ScheduledTask" not in text
    assert "Set-ScheduledTask `\n      -TaskPath" in text
    assert "[bool]$after.Settings.Enabled -ne $enabledBefore" in text
    assert "scheduled-task console containment requires an Administrator PowerShell" in text


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
    assert (
        r"C:\Python\start-wd-all.ps1 -Auto'" in text
    )


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None,
    reason="Windows PowerShell is unavailable",
)
def test_generated_wrappers_forward_switches_and_named_values(tmp_path: Path) -> None:
    target = tmp_path / "target.ps1"
    target.write_text(
        """
[CmdletBinding()]
param(
  [string] $RunId = '',
  [ValidateRange(10, 300)] [int] $HandshakeTimeoutSeconds = 90,
  [switch] $SkipCliUpdate,
  [switch] $Apply,
  [switch] $DryRun
)
$global:LASTEXITCODE = 7
[pscustomobject]@{
  run_id = $RunId
  timeout = $HandshakeTimeoutSeconds
  skip_update = [bool]$SkipCliUpdate
  apply = [bool]$Apply
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
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $env:SystemRoot (
  'System32\\WindowsPowerShell\\v1.0\\Modules\\' +
  'Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1'
)) -Force -ErrorAction Stop
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
[IO.File]::WriteAllBytes(
  '{fleet_wrapper}',
  $utf8.GetBytes([string]$fleetText)
)
$agentText = New-ForwardingWrapper `
  -Target '{agent_target_quoted}' `
  -ExpectedHash (Get-FileHash -LiteralPath '{agent_target_quoted}' -Algorithm SHA256).Hash `
  -ExpectedManifestHash $manifestHash `
  -WrapperKind agent `
  -FixedAgent fable-5
[IO.File]::WriteAllBytes(
  '{agent_wrapper}',
  $utf8.GetBytes([string]$agentText)
)
$toolsText = New-ForwardingWrapper `
  -Target '{tools_target_quoted}' `
  -ExpectedHash (Get-FileHash -LiteralPath '{tools_target_quoted}' -Algorithm SHA256).Hash `
  -ExpectedManifestHash $manifestHash `
  -WrapperKind tools
[IO.File]::WriteAllBytes(
  '{tools_wrapper}',
  $utf8.GetBytes([string]$toolsText)
)
foreach ($path in @('{fleet_wrapper}', '{agent_wrapper}', '{tools_wrapper}')) {{
  if ((Get-Item -LiteralPath $path).Length -le 0) {{
    throw "generated wrapper is empty: $path"
  }}
}}
""",
        executable=WINDOWS_POWERSHELL,
    )
    assert Path(fleet_wrapper.replace("''", "'")).stat().st_size > 0
    assert Path(agent_wrapper.replace("''", "'")).stat().st_size > 0
    assert Path(tools_wrapper.replace("''", "'")).stat().st_size > 0
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $env:SystemRoot (
  'System32\\WindowsPowerShell\\v1.0\\Modules\\' +
  'Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1'
)) -Force -ErrorAction Stop
$fleetDry = @(& '{fleet_wrapper}' `
  -RunId reboot-123 `
  -HandshakeTimeoutSeconds 42 `
  -SkipCliUpdate `
  -DryRun) | Where-Object {{ $_.PSObject.Properties['run_id'] }} |
    Select-Object -Last 1
$fleetApply = @(& '{fleet_wrapper}' `
  -RunId reboot-apply-123 `
  -HandshakeTimeoutSeconds 43 `
  -Apply) | Where-Object {{ $_.PSObject.Properties['run_id'] }} |
    Select-Object -Last 1
$agent = @(& '{agent_wrapper}' -RunId lane-456 -DryRun) |
  Where-Object {{ $_.PSObject.Properties['agent'] }} | Select-Object -Last 1
$tools = @(& '{tools_wrapper}' `
  -ConfigPath C:\\Python\\wd_supervisor_loop.json `
  -Generation {TOOLS_HEAD} `
  -ValidateOnly) | Where-Object {{ $_.PSObject.Properties['generation'] }} |
    Select-Object -Last 1
[pscustomobject]@{{
  fleet_dry_run_id = [string]$fleetDry.run_id
  fleet_dry_timeout = [int]$fleetDry.timeout
  fleet_dry_skip_update = [bool]$fleetDry.skip_update
  fleet_dry_apply = [bool]$fleetDry.apply
  fleet_dry_dry_run = [bool]$fleetDry.dry_run
  fleet_dry_native_status = [int]$fleetDry.handled_native_status
  fleet_apply_run_id = [string]$fleetApply.run_id
  fleet_apply_timeout = [int]$fleetApply.timeout
  fleet_apply_skip_update = [bool]$fleetApply.skip_update
  fleet_apply_apply = [bool]$fleetApply.apply
  fleet_apply_dry_run = [bool]$fleetApply.dry_run
  fleet_apply_native_status = [int]$fleetApply.handled_native_status
  agent_name = [string]$agent.agent
  agent_run_id = [string]$agent.run_id
  agent_dry_run = [bool]$agent.dry_run
  tools_config_path = [string]$tools.config_path
  tools_generation = [string]$tools.generation
  tools_validate_only = [bool]$tools.validate_only
}} |
  ConvertTo-Json -Depth 6 -Compress
""",
        executable=WINDOWS_POWERSHELL,
    )
    record = json.loads(result.stdout)
    assert record == {
        "fleet_dry_run_id": "reboot-123",
        "fleet_dry_timeout": 42,
        "fleet_dry_skip_update": True,
        "fleet_dry_apply": False,
        "fleet_dry_dry_run": True,
        "fleet_dry_native_status": 7,
        "fleet_apply_run_id": "reboot-apply-123",
        "fleet_apply_timeout": 43,
        "fleet_apply_skip_update": False,
        "fleet_apply_apply": True,
        "fleet_apply_dry_run": False,
        "fleet_apply_native_status": 7,
        "agent_name": "fable-5",
        "agent_run_id": "lane-456",
        "agent_dry_run": True,
        "tools_config_path": r"C:\Python\wd_supervisor_loop.json",
        "tools_generation": TOOLS_HEAD,
        "tools_validate_only": True,
    }


def test_root_runbook_names_current_one_line_and_authority_hold() -> None:
    text = (ROOT / "BOOT_AFTER_REBOOT.md").read_text(encoding="utf-8")
    assert (
        "powershell -NoProfile -ExecutionPolicy Bypass "
        "-File C:\\Python\\start-wd-all.ps1 -Apply"
    ) in text
    assert "defaults to byte-inert DryRun" in text
    assert "hash-bound old reboot bundle" in text
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

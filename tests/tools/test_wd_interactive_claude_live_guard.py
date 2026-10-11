"""An interactive Claude launch refuses a live same-lane session before bridge bootstrap.

Codex P1 4241232081 on PR1848 (RCO1-confirmed): the availability guard ran only for managed lanes and native
Lead, so a default resume or an operator -FreshSession of an interactive Claude lane could bootstrap a second
session with the same identity and worktree next to a live one. Both tests execute the real pre-bootstrap block
of start-wd-agent.ps1; the second one runs it with the real guard over a stubbed process snapshot.
"""
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

SOURCE = REBOOT / "start-wd-agent.ps1"
BLOCK_START = "if ($turnMode -ceq 'managed') {"
BLOCK_END = "if ($launchTurnMode -ceq 'interactive' -and [Console]::IsInputRedirected) {"


def _guard_block(source_path: Path) -> str:
    """The statements between the DryRun return and the interactive-console check, from the real script."""
    text = source_path.read_text(encoding="utf-8")
    start = text.index(BLOCK_START, text.index("if ($DryRun) {"))
    return text[start:text.index(BLOCK_END, start)]


def test_the_guard_runs_after_the_dry_run_return_and_before_bridge_bootstrap():
    text = SOURCE.read_text(encoding="utf-8")
    dry_return = text.index("return [pscustomobject]", text.index("if ($DryRun) {"))
    guard = text.index("if ($cliName -ieq 'claude.cmd' -and $launchTurnMode -ceq 'interactive') {\n"
                       "  # Codex P1 4241232081", dry_return)
    assert dry_return < guard < text.index(BLOCK_END, dry_return)
    assert guard < text.index("$session = . $starter @sessionArgs", dry_return)
    assert guard < text.index("$env:AGENT_BRIDGE_AGENT = ", dry_return)
    assert guard < text.rindex("$launchArguments = @()")


CASES = {
    # case: (turnMode, launchTurnMode, cliName, nativeLead, FreshSession)
    "interactive_claude_default": ("interactive", "interactive", "claude.cmd", False, False),
    "interactive_claude_fresh": ("interactive", "interactive", "claude.cmd", False, True),
    "interactive_claude_upper_case_cli": ("interactive", "interactive", "CLAUDE.CMD", False, False),
    "managed_claude": ("managed", "managed", "claude.cmd", False, False),
    "native_lead": ("interactive", "interactive", "codex.cmd", True, False),
    "interactive_codex_not_lead": ("interactive", "interactive", "codex.cmd", False, False),
}
EXPECTED_CHECKS = {
    "interactive_claude_default": 1,
    "interactive_claude_fresh": 1,
    "interactive_claude_upper_case_cli": 1,
    "managed_claude": 1,
    "native_lead": 1,
    "interactive_codex_not_lead": 0,
}


def _checks(source_path: Path, ps: str, tmp_path: Path) -> dict:
    block = _guard_block(source_path)
    script = f"""$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Assert-WdLaneLaunchAvailable {{ param($Lane,$KnownLanes,$ExternalSessions,[switch]$AllowUnpinnedParser) $script:checks++ }}
function Assert-LanePathWithoutReparse {{ param($Path,$TrustedRoot,$ExpectedType) }}
function Get-WdNativeLeadResumeState {{ param($Worktree,$RuntimeRoot) [pscustomobject]@{{thread_id='native'}} }}
function Invoke-Block([string] $Turn, [string] $LaunchTurn, [string] $Cli, [bool] $Native, [bool] $Fresh) {{
  $turnMode=$Turn; $launchTurnMode=$LaunchTurn; $cliName=$Cli; $nativeLead=$Native; $FreshSession=$Fresh
  $lane=[pscustomobject]@{{agent='claude-rco-2'}}; $manifest=[pscustomobject]@{{lanes=@($lane)}}
  $externalSessions=@(); $sourceTreeMode=$false; $DryRun=$false; $worktree={q(tmp_path)}
  $runtimeRoot={q(tmp_path)}; $laneTrustedDrive={q(tmp_path)}; $nativeLeadLease=$null
  $script:checks=0
  try {{
{block}
  }} finally {{ if ($null -ne $nativeLeadLease) {{ $nativeLeadLease.Dispose() }} }}
  return $script:checks
}}
$r=[ordered]@{{}}
"""
    for case, (turn, launch_turn, cli, native, fresh) in CASES.items():
        script += (f"$r['{case}']=Invoke-Block '{turn}' '{launch_turn}' '{cli}' "
                   f"${str(native).lower()} ${str(fresh).lower()}\n")
    script += "$r | ConvertTo-Json -Compress\n"
    return json.loads(_run_powershell(script, executable=ps).stdout)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell launcher")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_interactive_claude_launches_run_the_availability_guard_and_other_lanes_are_unchanged(tmp_path, ps):
    assert _checks(SOURCE, ps, tmp_path) == EXPECTED_CHECKS


LAUNCHER = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\\Python\\start-wd-agent.ps1 -Agent {agent}"
SNAPSHOTS = {
    # case: rows added to a baseline of Lead and RCO1 launchers with their native CLIs
    "other_lanes_only": [],
    "own_launcher_pid": ["self"],
    "same_lane_launcher": [("ps", 300, 1, LAUNCHER.format(agent="claude-rco-2"))],
    "same_lane_legacy_wrapper": [("ps", 300, 1, "powershell.exe -NoProfile -File C:\\Python\\start-wd-claude-rco2.ps1")],
    "same_lane_native_under_its_launcher": [("ps", 300, 1, LAUNCHER.format(agent="claude-rco-2")),
                                            ("claude", 301, 300, "claude.exe --resume x")],
    "unattributed_native": [("claude", 400, 1, "claude.exe")],
}
EXPECTED_SNAPSHOTS = {
    "other_lanes_only": "bootstrap_reached",
    "own_launcher_pid": "bootstrap_reached",
    "same_lane_launcher": "already has a live launcher",
    "same_lane_legacy_wrapper": "already has a live launcher",
    "same_lane_native_under_its_launcher": "already has a live launcher",
    "unattributed_native": "has no verified launcher ancestry",
}


def _row(kind, pid, parent, command, minute):
    name = "powershell.exe" if kind == "ps" else "claude.exe" if kind == "claude" else "codex.exe"
    return (f"[pscustomobject]@{{ProcessId={pid};ParentProcessId={parent};Name='{name}';"
            f"CreationDate=[datetime]'2026-10-11T10:{minute:02d}:00Z';ExecutablePath='C:\\x\\{name}';"
            f"CommandLine={q(command)}}}")


def _snapshots(source_path: Path, ps: str, tmp_path: Path) -> dict:
    block = _guard_block(source_path)
    script = load(source_path, "Assert-WdLaneLaunchAvailable") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Assert-LanePathWithoutReparse {{ param($Path,$TrustedRoot,$ExpectedType) }}
function Get-CimInstance {{ $global:rows }}
$lead=[pscustomobject]@{{agent='codex-lead-1';legacy_process_markers=@('start-wd-codex-lead.ps1')}}
$rco1=[pscustomobject]@{{agent='claude-rco-1';legacy_process_markers=@('start-wd-claude-rco.ps1')}}
$rco2=[pscustomobject]@{{agent='claude-rco-2';legacy_process_markers=@('start-wd-claude-rco2.ps1')}}
$baseline=@(
 {_row("ps", 100, 1, LAUNCHER.format(agent="codex-lead-1"), 0)},
 {_row("codex", 101, 100, "codex.exe resume", 1)},
 {_row("ps", 200, 1, LAUNCHER.format(agent="claude-rco-1"), 0)},
 {_row("claude", 201, 200, "claude.exe --resume y", 1)}
)
function Invoke-Block([object[]] $Extra) {{
  $global:rows=@($baseline) + @($Extra)
  $turnMode='interactive'; $launchTurnMode='interactive'; $cliName='claude.cmd'; $nativeLead=$false
  $FreshSession=$true; $lane=$rco2; $manifest=[pscustomobject]@{{lanes=@($lead,$rco1,$rco2)}}
  $externalSessions=@(); $sourceTreeMode=$true; $DryRun=$true
  try {{
{block}
    return 'bootstrap_reached'
  }} catch {{ return $_.Exception.Message }}
}}
$r=[ordered]@{{}}
"""
    for case, extra in SNAPSHOTS.items():
        rows = []
        for item in extra:
            if item == "self":
                rows.append("[pscustomobject]@{ProcessId=$PID;ParentProcessId=1;Name='powershell.exe';"
                            "CreationDate=[datetime]'2026-10-11T10:00:00Z';ExecutablePath='C:\\x\\powershell.exe';"
                            f"CommandLine={q(LAUNCHER.format(agent='claude-rco-2'))}}}")
            else:
                rows.append(_row(*item, 2 if item[0] == "ps" else 3))
        script += f"$r['{case}']=Invoke-Block @({','.join(rows)})\n"
    script += "$r | ConvertTo-Json -Compress\n"
    observed = json.loads(_run_powershell(script, executable=ps).stdout)
    return {case: value if value == "bootstrap_reached" else next(
        (label for label in EXPECTED_SNAPSHOTS.values() if label in value), value) for case, value in observed.items()}


@pytest.mark.skipif(os.name != "nt", reason="real Windows argv parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_a_fresh_or_resumed_claude_lane_refuses_a_live_same_lane_session_before_bootstrap(tmp_path, ps):
    assert _snapshots(SOURCE, ps, tmp_path) == EXPECTED_SNAPSHOTS

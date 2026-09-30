"""wd_supervisor.ps1 -BridgeWorkersOnly: verify-only merge-driver containment (Lead 2a1244a8; AUTHORED, NOT RUN).

The containment functions are AST-extracted from the real supervisor and run against fake Task Scheduler cmdlets:
no task, process, queue or supervisor run. Under -BridgeWorkersOnly every refusal happens with ZERO Disable or Stop
calls and BEFORE the watcher and Tools reconcile; the ordinary mode is unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, POWERSHELL, REBOOT, _run_powershell

SUPERVISOR = REBOOT / "wd_supervisor.ps1"
FUNCTIONS = ("Get-RequiredText", "Get-OptionalScheduledTask", "Test-LegacyDriverProvenNonApply", "Invoke-TaskContainment",
             "Invoke-WdReconciliationUnderDriverHold")
# case: (standing task, legacy task); a task is None (missing), "unreadable" or (enabled, state).
CASES = {
    "contained": ((False, "Ready"), (False, "Ready")),
    "standing_enabled": ((True, "Ready"), (False, "Ready")),
    "standing_running": ((False, "Running"), (False, "Ready")),
    "standing_missing": (None, (False, "Ready")),
    "standing_unreadable": ("unreadable", (False, "Ready")),
    "legacy_enabled": ((False, "Ready"), (True, "Ready")),
    "legacy_running": ((False, "Ready"), (False, "Running")),
    "legacy_missing": ((False, "Ready"), None),
    "legacy_unreadable": ((False, "Ready"), "unreadable"),
}


def _task(value) -> str:
    if value is None:
        return "$null"
    if value == "unreadable":
        return "'unreadable'"
    enabled, state = value
    return ("[pscustomobject]@{Settings=[pscustomobject]@{Enabled=$" + str(enabled).lower() + "};State='" + state +
            "';Actions=@([pscustomobject]@{Execute='cmd.exe';Arguments='/c legacy'})}")


def _run(ps: str, case: str, bridge_workers_only: bool) -> dict:
    standing, legacy = CASES[case]
    names = ", ".join("'" + name + "'" for name in FUNCTIONS)
    quoted = str(SUPERVISOR).replace("'", "''")
    mode = "$true" if bridge_workers_only else "$false"
    script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Apply = $true
$BridgeWorkersOnly = {mode}
$actions = New-Object 'System.Collections.Generic.List[string]'
$script:mutations = New-Object 'System.Collections.Generic.List[string]'
$script:reconciled = 0
$ast = [Management.Automation.Language.Parser]::ParseFile('{quoted}', [ref]$null, [ref]$null)
foreach ($name in @({names})) {{
    $definition = $ast.Find({{ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name }}, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}}
$tasks = @{{ 'WD-Standing' = {_task(standing)}; 'WD-Legacy' = {_task(legacy)} }}
function Get-ScheduledTask {{
    [CmdletBinding()] param([string] $TaskName)
    $task = $tasks[$TaskName]
    if ($null -eq $task) {{ Write-Error -Message 'task not found' -Category ObjectNotFound -ErrorAction Stop }}
    if ($task -is [string]) {{ throw 'access is denied' }}
    return $task
}}
function Disable-ScheduledTask {{ [CmdletBinding()] param([string] $TaskName) $script:mutations.Add('disable ' + $TaskName) }}
function Stop-ScheduledTask {{ [CmdletBinding()] param([string] $TaskName) $script:mutations.Add('stop ' + $TaskName) }}
$driver = [pscustomobject]@{{ standing_task = 'WD-Standing'; legacy_task = 'WD-Legacy'; legacy_script_path = 'legacy.ps1' }}
$outcome = 'reconciled'
try {{ Invoke-WdReconciliationUnderDriverHold -Driver $driver -ReconciliationAction {{ $script:reconciled++ }} }}
catch {{ $outcome = 'refused: ' + $_.Exception.Message }}
[pscustomobject]@{{ outcome = $outcome; mutations = @($script:mutations); reconciled = $script:reconciled; actions = @($actions) }} |
    ConvertTo-Json -Depth 4 -Compress
"""
    return json.loads(_run_powershell(script, executable=ps).stdout)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", sorted(CASES))
def test_bridge_workers_only_never_mutates_a_task_and_refuses_before_any_reconcile(ps, case):
    result = _run(ps, case, True)
    assert result["mutations"] == []  # absolutely no Disable or Stop, in every state
    if case == "contained":
        assert (result["outcome"], result["reconciled"]) == ("reconciled", 1)
        assert sum(action.startswith("HOLD verified") for action in result["actions"]) == 2
    else:
        assert result["outcome"].startswith("refused: ") and result["reconciled"] == 0, result


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_ordinary_supervisor_is_unchanged(ps):
    enabled = _run(ps, "standing_enabled", False)
    assert enabled["mutations"] == ["disable WD-Standing"] and enabled["reconciled"] == 0  # the fake stays enabled
    missing = _run(ps, "legacy_missing", False)
    assert (missing["outcome"], missing["reconciled"], missing["mutations"]) == ("reconciled", 1, [])

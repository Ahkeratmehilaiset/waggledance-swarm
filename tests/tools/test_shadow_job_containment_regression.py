"""Strict fake of the real stop-confirmation block; no native job is created.

This reproduces the premature accounting-zero acknowledgement, NOT the cause of
the observed Python survivor.  A native escape/termination diagnosis is separate.
"""

import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell


def _confirmation_block():
    source = (REBOOT / "Start-WdMindShadowJob.ps1").read_text(encoding="utf-8")
    first = source.index("            $confirmUntil = ")
    last = source.index("            $after = Get-WdShadowDescendants", first)
    block = source[first:last]
    assert block.count("$confirmUntil = ") == 1
    assert "$managed.ActiveProcesses" in block
    assert "$managed.Wait(0)" in block
    for forbidden in ("Add-Type", "DllImport", "Process]::", "Start-Process",
                      "Get-Cim", "taskkill", "$env:", "Get-WdShadowDescendants"):
        assert forbidden not in block, forbidden
    return block


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("root_signals_on", [0, 4])
def test_zero_accounting_cannot_acknowledge_an_unsignalled_root(ps, root_signals_on):
    # The real trace contained ActiveProcesses=0 followed by Wait(0)=false.
    # Time is virtual: every production sleep advances the same fake clock.
    # Case 0 never signals; case 4 signals on the fourth handle observation.
    prelude = r'''
$ErrorActionPreference = 'Stop'
$script:fakeMs = [long]0
$script:waitCalls = 0
$script:rootSignalsOn = ROOT_SIGNALS_ON
function Start-Sleep {
    param([int] $Milliseconds)
    if ($Milliseconds -le 0) { throw 'invalid virtual sleep' }
    $script:fakeMs += $Milliseconds
    if ($script:fakeMs -gt 31000) { throw 'unbounded confirmation loop' }
}
function Add-WdShadowUnknown {
    param([System.Collections.IList] $Unknown, [string] $Field, [string] $Reason)
    [void]$Unknown.Add([ordered]@{ field = $Field; reason = $Reason })
}
$clock = [pscustomobject]@{}
$clock | Add-Member ScriptProperty ElapsedMilliseconds { $script:fakeMs }
$clock | Add-Member ScriptProperty Elapsed { [TimeSpan]::FromMilliseconds($script:fakeMs) }
$managed = [pscustomobject]@{ ActiveProcesses = 0 }
$managed | Add-Member ScriptMethod Wait {
    param([int] $Milliseconds)
    if ($Milliseconds -ne 0) { throw 'expected nonblocking handle observation' }
    $script:waitCalls++
    return $script:rootSignalsOn -gt 0 -and $script:waitCalls -ge $script:rootSignalsOn
}
$unknown = New-Object System.Collections.ArrayList
$StopConfirmationWaitSeconds = 30
$stopConfirmed = $false
$rootExitAt = $null
$drainAt = $null
'''.replace("ROOT_SIGNALS_ON", str(root_signals_on))
    report = r'''
[ordered]@{
    confirmed = [bool]$stopConfirmed
    root_observed_exited = ($null -ne $rootExitAt)
    wait_calls = $script:waitCalls
    elapsed_ms = $script:fakeMs
    unknown = @($unknown)
} | ConvertTo-Json -Depth 5 -Compress
'''
    completed = _run_powershell(prelude + _confirmation_block() + report,
                                executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    if root_signals_on == 0:
        assert observed["confirmed"] is False, observed
        assert observed["root_observed_exited"] is False, observed
        assert observed["elapsed_ms"] == 30000, observed
    else:
        assert observed["confirmed"] is True, observed
        assert observed["root_observed_exited"] is True, observed
        assert observed["wait_calls"] >= root_signals_on, observed
        assert 0 < observed["elapsed_ms"] < 30000, observed


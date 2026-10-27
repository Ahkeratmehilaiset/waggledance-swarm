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


# S14: isolate the real binder and command assignment, not the adapter body.
# Only a local fake wrapper can run; no native job or installed component runs.
_NUMERIC_DEFAULTS = {
    "CaptureWindow": 3000, "CaptureWorkers": 4, "CaptureCycles": 36,
    "CaptureIntervalSeconds": 300, "RunDeadlineSeconds": 1800,
}
_NUMERIC_CASES = [
    ("historical_defaults", {}, True),
    ("operator_packet", {
        "CaptureWindow": 100, "CaptureWorkers": 4, "CaptureCycles": 1,
        "CaptureIntervalSeconds": 0, "RunDeadlineSeconds": 600,
    }, True),
    ("lower_bounds", {
        "CaptureWindow": 1, "CaptureWorkers": 1, "CaptureCycles": 1,
        "CaptureIntervalSeconds": 0, "RunDeadlineSeconds": 60,
    }, True),
    ("upper_bounds", {
        "CaptureWindow": 99500, "CaptureWorkers": 2147483647,
        "CaptureCycles": 2147483647, "CaptureIntervalSeconds": 2147483647,
        "RunDeadlineSeconds": 1800,
    }, True),
]
for _name, _value in [
    ("CaptureWindow", 0), ("CaptureWindow", 99501),
    ("CaptureWorkers", 0), ("CaptureWorkers", -1),
    ("CaptureCycles", 0), ("CaptureCycles", -1),
    ("CaptureIntervalSeconds", -1), ("RunDeadlineSeconds", 59),
    ("RunDeadlineSeconds", 1801), ("RunDeadlineSeconds", "Infinity"),
    ("CaptureWorkers", "NaN"), ("CaptureCycles", 2147483648),
    ("CaptureWindow", "100; throw 'injected'"),
    ("ProducerArgs", "--seed-from C:\\unapproved.json"),
]:
    _NUMERIC_CASES.append((f"refuse_{_name}_{len(_NUMERIC_CASES)}",
                           {_name: _value}, False))


@pytest.fixture(scope="module", params=LANE_TEST_SHELLS,
                ids=lambda ps: Path(ps).stem)
def bounded_parameter_runs(request):
    adapter_path = str(REBOOT / "Start-WdMindShadowJob.ps1").replace("'", "''")
    cases = json.dumps([{"name": name, "values": values}
                        for name, values, _ in _NUMERIC_CASES]).replace("'", "''")
    driver = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    'ADAPTER_PATH', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'adapter parse error' }
$top = @($ast.EndBlock.Statements)
$quote = @($top | Where-Object {
    $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $_.Name -ceq 'ConvertTo-WdShadowCommandLiteral'
})
$command = @($top | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
    $_.Left.Extent.Text -ceq '$rootCommand'
})
$deadline = @($top | Where-Object {
    $_ -is [Management.Automation.Language.AssignmentStatementAst] -and
    $_.Left.Extent.Text -ceq '$DeadlineSeconds'
})
if ($quote.Count -ne 1 -or $command.Count -ne 1 -or $deadline.Count -ne 1) {
    throw 'adapter extraction not unique'
}
# Only the pure escaper, actual binder and these two actual assignments run.
# The root's witness/run-id prologue are empty strings, never executed.
. ([scriptblock]::Create($quote[0].Extent.Text))
function Invoke-FakeWrapper {
    [CmdletBinding(PositionalBinding = $false)]
    param(
        [Parameter(Mandatory, Position = 0)] [string] $Tool,
        [switch] $VerifyPackage,
        [Parameter(ValueFromRemainingArguments)] [string[]] $ToolArguments = @()
    )
    $script:fakeCalls++
    $script:fakeCall = [ordered]@{
        tool = $Tool; verify = [bool]$VerifyPackage; arguments = @($ToolArguments)
    }
}
$RootRunIdPrefix = ''
$rootWitness = ''
$RootExitShim = ''
$launchId = '11111111-2222-4333-8444-555555555555'
$wrapperPath = 'Invoke-FakeWrapper'
$codeRootPath = "C:\synthetic source's"
$binPath = 'C:\synthetic bin'
$liveFull = "C:\synthetic live's"
$nl = [Environment]::NewLine
$body = $ast.ParamBlock.Extent.Text + $nl + $deadline[0].Extent.Text +
    $nl + $command[0].Extent.Text + @'

& ([scriptblock]::Create($rootCommand)) | Out-Null
[ordered]@{ deadline = $DeadlineSeconds; call = $script:fakeCall }
'@
$only = [scriptblock]::Create('[CmdletBinding(PositionalBinding = $false)]' + $nl + $body)
$rows = @()
foreach ($case in (ConvertFrom-Json -InputObject 'CASES_JSON')) {
    $arguments = @{
        ExpectedSourceCommit = ('a' * 40)
        ExpectedManifestSha256 = ('B' * 64)
        ExpectedAdapterSha256 = ('C' * 64)
        ApprovedRunRoot = 'C:\synthetic run'
        LiveDir = 'C:\synthetic live'
        ApprovedUserSid = 'S-1-5-21-1-2-3-4'
        ApprovedIntegrityLevel = 'medium'
        ExpectedPeakAdditionalBytes = 1
    }
    foreach ($property in $case.values.PSObject.Properties) {
        $arguments[$property.Name] = $property.Value
    }
    $script:fakeCalls = 0
    $script:fakeCall = $null
    $row = [ordered]@{ name = $case.name; accepted = $false; error = ''; value = $null }
    try {
        $row.value = & $only @arguments
        $row.accepted = $true
    } catch {
        $row.error = $_.Exception.Message
    }
    $row['wrapper_calls'] = $script:fakeCalls
    $rows += $row
}
[ordered]@{
    rows = @($rows)
    code_root = $codeRootPath; bin = $binPath; live = $liveFull
} | ConvertTo-Json -Depth 8 -Compress
'''.replace("ADAPTER_PATH", adapter_path).replace("CASES_JSON", cases)
    done = _run_powershell(driver, executable=request.param, check=False)
    assert done.returncode == 0, done.stderr
    observed = json.loads(done.stdout)
    observed["by_name"] = {row["name"]: row for row in observed["rows"]}
    assert len(observed["by_name"]) == len(_NUMERIC_CASES)
    return observed


@pytest.mark.parametrize("name,values,accepted", _NUMERIC_CASES,
                         ids=[case[0] for case in _NUMERIC_CASES])
def test_bounded_parameters_bind_before_the_fake_wrapper(bounded_parameter_runs,
                                                        name, values, accepted):
    observed = bounded_parameter_runs
    row = observed["by_name"][name]
    assert row["accepted"] is accepted, row
    if not accepted:
        assert row["wrapper_calls"] == 0, row
        assert row["error"], row
        return
    resolved = {**_NUMERIC_DEFAULTS, **values}
    assert row["wrapper_calls"] == 1, row
    assert row["value"]["deadline"] == resolved["RunDeadlineSeconds"], row
    call = row["value"]["call"]
    assert call["tool"] == "sandbox/mind/mind_capture_producer.py", call
    assert call["verify"] is True, call
    assert call["arguments"] == [
        "--src", observed["code_root"], "--bin", observed["bin"],
        "--live-dir", observed["live"],
        "--window", str(resolved["CaptureWindow"]),
        "--workers", str(resolved["CaptureWorkers"]),
        "--cycles", str(resolved["CaptureCycles"]),
        "--interval", str(resolved["CaptureIntervalSeconds"]),
    ], call
    assert "--deadline" not in call["arguments"]
    assert "--seed-from" not in call["arguments"]

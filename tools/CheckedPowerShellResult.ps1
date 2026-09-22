<#
.SYNOPSIS
Validates a PowerShell callback result without consulting a prior native exit code.

.DESCRIPTION
Invoke-CheckedResult accepts exactly one result object with a Boolean passed
property whose value is $true. It intentionally does not inspect $LASTEXITCODE:
that value belongs to the most recent native executable and may be stale.

Example:
    . .\tools\CheckedPowerShellResult.ps1
    Invoke-CheckedResult { & validate.ps1 -Round 1 | ConvertFrom-Json }

If a callback invokes a native program, the callback itself must check that
program's fresh exit code explicitly. This helper cannot validate native
program success automatically.
#>
function Invoke-CheckedResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [scriptblock] $Action
    )

    $callerErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Stop'
        # Capture this invocation's error stream, not the bounded global history.
        # An explicit -ErrorAction Continue must not turn an error into success.
        $results = @(& $Action 2>&1)
    }
    finally {
        $ErrorActionPreference = $callerErrorActionPreference
    }

    foreach ($item in $results) {
        if ($item -is [System.Management.Automation.ErrorRecord]) {
            throw 'Action emitted one or more PowerShell errors.'
        }
    }
    if ($results.Count -ne 1) {
        throw 'Action must return exactly one result object.'
    }

    $result = $results[0]
    if ($null -eq $result) {
        throw 'Action must not return null.'
    }

    if ($result -is [System.Collections.IDictionary]) {
        if (-not $result.Contains('passed')) {
            throw "Result must contain a 'passed' property."
        }
        $passed = $result['passed']
    }
    else {
        $passedProperty = $result.PSObject.Properties['passed']
        if ($null -eq $passedProperty) {
            throw "Result must contain a 'passed' property."
        }
        $passed = $passedProperty.Value
    }

    if ($passed -isnot [bool]) {
        throw "Result 'passed' property must be Boolean."
    }
    if (-not $passed) {
        throw "Result 'passed' property must be true."
    }

    return $result
}

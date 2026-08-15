#requires -Version 5.1
<#
.SYNOPSIS
    Makes the WD-Supervisor scheduled-task action version-independent.

.DESCRIPTION
    This script changes only the WD-Supervisor exec action.  It preserves the
    existing triggers, principal, settings, and enabled state.  It never
    enables or registers a merge-driver task.
#>
[CmdletBinding()]
param(
    [switch] $Apply,
    [string] $TaskName = 'WD-Supervisor',
    [string] $SupervisorScript = 'C:\Python\wd_supervisor.ps1'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$stablePowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $stablePowerShell -PathType Leaf)) {
    throw "stable Windows PowerShell executable is missing: $stablePowerShell"
}
if (-not (Test-Path -LiteralPath $SupervisorScript -PathType Leaf)) {
    throw "supervisor entry point is missing: $SupervisorScript"
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if (-not $task.Settings.Enabled) {
    throw "$TaskName is disabled; refusing to silently change its operating state"
}

$arguments = (
    '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass ' +
    '-File "{0}" -Apply' -f $SupervisorScript
)
$desiredAction = New-ScheduledTaskAction `
    -Execute $stablePowerShell `
    -Argument $arguments `
    -WorkingDirectory 'C:\Python'

Write-Host ("{0}: {1} {2}" -f $TaskName, $stablePowerShell, $arguments)
if (-not $Apply) {
    Write-Host 'PLAN ONLY: pass -Apply to replace this action.'
    return
}

$originalActions = @($task.Actions)
try {
    [void](Set-ScheduledTask -TaskName $TaskName -Action $desiredAction)
    $verified = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $actions = @($verified.Actions)
    if ($actions.Count -ne 1) {
        throw "$TaskName action verification failed: expected one action"
    }
    $actual = $actions[0]
    if (
        [string]$actual.Execute -cne $stablePowerShell -or
        [string]$actual.Arguments -cne $arguments -or
        [string]$actual.WorkingDirectory -cne 'C:\Python'
    ) {
        throw "$TaskName action verification failed after Set-ScheduledTask"
    }
    if (
        [string]$actual.Execute -match 'WindowsApps' -or
        [string]$actual.Arguments -match 'BridgeMergeDriver'
    ) {
        throw "$TaskName action contains a forbidden version pin or merge-driver reference"
    }
}
catch {
    $registrationFailure = $_
    try {
        [void](Set-ScheduledTask -TaskName $TaskName -Action $originalActions)
    }
    catch {
        throw (
            "{0}; restoring the original task action also failed: {1}" -f
                $registrationFailure.Exception.Message,
                $_.Exception.Message
        )
    }
    throw $registrationFailure
}
Write-Host "$TaskName now uses the stable Windows PowerShell path." -ForegroundColor Green

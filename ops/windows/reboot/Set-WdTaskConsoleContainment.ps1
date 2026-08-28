#requires -Version 5.1
<#
.SYNOPSIS
  Keeps known WD scheduled jobs from opening console windows.

.DESCRIPTION
  Dry-run by default. With -Apply, disables and stops the legacy merge-driver
  loop and routes two read-only reporting jobs through the existing hidden
  process launcher. Task triggers, principals, settings, enabled state, and
  working directories are otherwise preserved. Unknown action drift fails
  closed before mutation.
#>
[CmdletBinding()]
param([switch] $Apply)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$silentLauncher = 'C:\Python\wd_silent_launch.exe'
$silentLauncherSha256 = '4CD4FBED01E3EAD1C999493212F7499137C0937F597BDD5172C0EFEEDA3F509F'

function Get-RootTask {
  param([Parameter(Mandatory)] [string] $Name)

  $tasks = @(Get-ScheduledTask -TaskPath '\' -TaskName $Name -ErrorAction SilentlyContinue)
  if ($tasks.Count -gt 1) {
    throw "scheduled task identity is ambiguous: $Name"
  }
  if ($tasks.Count -eq 0) { return $null }
  return $tasks[0]
}

function Test-ActionExact {
  param(
    [Parameter(Mandatory)] $Task,
    [Parameter(Mandatory)] [string] $Execute,
    [Parameter(Mandatory)] [string] $Arguments,
    [string] $WorkingDirectory = ''
  )

  $actions = @($Task.Actions)
  return (
    $actions.Count -eq 1 -and
    [string]$actions[0].Execute -ceq $Execute -and
    [string]$actions[0].Arguments -ceq $Arguments -and
    [string]$actions[0].WorkingDirectory -ceq $WorkingDirectory
  )
}

function Assert-SilentLauncher {
  if (-not (Test-Path -LiteralPath $silentLauncher -PathType Leaf)) {
    throw "silent WD launcher is missing: $silentLauncher"
  }
  if (
    (Get-FileHash -LiteralPath $silentLauncher -Algorithm SHA256).Hash -cne
      $silentLauncherSha256
  ) {
    throw "silent WD launcher integrity mismatch: $silentLauncher"
  }
}

Assert-SilentLauncher

$legacyName = 'WD-BridgeMergeDriver'
$legacyExecute = 'powershell.exe'
$legacyArguments = '-NoProfile -ExecutionPolicy Bypass -File C:\Python\Invoke-BridgeMergeDriver.ps1 -Loop -PollSeconds 120'
$legacy = Get-RootTask -Name $legacyName
if ($null -ne $legacy -and -not (Test-ActionExact `
    -Task $legacy `
    -Execute $legacyExecute `
    -Arguments $legacyArguments)) {
  throw "legacy merge-driver task action drifted: $legacyName"
}

$jobs = @(
  [pscustomobject]@{
    name = 'WD-ConsensusStallDetector'
    original_execute = 'C:\Users\janik\AppData\Local\Microsoft\WindowsApps\python.exe'
    original_arguments = 'C:\Python\wd_consensus_stall_detector.py --alert'
    original_working_directory = 'C:\Python'
    hidden_arguments = '"C:\Users\janik\AppData\Local\Microsoft\WindowsApps\python.exe" "C:\Python\wd_consensus_stall_detector.py" --alert'
    hidden_working_directory = 'C:\Python'
  },
  [pscustomobject]@{
    name = 'WD-AgentValue-Weekly'
    original_execute = 'C:\Python\project2-master\.python\Python313\python.exe'
    original_arguments = 'C:\Python\wd-agent-value-metric.py --days 7 --post-bridge'
    original_working_directory = ''
    hidden_arguments = '"C:\Python\project2-master\.python\Python313\python.exe" "C:\Python\wd-agent-value-metric.py" --days 7 --post-bridge'
    hidden_working_directory = ''
  }
)

$plans = New-Object 'System.Collections.Generic.List[object]'
foreach ($job in $jobs) {
  $task = Get-RootTask -Name ([string]$job.name)
  if ($null -eq $task) {
    [void]$plans.Add([pscustomobject]@{
      name = [string]$job.name
      action = 'absent-skip'
      enabled = $false
    })
    continue
  }
  $isOriginal = Test-ActionExact `
    -Task $task `
    -Execute ([string]$job.original_execute) `
    -Arguments ([string]$job.original_arguments) `
    -WorkingDirectory ([string]$job.original_working_directory)
  $isHidden = Test-ActionExact `
    -Task $task `
    -Execute $silentLauncher `
    -Arguments ([string]$job.hidden_arguments) `
    -WorkingDirectory ([string]$job.hidden_working_directory)
  if (-not $isOriginal -and -not $isHidden) {
    throw "scheduled console task action drifted: $($job.name)"
  }
  [void]$plans.Add([pscustomobject]@{
    name = [string]$job.name
    action = if ($isHidden) { 'hidden-exact' } else { 'wrap-hidden' }
    enabled = [bool]$task.Settings.Enabled
  })
}

if (-not $Apply) {
  [pscustomobject]@{
    schema = 'wd.task-console-containment.v1'
    applied = $false
    legacy = if ($null -eq $legacy) {
      'absent-skip'
    } elseif (-not [bool]$legacy.Settings.Enabled -and [string]$legacy.State -ne 'Running') {
      'hold-exact'
    } else {
      'would-hold'
    }
    jobs = [object[]]$plans.ToArray()
  }
  return
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'scheduled-task console containment requires an Administrator PowerShell'
}

if ($null -ne $legacy) {
  Disable-ScheduledTask -TaskPath '\' -TaskName $legacyName | Out-Null
  Stop-ScheduledTask -TaskPath '\' -TaskName $legacyName -ErrorAction SilentlyContinue
  $legacyAfter = Get-RootTask -Name $legacyName
  if (
    $null -eq $legacyAfter -or
    [bool]$legacyAfter.Settings.Enabled -or
    [string]$legacyAfter.State -eq 'Running'
  ) {
    throw "legacy merge-driver HOLD verification failed: $legacyName"
  }
}

foreach ($job in $jobs) {
  $task = Get-RootTask -Name ([string]$job.name)
  if ($null -eq $task) { continue }
  $enabledBefore = [bool]$task.Settings.Enabled
  if (-not (Test-ActionExact `
      -Task $task `
      -Execute $silentLauncher `
      -Arguments ([string]$job.hidden_arguments) `
      -WorkingDirectory ([string]$job.hidden_working_directory))) {
    $actionParameters = @{
      Execute = $silentLauncher
      Argument = [string]$job.hidden_arguments
    }
    if (-not [string]::IsNullOrEmpty([string]$job.hidden_working_directory)) {
      $actionParameters['WorkingDirectory'] = [string]$job.hidden_working_directory
    }
    $action = New-ScheduledTaskAction @actionParameters
    Set-ScheduledTask `
      -TaskPath '\' `
      -TaskName ([string]$job.name) `
      -Action $action |
      Out-Null
  }
  $after = Get-RootTask -Name ([string]$job.name)
  if (
    $null -eq $after -or
    [bool]$after.Settings.Enabled -ne $enabledBefore -or
    -not (Test-ActionExact `
      -Task $after `
      -Execute $silentLauncher `
      -Arguments ([string]$job.hidden_arguments) `
      -WorkingDirectory ([string]$job.hidden_working_directory))
  ) {
    throw "scheduled console task postcondition failed: $($job.name)"
  }
}

[pscustomobject]@{
  schema = 'wd.task-console-containment.v1'
  applied = $true
  legacy = if ($null -eq $legacy) { 'absent-skip' } else { 'hold-exact' }
  jobs = @($plans.ToArray() | ForEach-Object {
      [pscustomobject]@{
        name = [string]$_.name
        action = if ([string]$_.action -eq 'absent-skip') {
          'absent-skip'
        } else {
          'hidden-exact'
        }
        enabled = [bool]$_.enabled
      }
    })
}

#requires -Version 5.1
<#
.SYNOPSIS
  Keeps known WD scheduled jobs from opening console windows.

.DESCRIPTION
  Dry-run by default. With -Apply, disables and stops the legacy merge-driver
  loop and routes two read-only reporting jobs through the existing hidden
  process launcher. Task triggers, principals, settings, enabled state, and
  working directories are otherwise preserved. Unknown action drift fails
  closed before mutation, and so does any change to a job's task between the
  plan and the apply.
#>
[CmdletBinding()]
param([switch] $Apply)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$silentLauncher = 'C:\Python\wd_silent_launch.exe'
$bundleStore = 'C:\Python\wd-reboot-bundles'
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

function Get-VerifiedBridgePinSuffix {
  # The weekly agent-value metric and the consensus-stall detector write through a pinned
  # bridge writer (--bridge-bundle and --bridge-manifest-sha256), so their tasks may carry
  # exactly that pin after the base arguments. The pin is accepted only when it names an existing deployed bundle
  # whose deployment manifest hashes to the given value. Returns '' for the bare base
  # arguments, the verified suffix for a pinned form, and $null for anything else.
  param(
    [Parameter(Mandatory)] [AllowEmptyString()] [string] $Arguments,
    [Parameter(Mandatory)] [string] $Base,
    [Parameter(Mandatory)] [string] $BundleStore
  )

  if ($Arguments -ceq $Base) { return '' }
  if (-not $Arguments.StartsWith($Base, [StringComparison]::Ordinal)) { return $null }
  $suffix = $Arguments.Substring($Base.Length)
  $pattern = '^ --bridge-bundle "' + [regex]::Escape($BundleStore) +
    '\\([0-9a-f]{40})" --bridge-manifest-sha256 ([0-9A-F]{64})$'
  $match = [regex]::Match($suffix, $pattern)
  if (-not $match.Success) { return $null }
  $bundle = Join-Path $BundleStore $match.Groups[1].Value
  $manifest = Join-Path $bundle 'deployment-manifest.json'
  if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { return $null }
  foreach ($item in @((Get-Item -LiteralPath $bundle -Force), (Get-Item -LiteralPath $manifest -Force))) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $null }
  }
  if ((Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash -cne $match.Groups[2].Value) {
    return $null
  }
  return $suffix
}

function Get-TaskBridgePin {
  # The verified pin of a task's single action, against either base form; '' when the
  # job takes no pin or the task carries none.
  param([Parameter(Mandatory)] $Task, [Parameter(Mandatory)] $Job, [Parameter(Mandatory)] [string] $BundleStore)

  # Only a job that declares bridge_pin takes one; the property is absent elsewhere (StrictMode).
  $declared = $Job.PSObject.Properties['bridge_pin']
  if ($null -eq $declared -or -not [bool]$declared.Value) { return '' }
  $actions = @($Task.Actions)
  if ($actions.Count -ne 1) { return '' }
  $arguments = [string]$actions[0].Arguments
  foreach ($base in @([string]$Job.hidden_arguments, [string]$Job.original_arguments)) {
    $suffix = Get-VerifiedBridgePinSuffix -Arguments $arguments -Base $base -BundleStore $BundleStore
    if ($null -ne $suffix) { return $suffix }
  }
  return ''
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

function Assert-TaskUnchangedSincePlan {
  # Apply changes only the exact task the plan verified. Any of these fails closed
  # before the task is changed: a task that appeared or vanished, another action,
  # another enabled state, or a pin that no longer verifies.
  param(
    [Parameter(Mandatory)] $Job,
    [AllowNull()] $Planned,
    [AllowNull()] $Task,
    [Parameter(Mandatory)] [AllowEmptyString()] [string] $Pin,
    [Parameter(Mandatory)] [string] $BundleStore
  )

  if ($null -eq $Planned -and $null -eq $Task) { return }
  if (
    $null -eq $Planned -or
    $null -eq $Task -or
    [bool]$Task.Settings.Enabled -ne [bool]$Planned.enabled -or
    -not (Test-ActionExact `
      -Task $Task `
      -Execute ([string]$Planned.execute) `
      -Arguments ([string]$Planned.arguments) `
      -WorkingDirectory ([string]$Planned.working_directory)) -or
    [string](Get-TaskBridgePin -Task $Task -Job $Job -BundleStore $BundleStore) -cne $Pin
  ) {
    throw "scheduled console task changed between plan and apply: $($Job.name)"
  }
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
    bridge_pin = $true
  },
  [pscustomobject]@{
    name = 'WD-AgentValue-Weekly'
    original_execute = 'C:\Python\project2-master\.python\Python313\python.exe'
    original_arguments = 'C:\Python\wd-agent-value-metric.py --days 7 --post-bridge'
    original_working_directory = ''
    hidden_arguments = '"C:\Python\project2-master\.python\Python313\python.exe" "C:\Python\wd-agent-value-metric.py" --days 7 --post-bridge'
    hidden_working_directory = ''
    bridge_pin = $true
  }
)

$plans = New-Object 'System.Collections.Generic.List[object]'
$pins = @{}
$planned = @{}
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
  $pin = Get-TaskBridgePin -Task $task -Job $job -BundleStore $bundleStore
  $pins[[string]$job.name] = $pin
  $isOriginal = Test-ActionExact `
    -Task $task `
    -Execute ([string]$job.original_execute) `
    -Arguments ([string]$job.original_arguments + $pin) `
    -WorkingDirectory ([string]$job.original_working_directory)
  $isHidden = Test-ActionExact `
    -Task $task `
    -Execute $silentLauncher `
    -Arguments ([string]$job.hidden_arguments + $pin) `
    -WorkingDirectory ([string]$job.hidden_working_directory)
  if (-not $isOriginal -and -not $isHidden) {
    throw "scheduled console task action drifted: $($job.name)"
  }
  $planned[[string]$job.name] = [pscustomobject]@{
    execute = [string]@($task.Actions)[0].Execute
    arguments = [string]@($task.Actions)[0].Arguments
    working_directory = [string]@($task.Actions)[0].WorkingDirectory
    enabled = [bool]$task.Settings.Enabled
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

# Check every job before the first change, then each job again just before its own.
foreach ($job in $jobs) {
  Assert-TaskUnchangedSincePlan `
    -Job $job `
    -Planned $planned[[string]$job.name] `
    -Task (Get-RootTask -Name ([string]$job.name)) `
    -Pin ([string]$pins[[string]$job.name]) `
    -BundleStore $bundleStore
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
  Assert-TaskUnchangedSincePlan `
    -Job $job `
    -Planned $planned[[string]$job.name] `
    -Task $task `
    -Pin ([string]$pins[[string]$job.name]) `
    -BundleStore $bundleStore
  if ($null -eq $task) { continue }
  $enabledBefore = [bool]$task.Settings.Enabled
  # Wrapping keeps the verified pin the plan saw; the metric cannot run without it.
  $hiddenArguments = [string]$job.hidden_arguments + [string]$pins[[string]$job.name]
  if (-not (Test-ActionExact `
      -Task $task `
      -Execute $silentLauncher `
      -Arguments $hiddenArguments `
      -WorkingDirectory ([string]$job.hidden_working_directory))) {
    $actionParameters = @{
      Execute = $silentLauncher
      Argument = $hiddenArguments
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
      -Arguments $hiddenArguments `
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

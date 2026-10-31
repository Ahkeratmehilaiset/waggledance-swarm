#requires -Version 5.1
<#
.SYNOPSIS
Plan or register a disabled, bounded, manually started Mind shadow pilot task.
.DESCRIPTION
The default only validates the supplied installed generation and prints a plan.
-Apply registers a DISABLED task with no trigger; it never starts the task or
the producer and never replaces an existing task. Enabling and manually starting
the task require separate authority. The adapter permits one bounded pilot run
from a fresh LiveDir, but does not support resuming that directory on a later
run. A missing publication is not proof that no earlier attempt ran, and this
task does not enforce a durable one-attempt latch. No automatic retry or
recurring refresh is configured. This is not evidence that a producer ran,
that its data is suitable for production, or that process containment works.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[0-9a-f]{40}$')] [string] $ExpectedSourceCommit,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9A-F]{64}$')] [string] $ExpectedManifestSha256,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9A-F]{64}$')] [string] $ExpectedAdapterSha256,
    [Parameter(Mandatory)] [string] $ApprovedRunRoot,
    [Parameter(Mandatory)] [string] $LiveDir,
    [Parameter(Mandatory)] [ValidatePattern('^S-1-5-21-\d+-\d+-\d+-\d+$')] [string] $ApprovedUserSid,
    # A Limited interactive scheduled task is intended for a medium token;
    # the adapter independently checks the actual token when it runs.
    [Parameter(Mandatory)] [ValidateSet('medium')] [string] $ApprovedIntegrityLevel,
    [Parameter(Mandatory)] [ValidateScript({ $_ -gt 0 })] [long] $ExpectedPeakAdditionalBytes,
    [Parameter(Mandatory)] [ValidateRange(1, 99500)] [int] $CaptureWindow,
    [Parameter(Mandatory)] [ValidateRange(1, 2147483647)] [int] $CaptureWorkers,
    [Parameter(Mandatory)] [ValidateRange(1, 2147483647)] [int] $CaptureCycles,
    [Parameter(Mandatory)] [ValidateRange(0, 2147483647)] [int] $CaptureIntervalSeconds,
    [Parameter(Mandatory)] [ValidateRange(60, 900)] [int] $RunDeadlineSeconds,
    [switch] $Apply
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-WdRefreshPath {
    param([Parameter(Mandatory)] [string] $Path, [switch] $Directory)
    if (-not [IO.Path]::IsPathRooted($Path) -or $Path -notmatch '^[A-Za-z]:\\' -or $Path.Contains('%')) {
        throw 'A fully qualified local drive path is required.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.TrimEnd('\') -ieq [IO.Path]::GetPathRoot($full).TrimEnd('\')) {
        throw 'A volume root is not an approved task path.'
    }
    $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if ([bool]$item.PSIsContainer -ne [bool]$Directory) { throw 'Task path has the wrong file type.' }
    $walk = $item
    while ($null -ne $walk) {
        if (($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'A task path contains a reparse point.'
        }
        if ($walk.PSIsContainer) { $walk = $walk.Parent } else { $walk = $walk.Directory }
    }
    return $full
}

function ConvertTo-WdRefreshLiteral {
    param([AllowEmptyString()] [string] $Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

$bundle = Assert-WdRefreshPath -Path (Join-Path 'C:\Python\wd-reboot-bundles' $ExpectedSourceCommit) -Directory
$manifestPath = Assert-WdRefreshPath -Path (Join-Path $bundle 'deployment-manifest.json')
$adapterPath = Assert-WdRefreshPath -Path (Join-Path $bundle 'Start-WdMindShadowJob.ps1')
$fleetPath = Assert-WdRefreshPath -Path (Join-Path $bundle 'wd-fleet.json')
$statePath = Assert-WdRefreshPath -Path 'C:\Python\WD_REBOOT_STATE_CURRENT.json'
$runRoot = Assert-WdRefreshPath -Path $ApprovedRunRoot -Directory
$liveRoot = Assert-WdRefreshPath -Path $LiveDir -Directory
$codeRoot = Assert-WdRefreshPath -Path (Join-Path $bundle 'tools-bootstrap') -Directory
$binRoot = Assert-WdRefreshPath -Path (Join-Path $codeRoot '.agent-bridge\bin') -Directory
$wrapper = Assert-WdRefreshPath -Path (Join-Path $bundle 'Invoke-WdBridgePython.ps1')
if ($runRoot.TrimEnd('\') -ieq $liveRoot.TrimEnd('\') -or
    $runRoot.StartsWith($liveRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $liveRoot.StartsWith($runRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The run root and live directory must be separate, non-nested directories.'
}
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne $ApprovedUserSid) {
    throw 'The approved SID does not match the current user.'
}
if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash -cne $ExpectedManifestSha256 -or
    (Get-FileHash -LiteralPath $adapterPath -Algorithm SHA256).Hash -cne $ExpectedAdapterSha256) {
    throw 'The installed manifest or adapter hash does not match the supplied pin.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$adapterEntry = $manifest.files.PSObject.Properties['Start-WdMindShadowJob.ps1']
if ([int]$manifest.schema_version -ne 1 -or [string]$manifest.source_commit -cne $ExpectedSourceCommit -or
    $null -eq $adapterEntry -or ([string]$adapterEntry.Value).ToUpperInvariant() -cne $ExpectedAdapterSha256) {
    throw 'The installed manifest does not bind this adapter and commit.'
}
$fleetEntry = $manifest.files.PSObject.Properties['wd-fleet.json']
$fleetBytes = [IO.File]::ReadAllBytes($fleetPath)
if ($null -eq $fleetEntry -or $fleetBytes.Length -gt 1048576) { throw 'The fleet pin is unavailable.' }
$fleetHasher = [Security.Cryptography.SHA256]::Create()
try { $fleetHash = [BitConverter]::ToString($fleetHasher.ComputeHash($fleetBytes)).Replace('-', '') }
finally { $fleetHasher.Dispose() }
if ($fleetHash -cne ([string]$fleetEntry.Value).ToUpperInvariant()) { throw 'The fleet bytes do not match the manifest.' }
$fleet = [Text.Encoding]::UTF8.GetString($fleetBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
$runtimeRoot = Assert-WdRefreshPath -Path ([string]$fleet.runtime_root) -Directory
$mindConfig = $fleet.PSObject.Properties['mind_read']
if ($null -ne $mindConfig -and $null -ne $mindConfig.Value -and
    $null -ne $mindConfig.Value.PSObject.Properties['live_dir_mode'] -and
    [string]$mindConfig.Value.live_dir_mode -ceq 'shared_runtime_audit') {
    $expectedLive = [IO.Path]::GetFullPath((Join-Path $runtimeRoot 'shared\.codex-audit\mind-live'))
    if (-not $liveRoot.TrimEnd('\').Equals($expectedLive.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The producer live directory does not match the fleet Mind location.'
    }
}
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$state.source_commit -cne $ExpectedSourceCommit -or
    [string]$state.final_commit -cne $ExpectedSourceCommit -or
    [string]$state.manifest_sha256 -cne $ExpectedManifestSha256 -or
    [string]$state.final_manifest_sha256 -cne $ExpectedManifestSha256 -or
    -not [string]::Equals([string]$state.active_bundle, $bundle, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The active state pointer does not bind the supplied commit and manifest.'
}

# Values are PowerShell single-quoted literals, not executable fragments. The
# fixed bootstrap is encoded as UTF-16LE so paths and shell metacharacters are
# not parsed by cmd.exe. Recheck pins before loading the adapter on manual start;
# the adapter then performs its own state, identity, wrapper and package checks.
# Hash-then-execute still has a TOCTOU interval and is not an OS isolation boundary.
$bootstrap = @'
$ErrorActionPreference = 'Stop'
$manifest = %MANIFEST%
$adapter = %ADAPTER%
if ((Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash -cne %MANIFEST_HASH% -or
    (Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $adapter -Algorithm SHA256).Hash -cne %ADAPTER_HASH%) {
    throw 'Mind refresh pins changed; refusing execution.'
}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH = %MANIFEST_HASH%
$env:WD_BRIDGE_CODE_ROOT = %CODE_ROOT%
$env:WD_BRIDGE_BIN = %BIN_ROOT%
$env:WD_BRIDGE_PYTHON_WRAPPER = %WRAPPER%
$env:AGENT_BRIDGE_RUNTIME_ROOT = %RUNTIME_ROOT%
& $adapter -ExpectedSourceCommit %COMMIT% -ExpectedManifestSha256 %MANIFEST_HASH% -ExpectedAdapterSha256 %ADAPTER_HASH% -ApprovedRunRoot %RUN_ROOT% -LiveDir %LIVE_ROOT% -ApprovedUserSid %SID% -ApprovedIntegrityLevel %INTEGRITY% -ExpectedPeakAdditionalBytes %PEAK% -CaptureWindow %WINDOW% -CaptureWorkers %WORKERS% -CaptureCycles %CYCLES% -CaptureIntervalSeconds %INTERVAL% -RunDeadlineSeconds %DEADLINE%
if (-not $?) { exit 1 }
exit 0
'@
$values = [ordered]@{
    '%MANIFEST%' = $manifestPath; '%ADAPTER%' = $adapterPath; '%MANIFEST_HASH%' = $ExpectedManifestSha256
    '%ADAPTER_HASH%' = $ExpectedAdapterSha256; '%CODE_ROOT%' = $codeRoot; '%BIN_ROOT%' = $binRoot
    '%WRAPPER%' = $wrapper; '%RUNTIME_ROOT%' = $runtimeRoot; '%COMMIT%' = $ExpectedSourceCommit; '%RUN_ROOT%' = $runRoot
    '%LIVE_ROOT%' = $liveRoot; '%SID%' = $ApprovedUserSid; '%INTEGRITY%' = $ApprovedIntegrityLevel
    '%PEAK%' = $ExpectedPeakAdditionalBytes.ToString([Globalization.CultureInfo]::InvariantCulture)
    '%WINDOW%' = $CaptureWindow.ToString([Globalization.CultureInfo]::InvariantCulture)
    '%WORKERS%' = $CaptureWorkers.ToString([Globalization.CultureInfo]::InvariantCulture)
    '%CYCLES%' = $CaptureCycles.ToString([Globalization.CultureInfo]::InvariantCulture)
    '%INTERVAL%' = $CaptureIntervalSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
    '%DEADLINE%' = $RunDeadlineSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
}
foreach ($key in $values.Keys) { $bootstrap = $bootstrap.Replace($key, (ConvertTo-WdRefreshLiteral $values[$key])) }
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$hostPath = Assert-WdRefreshPath -Path ([IO.Path]::Combine([Environment]::SystemDirectory, 'WindowsPowerShell\v1.0\powershell.exe'))
$taskName = 'WD-MindShadowPilot'
$plan = [ordered]@{
    schema = 'wd.mind-shadow-pilot-plan.v1'; task = $taskName; registered = [bool]$Apply
    enabled = $false; source_commit = $ExpectedSourceCommit; manifest_sha256 = $ExpectedManifestSha256
    adapter_sha256 = $ExpectedAdapterSha256; user_sid = $ApprovedUserSid; integrity = $ApprovedIntegrityLevel
    run_root = $runRoot; live_dir = $liveRoot; expected_peak_additional_bytes = $ExpectedPeakAdditionalBytes
    trigger = 'none'; automatic_repetition = $false; overlap = 'IgnoreNew'; logon = 'Interactive'; run_level = 'Limited'
    deadline_seconds = $RunDeadlineSeconds; task_limit_minutes = 18; native_acceptance = 'NOT_RUN'
    activation = 'separate_enable_and_manual_start_required'; starting_the_task = 'not_performed'
    limitation = 'fresh_live_dir_only; no durable one_attempt latch or restart continuity'
}
if (-not $Apply) { $plan | ConvertTo-Json -Depth 4; return }

if ($null -ne (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    throw 'An existing task has this name; refusing to replace or alter it.'
}
$action = New-ScheduledTaskAction -Execute $hostPath -Argument ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ' + $encoded) -WorkingDirectory $bundle
$settings = New-ScheduledTaskSettingsSet -Disable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 18)
$principal = New-ScheduledTaskPrincipal -UserId $ApprovedUserSid -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Settings $settings -Principal $principal | Out-Null
$plan | ConvertTo-Json -Depth 4

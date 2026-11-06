#requires -Version 5.1
<#
.SYNOPSIS
Plan or register a disabled same-user recurring Mind update task.
.DESCRIPTION
Default is read-only planning. -Apply only registers a new DISABLED task; it
does not start or enable one, and refuses to replace an existing task. The
action checks the active generation and the config pins again on every run,
then invokes the packaged --once producer. An uncertain pending marker is
never removed here. Same-user path protection, native process containment,
source authenticity and actual execution are not proven by registration.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [string] $BundleRoot,
    [Parameter(Mandatory)] [string] $StatePath,
    [Parameter(Mandatory)] [string] $ConfigPath,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9a-f]{40}$')] [string] $ExpectedCommit,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9A-Fa-f]{64}$')] [string] $ExpectedManifestSha256,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9A-Fa-f]{64}$')] [string] $ExpectedConfigSha256,
    [Parameter(Mandatory)] [ValidatePattern('^S-1-5-21-\d+-\d+-\d+-\d+$')] [string] $ApprovedUserSid,
    [ValidateRange(1, 1440)] [int] $IntervalMinutes = 5,
    [switch] $Apply
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-LivePath {
    param([string] $Path, [switch] $Directory)
    if (-not [IO.Path]::IsPathRooted($Path) -or $Path -notmatch '^[A-Za-z]:\\' -or $Path.Contains('%')) {
        throw 'An absolute local path is required.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.TrimEnd('\') -ieq [IO.Path]::GetPathRoot($full).TrimEnd('\')) { throw 'A volume root is not permitted.' }
    $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if ([bool]($item.Attributes -band [IO.FileAttributes]::Directory) -ne [bool]$Directory) { throw 'Unexpected path type.' }
    $walk = $item
    while ($null -ne $walk) {
        if (($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point refused.' }
        if (($walk.Attributes -band [IO.FileAttributes]::Directory) -ne 0) { $walk = $walk.Parent } else { $walk = $walk.Directory }
    }
    return $full
}
function Literal([string] $Value) { return "'" + $Value.Replace("'", "''") + "'" }

$bundle = Assert-LivePath $BundleRoot -Directory
$stateFile = Assert-LivePath $StatePath
$configFile = Assert-LivePath $ConfigPath
$manifestFile = Assert-LivePath (Join-Path $bundle 'deployment-manifest.json')
$wrapper = Assert-LivePath (Join-Path $bundle 'Invoke-WdBridgePython.ps1')
$module = Assert-LivePath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_live_update_once.py')
$manifestHash = $ExpectedManifestSha256.ToUpperInvariant()
$configHash = $ExpectedConfigSha256.ToUpperInvariant()
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne $ApprovedUserSid) { throw 'User SID mismatch.' }
if ((Get-FileHash -LiteralPath $manifestFile -Algorithm SHA256).Hash -cne $manifestHash -or
    (Get-FileHash -LiteralPath $configFile -Algorithm SHA256).Hash -cne $configHash) { throw 'Manifest or config pin mismatch.' }
$manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
$state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
$config = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1 -or [string]$manifest.source_commit -cne $ExpectedCommit -or
    [string]$state.source_commit -cne $ExpectedCommit -or [string]$state.final_commit -cne $ExpectedCommit -or
    ([string]$state.manifest_sha256).ToUpperInvariant() -cne $manifestHash -or
    ([string]$state.final_manifest_sha256).ToUpperInvariant() -cne $manifestHash -or
    -not [string]::Equals([string]$state.active_bundle, $bundle, [StringComparison]::OrdinalIgnoreCase) -or
    [string]$config.schema -cne 'wd.q4-live-update-once.v1' -or
    ([string]$config.manifest_sha256).ToUpperInvariant() -cne $manifestHash) { throw 'State, commit or config does not bind the approved bundle.' }
foreach ($entry in @(@('Invoke-WdBridgePython.ps1', $wrapper), @('tools-bootstrap/tools/wd_q4_live_update_once.py', $module))) {
    $pin = $manifest.files.PSObject.Properties[$entry[0]]
    if ($null -eq $pin -or (Get-FileHash -LiteralPath $entry[1] -Algorithm SHA256).Hash -cne ([string]$pin.Value).ToUpperInvariant()) {
        throw 'Packaged tool hash does not match the manifest.'
    }
}
$routeFile = Assert-LivePath ([string]$config.route_path)
if ((Get-FileHash -LiteralPath $routeFile -Algorithm SHA256).Hash -cne ([string]$config.route_sha256).ToUpperInvariant()) {
    throw 'Route pin mismatch.'
}
$route = Get-Content -LiteralPath $routeFile -Raw -Encoding UTF8 | ConvertFrom-Json
$privateRoot = Assert-LivePath ([string]$route.private_state_root) -Directory
$marker = Join-Path $privateRoot 'q4-live-update.pending.json'
if (Test-Path -LiteralPath $marker) { throw 'An uncertain attempt requires reconciliation.' }

# All substituted values are single-quoted data. Execution checks state and
# all pins again; this remains a hash-then-execute TOCTOU boundary.
$bootstrap = @'
$ErrorActionPreference = 'Stop'
$state = Get-Content -LiteralPath %STATE% -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$state.source_commit -cne %COMMIT% -or [string]$state.final_commit -cne %COMMIT% -or
    ([string]$state.manifest_sha256).ToUpperInvariant() -cne %MANIFEST_HASH% -or
    ([string]$state.final_manifest_sha256).ToUpperInvariant() -cne %MANIFEST_HASH% -or
    -not [string]::Equals([string]$state.active_bundle, %BUNDLE%, [StringComparison]::OrdinalIgnoreCase) -or
    (Get-FileHash -LiteralPath %MANIFEST% -Algorithm SHA256).Hash -cne %MANIFEST_HASH% -or
    (Get-FileHash -LiteralPath %CONFIG% -Algorithm SHA256).Hash -cne %CONFIG_HASH% -or
    (Get-FileHash -LiteralPath %WRAPPER% -Algorithm SHA256).Hash -cne %WRAPPER_HASH% -or
    (Test-Path -LiteralPath %MARKER%)) { exit 3 }
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH = %MANIFEST_HASH%
& %WRAPPER% -Tool 'tools/wd_q4_live_update_once.py' -VerifyPackage -ToolArguments @('--once','--config',%CONFIG%,'--sha256',%CONFIG_HASH_LOWER%)
exit $LASTEXITCODE
'@
$values = [ordered]@{'%STATE%'=$stateFile; '%COMMIT%'=$ExpectedCommit; '%BUNDLE%'=$bundle;
    '%MANIFEST%'=$manifestFile; '%MANIFEST_HASH%'=$manifestHash; '%CONFIG%'=$configFile; '%CONFIG_HASH%'=$configHash;
    '%CONFIG_HASH_LOWER%'=$configHash.ToLowerInvariant(); '%WRAPPER%'=$wrapper;
    '%WRAPPER_HASH%'=(Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash; '%MARKER%'=$marker}
foreach ($key in $values.Keys) { $bootstrap = $bootstrap.Replace($key, (Literal $values[$key])) }
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$taskName = 'WD-MindLiveUpdate'
$plan = [ordered]@{schema='wd.mind-live-refresh-plan.v1'; task=$taskName; registered=[bool]$Apply; enabled=$false;
    source_commit=$ExpectedCommit; manifest_sha256=$manifestHash; config_sha256=$configHash; user_sid=$ApprovedUserSid;
    interval_minutes=$IntervalMinutes; multiple_instances='IgnoreNew'; logon='Interactive'; run_level='Limited';
    pending='blocks_attempt_until_reconciled'; execution_limit='unlimited'; supervisor='unchanged'; execution='NOT_RUN'}
if (-not $Apply) { $plan | ConvertTo-Json -Compress; return }
if (@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object { $_.TaskName -eq $taskName }).Count) {
    throw 'Task already exists; refusing replacement.'
}
if (Test-Path -LiteralPath $marker) { throw 'An uncertain attempt requires reconciliation.' }
$hostPath = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $hostPath -Argument ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ' + $encoded) -WorkingDirectory $bundle
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes($IntervalMinutes) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -Disable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $ApprovedUserSid -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
$verified = @(Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction Stop)
$verifiedSid = $null
if ($verified.Count -eq 1) {
    try {
        $actualUser = [string]$verified[0].Principal.UserId
        if ($actualUser -match '^S-1-') { $verifiedSid = (New-Object Security.Principal.SecurityIdentifier($actualUser)).Value }
        else { $verifiedSid = (New-Object Security.Principal.NTAccount($actualUser)).Translate([Security.Principal.SecurityIdentifier]).Value }
    } catch { $verifiedSid = $null }
}
if ($verified.Count -ne 1 -or $verified[0].Settings.Enabled -or
    [string]$verified[0].Settings.MultipleInstances -cne 'IgnoreNew' -or
    $verifiedSid -cne $ApprovedUserSid -or
    [string]$verified[0].Principal.LogonType -cne 'Interactive' -or
    [string]$verified[0].Principal.RunLevel -cne 'Limited' -or
    @($verified[0].Actions).Count -ne 1 -or
    [string]$verified[0].Actions[0].Execute -cne $hostPath -or
    [string]$verified[0].Actions[0].Arguments -cne [string]$action.Arguments -or
    [string]$verified[0].Actions[0].WorkingDirectory -cne $bundle -or
    @($verified[0].Triggers).Count -ne 1 -or
    [string]$verified[0].Triggers[0].Repetition.Interval -cne [string]$trigger.Repetition.Interval) {
    throw 'Task registration outcome requires manual reconciliation.'
}
$plan | ConvertTo-Json -Compress

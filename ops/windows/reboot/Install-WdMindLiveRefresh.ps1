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
    [Parameter(Mandatory)] [string] $ScratchRoot,
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
function Test-LiveConfigPolicy {
    param([object] $Config, [string] $Raw)
    $names = @($Config.PSObject.Properties.Name)
    if ([string]$Config.schema -ceq 'wd.q4-live-update-once.v1') {
        return ($names -notcontains 'p1_enabled')
    }
    $required = @('schema','route_path','route_sha256','plan_path','anchor_path','policy_path',
        'public_policy_path','baseline_path','baseline_sha256','manifest_sha256','p1_enabled')
    if ([string]$Config.schema -cne 'wd.q4-live-update-once.v2' -or $names.Count -ne $required.Count -or
        $Raw.Length -gt 65536 -or $Config.p1_enabled -isnot [bool] -or $Config.p1_enabled -cne $true) {
        return $false
    }
    # ConvertFrom-Json can collapse duplicate keys. V2 requires each literal
    # generated ASCII key once, including no escaped duplicate aliases.
    if ([regex]::Matches($Raw, '(?<!\\)"(?:[^"\\]|\\.)*"\s*:').Count -ne $required.Count) { return $false }
    foreach ($key in $required) {
        if ($names -cnotcontains $key -or
            [regex]::Matches($Raw, '(?<!\\)"' + [regex]::Escape($key) + '"\s*:').Count -ne 1) { return $false }
        if ($key -cne 'p1_enabled' -and ($Config.$key -isnot [string] -or [string]::IsNullOrEmpty($Config.$key))) {
            return $false
        }
    }
    foreach ($key in @('route_sha256','baseline_sha256','manifest_sha256')) {
        if ($Config.$key -cnotmatch '^[0-9a-f]{64}$') { return $false }
    }
    return $true
}
function Literal([string] $Value) { return "'" + $Value.Replace("'", "''") + "'" }
function Assert-LiveDisjoint([string] $Left, [string] $Right) {
    $a = [IO.Path]::GetFullPath($Left).TrimEnd('\')
    $b = [IO.Path]::GetFullPath($Right).TrimEnd('\')
    if ($a.Equals($b, [StringComparison]::OrdinalIgnoreCase) -or
        $a.StartsWith($b + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $b.StartsWith($a + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Scratch overlaps a protected path.' }
}

$bundle = Assert-LivePath $BundleRoot -Directory
$stateFile = Assert-LivePath $StatePath
$configFile = Assert-LivePath $ConfigPath
$scratch = Assert-LivePath $ScratchRoot -Directory
$manifestFile = Assert-LivePath (Join-Path $bundle 'deployment-manifest.json')
$bootstrapFile = Assert-LivePath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_live_update_bootstrap.py')
$module = Assert-LivePath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_live_update_once.py')
$jobHelper = Assert-LivePath (Join-Path $bundle 'WdQ4ProcessJob.ps1')
$manifestHash = $ExpectedManifestSha256.ToUpperInvariant()
$configHash = $ExpectedConfigSha256.ToUpperInvariant()
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne $ApprovedUserSid) { throw 'User SID mismatch.' }
if ((Get-FileHash -LiteralPath $manifestFile -Algorithm SHA256).Hash -cne $manifestHash -or
    (Get-FileHash -LiteralPath $configFile -Algorithm SHA256).Hash -cne $configHash) { throw 'Manifest or config pin mismatch.' }
$manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
$state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
$configRaw = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8
$config = $configRaw | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1 -or [string]$manifest.source_commit -cne $ExpectedCommit -or
    [string]$state.source_commit -cne $ExpectedCommit -or [string]$state.final_commit -cne $ExpectedCommit -or
    ([string]$state.manifest_sha256).ToUpperInvariant() -cne $manifestHash -or
    ([string]$state.final_manifest_sha256).ToUpperInvariant() -cne $manifestHash -or
    -not [string]::Equals([string]$state.active_bundle, $bundle, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-LiveConfigPolicy -Config $config -Raw $configRaw) -or
    ([string]$config.manifest_sha256).ToUpperInvariant() -cne $manifestHash) { throw 'State, commit or config does not bind the approved bundle.' }
foreach ($entry in @(@('tools-bootstrap/tools/wd_q4_live_update_bootstrap.py', $bootstrapFile),
                       @('tools-bootstrap/tools/wd_q4_live_update_once.py', $module),
                       @('WdQ4ProcessJob.ps1', $jobHelper))) {
    $pin = $manifest.files.PSObject.Properties[$entry[0]]
    if ($null -eq $pin -or (Get-FileHash -LiteralPath $entry[1] -Algorithm SHA256).Hash -cne ([string]$pin.Value).ToUpperInvariant()) {
        throw 'Packaged tool hash does not match the manifest.'
    }
}
$baselineFile = Assert-LivePath ([string]$config.baseline_path)
if ((Get-FileHash -LiteralPath $baselineFile -Algorithm SHA256).Hash -cne ([string]$config.baseline_sha256).ToUpperInvariant()) {
    throw 'Baseline descriptor pin mismatch.'
}
$baseline = Get-Content -LiteralPath $baselineFile -Raw -Encoding UTF8 | ConvertFrom-Json
$interpreter = Assert-LivePath ([string]$baseline.interpreter)
$fixedFile = Assert-LivePath ([string]$baseline.fixed_plan_path)
$runtimeManifest = Assert-LivePath ([string]$baseline.runtime_manifest_path)
$interpreterHash = ([string]$baseline.interpreter_sha256).ToUpperInvariant()
$fixedHash = ([string]$baseline.fixed_plan_sha256).ToUpperInvariant()
$runtimeHash = ([string]$baseline.runtime_manifest_sha256).ToUpperInvariant()
if ((Get-FileHash -LiteralPath $interpreter -Algorithm SHA256).Hash -cne $interpreterHash -or
    (Get-FileHash -LiteralPath $fixedFile -Algorithm SHA256).Hash -cne $fixedHash -or
    (Get-FileHash -LiteralPath $runtimeManifest -Algorithm SHA256).Hash -cne $runtimeHash) { throw 'Runtime tuple pin mismatch.' }
$fixed = Get-Content -LiteralPath $fixedFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (([string]$fixed.interpreter_sha256).ToUpperInvariant() -cne $interpreterHash -or
    ([string]$fixed.runtime_manifest_sha256).ToUpperInvariant() -cne $runtimeHash -or
    -not [string]::Equals([string]$fixed.runtime_manifest_path, $runtimeManifest, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Runtime tuple is inconsistent.'
}
$runtimeRoot = Assert-LivePath ([string]$fixed.runtime_root) -Directory
$routeFile = Assert-LivePath ([string]$config.route_path)
if ((Get-FileHash -LiteralPath $routeFile -Algorithm SHA256).Hash -cne ([string]$config.route_sha256).ToUpperInvariant()) {
    throw 'Route pin mismatch.'
}
$route = Get-Content -LiteralPath $routeFile -Raw -Encoding UTF8 | ConvertFrom-Json
$privateRoot = Assert-LivePath ([string]$route.private_state_root) -Directory
$checkpointRoot = Assert-LivePath ([string]$route.checkpoint_root) -Directory
$modelRoot = Assert-LivePath ([string]$route.trusted_model_root) -Directory
$sourceFile = Assert-LivePath ([string]$route.source)
$sidecarFile = Assert-LivePath ([string]$route.generation_path)
foreach ($protected in @($bundle, $runtimeRoot, $privateRoot, $checkpointRoot, $modelRoot,
        [IO.Path]::GetDirectoryName($interpreter), [IO.Path]::GetDirectoryName($sourceFile),
        [IO.Path]::GetDirectoryName($sidecarFile), [IO.Path]::GetDirectoryName($configFile),
        [IO.Path]::GetDirectoryName($baselineFile), [IO.Path]::GetDirectoryName($fixedFile),
        [IO.Path]::GetDirectoryName($runtimeManifest), [IO.Path]::GetDirectoryName($routeFile))) {
    Assert-LiveDisjoint $scratch $protected
}
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
    (Get-FileHash -LiteralPath %BASELINE% -Algorithm SHA256).Hash -cne %BASELINE_HASH% -or
    (Get-FileHash -LiteralPath %FIXED% -Algorithm SHA256).Hash -cne %FIXED_HASH% -or
    (Get-FileHash -LiteralPath %RUNTIME_MANIFEST% -Algorithm SHA256).Hash -cne %RUNTIME_HASH% -or
    (Get-FileHash -LiteralPath %PYTHON% -Algorithm SHA256).Hash -cne %PYTHON_HASH% -or
    (Get-FileHash -LiteralPath %BOOTSTRAP% -Algorithm SHA256).Hash -cne %BOOTSTRAP_HASH% -or
    (Get-FileHash -LiteralPath %JOB% -Algorithm SHA256).Hash -cne %JOB_HASH% -or
    (Test-Path -LiteralPath %MARKER%)) { exit 3 }
. %JOB%
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $bytes = New-Object byte[] 32; $random.GetBytes($bytes) } finally { $random.Dispose() }
$attempt = ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
$job = Invoke-WdQ4ProcessJob -Executable %PYTHON% -WorkingDirectory %BUNDLE% -ScratchRoot %SCRATCH% `
    -Arguments @('-I','-S','-B',%BOOTSTRAP%,'--config',%CONFIG%,'--sha256',%CONFIG_HASH_LOWER%,
                 '--manifest-sha256',%MANIFEST_HASH_LOWER%,'--host-attempt',$attempt) -InputBytes ([byte[]]@()) `
    -TimeoutSeconds 1700 -MaxOutputBytes 65536 -DetailedOutcome
if ($null -ne $job -and $job.Cleanup -ceq 'confirmed' -and (Test-Path -LiteralPath %MARKER%)) {
    try {
        # A completed owned Job is necessary but not sufficient to recover. This
        # receipt records only this host's observation, not writer identity or
        # power-loss durability. A crash before the flushed receipt is UNKNOWN.
        $private = %PRIVATE%
        $intentPath = Join-Path $private ('q4-live-update-intent-' + $attempt + '.json')
        $reservationPath = Join-Path $private ('q4-live-runner-reservation-' + $attempt + '.json')
        foreach ($file in @(%MARKER%, $intentPath, $reservationPath)) {
            $item = Get-Item -LiteralPath $file -Force -ErrorAction Stop
            if ($item.PSIsContainer -or $item.Length -le 0 -or $item.Length -gt 262144 -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'binding' }
        }
        $pending = Get-Content -LiteralPath %MARKER% -Raw -Encoding UTF8 | ConvertFrom-Json
        $intent = Get-Content -LiteralPath $intentPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $reservation = Get-Content -LiteralPath $reservationPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $intentHash = (Get-FileHash -LiteralPath $intentPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ([string]$pending.schema -cne 'wd.q4-live-update-pending.v1' -or
            [string]$intent.schema -cne 'wd.q4-live-update-intent.v1' -or
            [string]$reservation.schema -cne 'wd.q4-live-runner-reservation.v1' -or
            [string]$pending.attempt -cne $attempt -or [string]$intent.attempt -cne $attempt -or
            [string]$reservation.attempt -cne $attempt -or
            [string]$pending.config_sha256 -cne %CONFIG_HASH_LOWER% -or
            [string]$intent.config_sha256 -cne %CONFIG_HASH_LOWER% -or
            [string]$pending.previous_pointer_sha256 -cne [string]$intent.previous_pointer_sha256 -or
            [string]$pending.intent_sha256 -cne $intentHash -or
            [string]$reservation.intent_sha256 -cne $intentHash -or
            [string]$reservation.token -cnotmatch '^[0-9a-f]{64}$') { throw 'binding' }
        $jobReceipt = [ordered]@{schema='wd.q4-live-job-result.v1'; attempt=$attempt;
            config_sha256=%CONFIG_HASH_LOWER%; intent_sha256=$intentHash;
            marker_sha256=(Get-FileHash -LiteralPath %MARKER% -Algorithm SHA256).Hash.ToLowerInvariant();
            reservation_sha256=(Get-FileHash -LiteralPath $reservationPath -Algorithm SHA256).Hash.ToLowerInvariant();
            job_helper_sha256=%JOB_HASH_LOWER%; cleanup='confirmed'; child_outcome=[string]$job.Outcome;
            exit_code=if ($job.Outcome -ceq 'completed') { [int]$job.ExitCode } else { $null }}
        $receiptPath = Join-Path $private ('q4-live-job-result-' + $attempt + '.json')
        $encodedReceipt = [Text.Encoding]::UTF8.GetBytes(($jobReceipt | ConvertTo-Json -Compress))
        $stream = [IO.File]::Open($receiptPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($encodedReceipt, 0, $encodedReceipt.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    } catch { exit 3 }
}
if ($null -eq $job -or $job.Cleanup -cne 'confirmed' -or $job.Outcome -cne 'completed' -or $job.ExitCode -ne 0) { exit 3 }
try {
    $text = [Text.UTF8Encoding]::new($false, $true).GetString($job.Stdout)
    $receipt = $text | ConvertFrom-Json -ErrorAction Stop
    if ([string]$receipt.status -cnotin @('PUBLISHED','NO_CHANGE') -or
        [string]$receipt.pointer_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$receipt.receipt_sha256 -cnotmatch '^[0-9a-f]{64}$') { exit 3 }
    @{status=[string]$receipt.status; pointer_sha256=[string]$receipt.pointer_sha256;
      receipt_sha256=[string]$receipt.receipt_sha256; job_cleanup='confirmed'} | ConvertTo-Json -Compress
} catch { exit 3 }
exit 0
'@
$values = [ordered]@{'%STATE%'=$stateFile; '%COMMIT%'=$ExpectedCommit; '%BUNDLE%'=$bundle;
    '%MANIFEST%'=$manifestFile; '%MANIFEST_HASH%'=$manifestHash; '%CONFIG%'=$configFile; '%CONFIG_HASH%'=$configHash;
    '%CONFIG_HASH_LOWER%'=$configHash.ToLowerInvariant(); '%MANIFEST_HASH_LOWER%'=$manifestHash.ToLowerInvariant();
    '%BASELINE%'=$baselineFile; '%BASELINE_HASH%'=([string]$config.baseline_sha256).ToUpperInvariant();
    '%FIXED%'=$fixedFile; '%FIXED_HASH%'=$fixedHash; '%RUNTIME_MANIFEST%'=$runtimeManifest; '%RUNTIME_HASH%'=$runtimeHash;
    '%PYTHON%'=$interpreter; '%PYTHON_HASH%'=$interpreterHash; '%BOOTSTRAP%'=$bootstrapFile;
    '%BOOTSTRAP_HASH%'=(Get-FileHash -LiteralPath $bootstrapFile -Algorithm SHA256).Hash;
    '%JOB%'=$jobHelper; '%JOB_HASH%'=(Get-FileHash -LiteralPath $jobHelper -Algorithm SHA256).Hash;
    '%JOB_HASH_LOWER%'=(Get-FileHash -LiteralPath $jobHelper -Algorithm SHA256).Hash.ToLowerInvariant();
    '%SCRATCH%'=$scratch; '%MARKER%'=$marker; '%PRIVATE%'=$privateRoot}
foreach ($key in $values.Keys) { $bootstrap = $bootstrap.Replace($key, (Literal $values[$key])) }
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$taskName = 'WD-MindLiveUpdate'
$plan = [ordered]@{schema='wd.mind-live-refresh-plan.v1'; task=$taskName; registered=[bool]$Apply; enabled=$false;
    source_commit=$ExpectedCommit; manifest_sha256=$manifestHash; config_sha256=$configHash; user_sid=$ApprovedUserSid;
    interval_minutes=$IntervalMinutes; multiple_instances='IgnoreNew'; logon='Interactive'; run_level='Limited';
    pending='blocks_attempt_until_reconciled'; execution_limit='30 minutes'; process_tree_exit='NOT_PROVEN';
    job_containment='kill-on-close; owned Job only';
    python_pin_scope='python.exe only; stdlib and native DLLs not independently verified';
    supervisor='unchanged'; execution='NOT_RUN'}
if (-not $Apply) { $plan | ConvertTo-Json -Compress; return }
if (@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object { $_.TaskName -eq $taskName }).Count) {
    throw 'Task already exists; refusing replacement.'
}
if (Test-Path -LiteralPath $marker) { throw 'An uncertain attempt requires reconciliation.' }
$hostPath = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $hostPath -Argument ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ' + $encoded) -WorkingDirectory $bundle
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes($IntervalMinutes) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -Disable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
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
    [string]$verified[0].Settings.ExecutionTimeLimit -cne 'PT30M' -or
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

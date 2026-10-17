#requires -Version 5.1
<#
.SYNOPSIS
    Runs the Mind shadow producer once inside an owned kill-on-close job with one fixed deadline. Preparation code:
    no run is authorized, and native acceptance A1-A7 is NOT_RUN.

.DESCRIPTION
    Route B (Lead decision): the job type is the lane loop's own WdManagedTurnProcess. The adapter refuses a type or
    function that is already loaded (R1), reads the installed lane loop ONCE, verifies those bytes against the pinned
    deployment manifest, and imports exactly one function, Initialize-WdNativeTurnType, from the same bytes (R2). The
    lane loop and the launchers are unchanged.

    Time: one Stopwatch is the enforcement clock, started once and never reset; UTC is read once at launch and every
    *_utc value is that launch time plus the monotonic elapsed time. Stop() (TerminateJobObject) runs only at the
    1800 s deadline, after an unreadable job state or after a failed start record, followed by at most 30 s for the
    job to drain. completed needs a full drain before the deadline (root exit alone never counts), a positive
    exclusive reacquire and release of the live-dir lock, and no unknown value; stopped_at_deadline needs a Stop that
    returned, a confirmed drain and the lock reacquire; everything else is stop_unconfirmed.

    Records (D8 baseline) live in a new per-launch directory under the approved run root, outside the live dir, and
    each is published once (CreateNew, then a rename that never overwrites). The type creates the job, assigns the
    suspended root and resumes it inside its own constructor, so no record can sit between assignment and resume:
    run_intent is published before the create, run_start right after the constructor, controller_outcome at the end.
    After a controller death nothing more is written here; native A5 needs the independent observer witnesses.

    Design: RCO1 (A1-A7, the job reuse, R1, R2, the throwing-Stop rule), RCO2 (G1-G4 and the member witness), Lead
    (route, deadline, records, launch id, time and binding decisions); fable-5 wrote this file. There is no process
    kill by PID, and producer.lock is only opened here, never created or removed.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [string] $ExpectedSourceCommit,
    [Parameter(Mandatory)] [string] $ExpectedManifestSha256,
    [Parameter(Mandatory)] [string] $ExpectedAdapterSha256,
    [Parameter(Mandatory)] [string] $ApprovedRunRoot,
    [Parameter(Mandatory)] [string] $LiveDir,
    [Parameter(Mandatory)] [ValidateSet('limited', 'elevated', IgnoreCase = $false)] [string] $ApprovedTokenClass
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# R1 (RCO1): the lane loop's own guard returns early when the type exists, so an already-loaded type would run instead
# of the verified bytes. Refuse before anything is read.
if ('WdManagedTurnProcess' -as [type]) {
    throw 'refused: WdManagedTurnProcess is already loaded in this process (R1); start the adapter in a fresh process'
}
if (Get-Command -Name 'Initialize-WdNativeTurnType' -CommandType Function -ErrorAction SilentlyContinue) {
    throw 'refused: Initialize-WdNativeTurnType is already defined in this session (R1)'
}

$DeadlineSeconds = 1800
$StopConfirmationWaitSeconds = 30
$PollMilliseconds = 1000
# The producer's Windows live-dir lock byte: sandbox/mind/mind_capture_producer.py LOCK_BYTE = 2 ** 30.
$LockByte = 1073741824
$StatePath = 'C:\Python\WD_REBOOT_STATE_CURRENT.json'
$BundleStore = 'C:\Python\wd-reboot-bundles'
$WrapperName = 'Invoke-WdBridgePython.ps1'
$Schema = 'wd.mind-shadow-run-outcome.v1'

function Assert-WdShadowValue {
    param([string] $Name, [string] $Value, [string] $Pattern)
    if ($Value -match '[<>]' -or $Value -match 'SYN_') {
        throw "refused: $Name is a synthetic placeholder, never a runtime input"
    }
    if ($Value -cnotmatch $Pattern) { throw "refused: $Name does not match $Pattern" }
}

function Read-WdShadowFileSnapshot {
    # One read of the whole file: the hash and the text come from the same bytes.
    param([string] $Path)
    $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($stream.Length -gt 16777216) { throw "refused: $Path is larger than 16 MiB" }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $got = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($got -le 0) { throw "refused: $Path ended early while it was read" }
            $offset += $got
        }
        if ($stream.ReadByte() -ne -1) { throw "refused: $Path grew while it was read" }
    } finally { $stream.Dispose() }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '') } finally { $sha.Dispose() }
    $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    return [pscustomobject]@{ Hash = $hash; Text = $text }
}

function Get-WdShadowUtc {
    # Derived time: the one launch UTC reading plus monotonic elapsed seconds; never a second wall-clock reading.
    param([double] $ElapsedSeconds)
    return $script:LaunchUtc.AddSeconds($ElapsedSeconds).ToString('o')
}

function Add-WdShadowUnknown {
    param([System.Collections.IList] $Unknown, [string] $Field, [string] $Reason)
    [void]$Unknown.Add([ordered]@{ field = $Field; reason = $Reason })
}

function Publish-WdShadowRecord {
    # Atomic per record: write a new temporary file (CreateNew), then rename it once; an existing record or a leftover
    # temporary file refuses, so nothing is ever overwritten. No universal crash-safety claim (D8).
    param([string] $Directory, [string] $Name, $Record)
    $final = Join-Path $Directory ($Name + '.json')
    $temporary = Join-Path $Directory ('.' + $Name + '.json.tmp')
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes((($Record | ConvertTo-Json -Depth 8) + "`n"))
    $stream = New-Object IO.FileStream($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    [IO.File]::Move($temporary, $final)
}

function Test-WdShadowLiveDirLock {
    # G2 (RCO2): ActiveProcesses 0 proves only that the members are gone; the live-dir lock must be free as well. The
    # file is opened, never created, truncated, written or removed.
    param([string] $Path)
    $result = [ordered]@{ attempted = $true; acquired = $false; released = $false; error = $null }
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
        try {
            $stream.Lock($LockByte, 1)
            $result.acquired = $true
            $stream.Unlock($LockByte, 1)
            $result.released = $true
        } finally { $stream.Dispose() }
    } catch {
        $result.error = $_.Exception.GetType().FullName
    }
    return $result
}

function Get-WdShadowDescendants {
    param($Managed, [System.Collections.IList] $Unknown, [string] $Field)
    try {
        return @($Managed.LiveDescendants | ForEach-Object { [ordered]@{ pid = $_.Pid; image = $_.ImagePath } })
    } catch {
        Add-WdShadowUnknown $Unknown $Field $_.Exception.GetType().FullName
        return 'UNKNOWN'
    }
}

function Resolve-WdShadowOutcome {
    # G3: root exit alone never completes; G2: the lock must be reacquired; G4: any unknown value is never positive.
    param([bool] $Drained, [bool] $StopCalled, [bool] $StopOk, [bool] $StopConfirmed, [bool] $LockAcquired,
        [System.Collections.IList] $Unknown)
    if ($Unknown.Count -eq 0 -and $Drained -and -not $StopCalled -and $LockAcquired) {
        return 'completed'
    }
    if ($Unknown.Count -eq 0 -and $StopCalled -and $StopOk -and $StopConfirmed -and $LockAcquired) {
        return 'stopped_at_deadline'
    }
    return 'stop_unconfirmed'
}

Assert-WdShadowValue -Name 'ExpectedSourceCommit' -Value $ExpectedSourceCommit -Pattern '^[0-9a-f]{40}$'
Assert-WdShadowValue -Name 'ExpectedManifestSha256' -Value $ExpectedManifestSha256 -Pattern '^[0-9A-F]{64}$'
Assert-WdShadowValue -Name 'ExpectedAdapterSha256' -Value $ExpectedAdapterSha256 -Pattern '^[0-9A-F]{64}$'
Assert-WdShadowValue -Name 'ApprovedRunRoot' -Value $ApprovedRunRoot -Pattern '^[A-Za-z]:\\'
Assert-WdShadowValue -Name 'LiveDir' -Value $LiveDir -Pattern '^[A-Za-z]:\\'

# D5: the token class is an explicit approved input; the actual token must match it, nothing is inferred.
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$tokenClass = 'limited'
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { $tokenClass = 'elevated' }
if ($tokenClass -cne $ApprovedTokenClass) {
    throw "refused: the actual token class $tokenClass is not the approved $ApprovedTokenClass"
}

$adapterPath = $PSCommandPath
$adapterHash = (Read-WdShadowFileSnapshot -Path $adapterPath).Hash
if ($adapterHash -cne $ExpectedAdapterSha256) { throw 'refused: this adapter file is not the expected one' }

# The external manifest anchor and the three pins must all name the expected generation; absent or foreign = STOP.
$state = (Read-WdShadowFileSnapshot -Path $StatePath).Text | ConvertFrom-Json
$bundle = Join-Path $BundleStore $ExpectedSourceCommit
$stateMatches = (
    [string]$state.source_commit -ceq $ExpectedSourceCommit -and
    [string]$state.final_commit -ceq $ExpectedSourceCommit -and
    [string]$state.manifest_sha256 -ceq $ExpectedManifestSha256 -and
    [string]$state.final_manifest_sha256 -ceq $ExpectedManifestSha256 -and
    [string]::Equals([string]$state.active_bundle, $bundle, [StringComparison]::OrdinalIgnoreCase)
)
if (-not $stateMatches) {
    throw 'refused: the reboot state pointer does not name the expected commit, manifest and bundle'
}
if ([string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH -cne $ExpectedManifestSha256) {
    throw 'refused: WD_REBOOT_EXPECTED_MANIFEST_HASH is absent or foreign'
}
$manifestSnapshot = Read-WdShadowFileSnapshot -Path (Join-Path $bundle 'deployment-manifest.json')
if ($manifestSnapshot.Hash -cne $ExpectedManifestSha256) {
    throw 'refused: the deployment manifest does not hash to the expected manifest sha256'
}
$manifest = $manifestSnapshot.Text | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1 -or [string]$manifest.source_commit -cne $ExpectedSourceCommit) {
    throw 'refused: the deployment manifest is not schema 1 for the expected commit'
}
$bundlePrefix = [IO.Path]::GetFullPath($bundle).TrimEnd('\') + '\'
$pins = [ordered]@{
    WD_BRIDGE_CODE_ROOT = [string]$env:WD_BRIDGE_CODE_ROOT
    WD_BRIDGE_BIN = [string]$env:WD_BRIDGE_BIN
    WD_BRIDGE_PYTHON_WRAPPER = [string]$env:WD_BRIDGE_PYTHON_WRAPPER
}
foreach ($name in @($pins.Keys)) {
    $value = $pins[$name]
    if (-not $value -or -not [IO.Path]::IsPathRooted($value) -or -not (Test-Path -LiteralPath $value) -or
        -not [IO.Path]::GetFullPath($value).StartsWith($bundlePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refused: the pin $name is absent or foreign to the expected bundle"
    }
}
if ([IO.Path]::GetFileName($pins['WD_BRIDGE_PYTHON_WRAPPER']) -cne $WrapperName) {
    throw "refused: WD_BRIDGE_PYTHON_WRAPPER is not the bundle wrapper $WrapperName"
}

# R2 (RCO1): one read, one hash, one parse of the same bytes, exactly one imported function.
$loopPath = Join-Path $bundle 'Invoke-WdLaneTurnLoop.ps1'
$loopPin = $manifest.files.PSObject.Properties['Invoke-WdLaneTurnLoop.ps1']
if ($null -eq $loopPin -or [string]$loopPin.Value -cnotmatch '^[0-9A-Fa-f]{64}$') {
    throw 'refused: the manifest has no sha256 pin for Invoke-WdLaneTurnLoop.ps1'
}
$loopBytes = [IO.File]::ReadAllBytes($loopPath)
$sha = [Security.Cryptography.SHA256]::Create()
try { $loopHash = [BitConverter]::ToString($sha.ComputeHash($loopBytes)).Replace('-', '') } finally { $sha.Dispose() }
if ($loopHash -cne ([string]$loopPin.Value).ToUpperInvariant()) {
    throw 'refused: the lane loop bytes do not match the manifest'
}
$loopText = (New-Object Text.UTF8Encoding($false, $true)).GetString($loopBytes)
if ($loopText.Length -gt 0 -and $loopText[0] -eq [char]0xFEFF) { $loopText = $loopText.Substring(1) }
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($loopText, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'refused: the verified lane loop does not parse' }
$matched = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq 'Initialize-WdNativeTurnType'
})
if ($matched.Count -ne 1) { throw "refused: expected one Initialize-WdNativeTurnType, found $($matched.Count)" }
. ([scriptblock]::Create($matched[0].Extent.Text))

# Fresh local state only; results outside the live dir.
$runRootFull = [IO.Path]::GetFullPath($ApprovedRunRoot).TrimEnd('\')
$liveFull = [IO.Path]::GetFullPath($LiveDir).TrimEnd('\')
if (-not (Test-Path -LiteralPath $runRootFull -PathType Container)) { throw 'refused: the run root is not a directory' }
$ignore = [StringComparison]::OrdinalIgnoreCase
if ([string]::Equals($runRootFull, $liveFull, $ignore) -or $liveFull.StartsWith($runRootFull + '\', $ignore) -or
    $runRootFull.StartsWith($liveFull + '\', $ignore)) {
    throw 'refused: the run root and the live dir must not contain each other'
}
if ((Test-Path -LiteralPath $liveFull) -and @(Get-ChildItem -LiteralPath $liveFull -Force).Count -gt 0) {
    throw 'refused: the live dir is not fresh (fresh state only)'
}
$drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($liveFull))
if ($drive.DriveType -ne [IO.DriveType]::Fixed -or $drive.DriveFormat -cne 'NTFS') {
    throw 'refused: the live dir is not on a local NTFS disk'
}

# A fresh launch id chosen before the create: a future launch id, never a claimed native conversation id.
$launchId = [guid]::NewGuid().ToString('D')
$runDir = Join-Path $runRootFull $launchId
[void](New-Item -ItemType Directory -Path $runDir)
$clock = [Diagnostics.Stopwatch]::StartNew()
$script:LaunchUtc = [DateTime]::UtcNow
$binding = [ordered]@{
    run_id = $launchId
    source_commit = $ExpectedSourceCommit
    manifest_sha256 = $ExpectedManifestSha256
    adapter = [ordered]@{ path = $adapterPath; sha256 = $adapterHash }
    token_class = $tokenClass
    root = [ordered]@{ pid = 'UNKNOWN'; creation_time_utc = 'UNKNOWN' }
    deadline = [ordered]@{
        duration_s = $DeadlineSeconds
        absolute_utc = Get-WdShadowUtc -ElapsedSeconds $DeadlineSeconds
        launch_utc = Get-WdShadowUtc -ElapsedSeconds 0
        clock = 'Stopwatch (monotonic); every *_utc value is launch_utc plus elapsed seconds'
    }
}
Publish-WdShadowRecord -Directory $runDir -Name 'run_intent' -Record ([ordered]@{
    schema = $Schema; record = 'run_intent'; binding = $binding; live_dir = $liveFull
    monotonic_elapsed_s = $clock.Elapsed.TotalSeconds
    written_utc = Get-WdShadowUtc -ElapsedSeconds $clock.Elapsed.TotalSeconds
})

$inputPath = Join-Path $runDir 'root.stdin.empty'
(New-Object IO.FileStream($inputPath, [IO.FileMode]::CreateNew)).Dispose()
$hostExe = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
$arguments = [string[]]@('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
    $env:WD_BRIDGE_PYTHON_WRAPPER, 'sandbox/mind/mind_capture_producer.py', '--src', $env:WD_BRIDGE_CODE_ROOT,
    '--bin', $env:WD_BRIDGE_BIN, '--live-dir', $liveFull)
$unknown = New-Object System.Collections.ArrayList
Initialize-WdNativeTurnType
$managed = $null
$drained = $false
$stopCalled = $false
$stopOk = $false
$stopConfirmed = $false
$stopError = $null
$stopAt = $null
$rootExitAt = $null
$drainAt = $null
$before = 'UNKNOWN'
$after = 'UNKNOWN'
$lock = [ordered]@{ attempted = $false; acquired = $false; released = $false; error = $null }
try {
    try {
        $managed = [WdManagedTurnProcess]::new($hostExe, $arguments, $env:WD_BRIDGE_CODE_ROOT, $inputPath,
            (Join-Path $runDir 'root.stdout.log'), (Join-Path $runDir 'root.stderr.log'))
    } catch {
        # The type terminates and disposes its own partial job on a failed create, assignment or resume.
        Add-WdShadowUnknown $unknown 'create' $_.Exception.GetType().FullName
    }
    $forceStop = $false
    if ($null -ne $managed) {
        $binding.root['pid'] = $managed.Pid
        try {
            # The open process handle inside the type keeps this PID from being reused while it is read.
            $started = [Diagnostics.Process]::GetProcessById($managed.Pid).StartTime
            $binding.root['creation_time_utc'] = $started.ToUniversalTime().ToString('o')
        } catch {
            Add-WdShadowUnknown $unknown 'binding.root.creation_time_utc' $_.Exception.GetType().FullName
        }
        try {
            Publish-WdShadowRecord -Directory $runDir -Name 'run_start' -Record ([ordered]@{
                schema = $Schema; record = 'run_start'; binding = $binding; expected_members = @()
                monotonic_elapsed_s = $clock.Elapsed.TotalSeconds
                written_utc = Get-WdShadowUtc -ElapsedSeconds $clock.Elapsed.TotalSeconds
            })
        } catch {
            Add-WdShadowUnknown $unknown 'run_start' $_.Exception.GetType().FullName
            $forceStop = $true
        }
        $deadlineMs = [long]$DeadlineSeconds * 1000
        while (-not $forceStop -and $clock.ElapsedMilliseconds -lt $deadlineMs) {
            $slice = [int][Math]::Max(1, [Math]::Min($PollMilliseconds, $deadlineMs - $clock.ElapsedMilliseconds))
            try {
                if ($null -eq $rootExitAt) {
                    if ($managed.Wait($slice)) { $rootExitAt = $clock.Elapsed.TotalSeconds }
                } else {
                    Start-Sleep -Milliseconds ([Math]::Min(250, $slice))
                }
                if ($null -ne $rootExitAt -and $managed.ActiveProcesses -eq 0) {
                    $drained = $true
                    $drainAt = $clock.Elapsed.TotalSeconds
                    break
                }
            } catch {
                Add-WdShadowUnknown $unknown 'job_state' $_.Exception.GetType().FullName
                break
            }
        }
        if (-not $drained) {
            $before = Get-WdShadowDescendants -Managed $managed -Unknown $unknown -Field 'live_descendants_before_stop'
            $stopCalled = $true
            $stopAt = $clock.Elapsed.TotalSeconds
            try {
                $managed.Stop()
                $stopOk = $true
            } catch {
                $stopError = $_.Exception.GetType().FullName
                Add-WdShadowUnknown $unknown 'stop' $stopError
            }
            $confirmUntil = $clock.ElapsedMilliseconds + [long]$StopConfirmationWaitSeconds * 1000
            while ($clock.ElapsedMilliseconds -lt $confirmUntil) {
                try {
                    if ($managed.ActiveProcesses -eq 0) { $stopConfirmed = $true; break }
                } catch {
                    Add-WdShadowUnknown $unknown 'job_state_after_stop' $_.Exception.GetType().FullName
                    break
                }
                Start-Sleep -Milliseconds 250
            }
            $after = Get-WdShadowDescendants -Managed $managed -Unknown $unknown -Field 'live_descendants_after_stop'
        } else {
            $before = @()
            $after = @()
        }
        $lock = Test-WdShadowLiveDirLock -Path (Join-Path $liveFull 'producer.lock')
    }
} finally {
    if ($null -ne $managed) { $managed.Dispose() }
}
if ($null -eq $rootExitAt) { Add-WdShadowUnknown $unknown 'root_exit_utc' 'root exit not observed' }
if (-not $stopConfirmed -and -not $drained) { Add-WdShadowUnknown $unknown 'job_drained' 'drain not confirmed' }
$lockOk = [bool]$lock.acquired -and [bool]$lock.released
$outcome = Resolve-WdShadowOutcome -Drained $drained -StopCalled $stopCalled -StopOk $stopOk `
    -StopConfirmed $stopConfirmed -LockAcquired $lockOk -Unknown $unknown
$rootExitUtc = 'UNKNOWN'
if ($null -ne $rootExitAt) { $rootExitUtc = Get-WdShadowUtc -ElapsedSeconds $rootExitAt }
$drainUtc = 'UNKNOWN'
if ($null -ne $drainAt) { $drainUtc = Get-WdShadowUtc -ElapsedSeconds $drainAt }
$stopUtc = $null
if ($null -ne $stopAt) { $stopUtc = Get-WdShadowUtc -ElapsedSeconds $stopAt }
Publish-WdShadowRecord -Directory $runDir -Name 'controller_outcome' -Record ([ordered]@{
    schema = $Schema; record = 'controller_outcome'; binding = $binding; outcome = $outcome
    root_exit_utc = $rootExitUtc
    job_drained = [ordered]@{ drained = $drained; active_processes_zero_utc = $drainUtc }
    stop = [ordered]@{ called = $stopCalled; called_utc = $stopUtc; returned_ok = $stopOk; error = $stopError
        confirmed = $stopConfirmed }
    stop_confirmation_wait_s = $StopConfirmationWaitSeconds
    live_descendants_before_stop = $before
    live_descendants_after_stop = $after
    lock_reacquire = $lock
    monotonic_elapsed_s = $clock.Elapsed.TotalSeconds
    unknown_reasons = @($unknown)
    written_utc = Get-WdShadowUtc -ElapsedSeconds $clock.Elapsed.TotalSeconds
})
if ($outcome -ceq 'stop_unconfirmed') { exit 3 }
exit 0

#requires -Version 5.1
<#
.SYNOPSIS
    Runs the Mind shadow producer once inside an owned kill-on-close job with one fixed deadline, a C: floor and an
    operator off switch. Preparation code: no run is authorized, and native acceptance A1-A7 is NOT_RUN.

.DESCRIPTION
    Route B (Lead decision): the job type is the lane loop's own WdManagedTurnProcess. The adapter refuses a type or
    function that is already loaded (R1), reads the installed lane loop ONCE, verifies those bytes against the pinned
    deployment manifest, and imports exactly one function, Initialize-WdNativeTurnType, from the same bytes (R2). The
    lane loop and the launchers are unchanged.

    Time: one Stopwatch is the enforcement clock, started once and never reset; UTC is read once at launch and every
    *_utc value is that launch time plus the monotonic elapsed time. Stop() (TerminateJobObject) runs only at the
    1800 s deadline, after a guard hit (see Guards), after an unreadable job state or after a failed start record,
    followed by at most 30 s for the job to drain. completed needs a full drain first observed strictly before the
    deadline (Tools P2: the clock is read after that observation and the deadline is checked again after the
    guards; a first observation at or after it takes the Stop path), with root exit alone never counting, an
    observed root exit, a positive exclusive reacquire and release of the live-dir lock, and no unknown value;
    stopped_at_deadline needs a Stop that returned, a confirmed drain, the root observed exited on the managed
    handle after that drain and the lock reacquire (D1); everything else is stop_unconfirmed. This bounds when a
    drain is credited; it does not preempt a blocking call.

    Records (D8 baseline) live in a new per-launch directory under the approved run root, outside the live dir, and
    each is published once (CreateNew, then a rename that never overwrites). The input file and the verified type are
    prepared first; run_intent is published immediately before the single constructor call, run_start right after
    it, controller_outcome at the end (D2). A missing later record is UNKNOWN and never proves that execution began
    or stopped; no directory or crash durability is claimed. After a controller death nothing more is written here;
    native A5 needs the independent observer witnesses.

    Root exit code (cp4, record schema v2): the root runs the wrapper through -Command with every data argument as a
    single-quoted literal and a literal exit-forwarding shim, because -File turns the wrapper's published code into
    0. controller_outcome.root_exit_code is the managed ExitCode read after a positively observed root exit and
    before Dispose, else UNKNOWN (never 0). It is raw evidence only: the outcome stays a containment result and never
    means that the producer succeeded.

    Run id metadata (cp5): the root's own command first refuses an inherited WD_MIND_SHADOW_RUN_ID (exit 254, never
    reused), then sets it to the actual launch id as an escaped literal before the wrapper runs. It is metadata
    transport, not authentication: anything in the job can read or change it, and it proves no member identity or
    lifetime. This process's environment is only read (an existing value is refused before run_intent), never set.

    Root witness (Lead decision v1): after the run id assignment and before the unchanged wrapper, the root's own fixed
    prologue opens root.token in the fresh run directory (CreateNew, FileShare.None, not inheritable), writes and
    flushes a run-bound token, then marks its SafeFileHandle invalid so the raw handle is released only by OS process
    teardown. Only then does it publish one no-overwrite root_member_start record (run id, source commit, manifest
    sha256, authority none); any error exits 253 before the wrapper. This witnesses the job root only, never members,
    death or PASS, and it is not the first instruction of the root. Framework internals stay UNKNOWN from source;
    native parity on PS 5.1 and 7 is NOT_RUN. A missing, partial or foreign record is UNKNOWN or FAIL, never proof
    that no process ran.

    Guards (Lead Source-GO 2026-10-21, after the operator's shadow-run decision): a C: floor and an operator off
    switch, record schema v3. The floor and its comparison are Lead's deployment disk budget rule (542e26be):
    3221225472 bytes (3 GiB), always on C:\ and on the volumes of the run root and the live dir. Before anything is
    written, each volume must hold the floor plus ExpectedPeakAdditionalBytes, a reviewed positive bound from the
    caller (unknown refuses; it is not a measured footprint or a reservation); while the job runs, each must keep the
    floor itself, and a failed measurement counts as below it. The off switch is any entry named mind-shadow.off in
    the approved run root: present before the start, nothing is started; appearing later, the job is stopped through
    the deadline's Stop path. Only its existence is read, never its content, through File.GetAttributes (never the
    PowerShell provider, which may report a denied read as not found: suspected from static code, not observed),
    and only a positive file-not-found answer under an existing run root counts as off. Both guards are read once
    per loop slice, so a guard acts within about
    one second plus the stop wait, never instantly; nothing here reserves space or guards against other writers. A
    confirmed guard stop is stopped_by_off_switch or stopped_at_disk_floor under the stopped_at_deadline rules; a
    guard error is UNKNOWN, so stop_unconfirmed. Follow-up (Lead, after Root's static finding at 386517ac): the
    volume and NTFS checks read textual drive roots, so neither the run root nor the live dir may pass through a
    reparse point (a junction, a symbolic link or a volume mount point). An existing component that is one, or
    that cannot be read, refuses before anything is written; one that appears later counts as a floor hit. Only a
    file-not-found or directory-not-found answer skips a component that does not exist yet. A drive letter made by
    subst or another DOS device mapping is invisible to this walk: reparse points in its target path are not seen,
    and the volume and NTFS checks measure whatever volume Windows resolves the drive letter to; that this is the
    expected target volume is not observed. Nothing is pinned, so a path change between two readings stays possible:
    no whole-directory or race safety is claimed.

    Identity (T1): the actual mandatory integrity label of this token (its S-1-16 SID as whoami.exe reports it) must
    equal the approved level, and the token user SID must equal the explicitly supplied approved user SID. Group
    membership is never used, and nothing is defaulted from the caller. whoami.exe comes from the OS system directory
    ([Environment]::SystemDirectory), never from an environment variable, and must be an absolute regular file with no
    reparse point on it or on any parent directory; anything else is refused.

    Source-preparation limits (Lead D8 decision): L1, the type resumes the root inside its constructor, so run_start
    follows the resume; this does not meet a before-resume native-evidence need and waives nothing. L2, a root that
    exits before its creation time is read leaves that identity UNKNOWN; the observation time is never used instead.

    PR1915 follow-ups (Lead Source-GO, after the bot, RCO1 and RCO2 static findings): B1, WD_BRIDGE_PYTHON_WRAPPER
    must be exactly the bundle-root wrapper, with no reparse point on its path, and its bytes must hash to the
    manifest pin before anything is written; the root reads the file again later, so a change in between stays
    possible and no OS isolation is claimed. B2, a volume root as the run root or the live dir is refused before its
    separator could be trimmed into a drive-relative path.

    Design: RCO1 (A1-A7, the job reuse, R1, R2, the throwing-Stop rule, the D1/D2/T1 audit, the T1 trust-path finding),
    RCO2 (G1-G4 and the member witness), Tools (the -File exit diagnostic), Root (the PS7 exit characterization and
    the schema/caller inputs), Lead (route, deadline, records, launch id, time, binding, cp2 to cp5 decisions, the
    cp5 child run id transport and command prefix design, the root witness contract, and the guard Source-GO with
    its 542e26be disk rule, and the reparse follow-up after Root's static finding); fable-5 wrote this file.
    There is no process kill by PID, and producer.lock is only opened here, never created or removed.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [string] $ExpectedSourceCommit,
    [Parameter(Mandatory)] [string] $ExpectedManifestSha256,
    [Parameter(Mandatory)] [string] $ExpectedAdapterSha256,
    [Parameter(Mandatory)] [string] $ApprovedRunRoot,
    [Parameter(Mandatory)] [string] $LiveDir,
    [Parameter(Mandatory)] [string] $ApprovedUserSid,
    [Parameter(Mandatory)] [ValidateSet('medium', 'high', IgnoreCase = $false)] [string] $ApprovedIntegrityLevel,
    # Guards: the reviewed positive additional-write bound for this run; unknown (0 or less) refuses before any write.
    [Parameter(Mandatory)] [long] $ExpectedPeakAdditionalBytes
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
# Guards (Lead Source-GO 2026-10-21): Lead's deployment disk floor (542e26be), never lowered or omitted, and the one
# fixed name of the operator off switch in the approved run root.
$DiskFloorBytes = [long]3221225472
$OffSwitchName = 'mind-shadow.off'
$StatePath = 'C:\Python\WD_REBOOT_STATE_CURRENT.json'
$BundleStore = 'C:\Python\wd-reboot-bundles'
$WrapperName = 'Invoke-WdBridgePython.ps1'
# Record schema v3 (guards): binding.guard, controller_outcome.guard_stop and the two guard stop outcomes.
$Schema = 'wd.mind-shadow-run-outcome.v3'
# cp4 (Tools PS5 and Root PS7 finding): -File turns the wrapper's published exit code into 0, so the root runs the
# wrapper through -Command and this literal shim (never expanded here) forwards it; no published code exits 255.
$RootExitShim = '; if ($LASTEXITCODE -is [int]) { exit $LASTEXITCODE }; exit 255'
# cp5 (Lead): the child's own command refuses an inherited run id variable, then sets it from the actual launch id.
$RootRunIdPrefix = 'if ($null -ne $env:WD_MIND_SHADOW_RUN_ID) { exit 254 }; $env:WD_MIND_SHADOW_RUN_ID = '
# Root witness (Lead decision v1): the child's own fixed prologue; data arrive only as escaped $wdRoot* literals.
$RootWitnessPrologue = '; try { $wdRootTokenStream = [IO.FileStream]::new($wdRootTokenPath, ' +
    '[IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None); ' +
    '$wdRootBytes = [Convert]::FromBase64String($wdRootTokenData); ' +
    '$wdRootTokenStream.Write($wdRootBytes, 0, $wdRootBytes.Length); $wdRootTokenStream.Flush($true); ' +
    '$wdRootTokenStream.SafeFileHandle.SetHandleAsInvalid(); ' +
    '$wdRootStartStream = [IO.FileStream]::new($wdRootStartTemporary, ' +
    '[IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None); ' +
    'try { $wdRootBytes = [Convert]::FromBase64String($wdRootStartData); ' +
    '$wdRootStartStream.Write($wdRootBytes, 0, $wdRootBytes.Length); $wdRootStartStream.Flush($true) } ' +
    'finally { $wdRootStartStream.Dispose() }; [IO.File]::Move($wdRootStartTemporary, $wdRootStartPath) } ' +
    'catch { exit 253 }'

function Assert-WdShadowValue {
    param([string] $Name, [string] $Value, [string] $Pattern)
    if ($Value -match '[<>]' -or $Value -match 'SYN_') {
        throw "refused: $Name is a synthetic placeholder, never a runtime input"
    }
    if ($Value -cnotmatch $Pattern) { throw "refused: $Name does not match $Pattern" }
}

function ConvertTo-WdShadowCommandLiteral {
    # One data argument as a PowerShell single-quoted literal for the root -Command text: the engine's own escaper
    # doubles every single-quote form; a double quote or a control character (never in a Windows path) is refused.
    param([string] $Value)
    if ($Value -match '["\x00-\x1F]') {
        throw 'refused: a root command argument holds a double quote or a control character'
    }
    return "'" + [Management.Automation.Language.CodeGeneration]::EscapeSingleQuotedStringContent($Value) + "'"
}

function ConvertTo-WdShadowBase64Json {
    # Root witness data (Lead decision v1): compressed JSON of fixed ASCII keys and checked printable ASCII values,
    # then Base64, so the child receives data only, never code.
    param($Record)
    $json = $Record | ConvertTo-Json -Compress -Depth 4
    if ($json -cnotmatch '^[\x20-\x7E]+$') { throw 'refused: root witness data must be printable ASCII' }
    return [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($json + "`n"))
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

function Get-WdShadowDiskShortfall {
    # Lead's deployment disk budget rule (542e26be): every volume must be a ready fixed local volume holding the floor
    # plus the bound; exactly floor + bound passes, one byte less does not, and the subtraction follows the floor check
    # so it cannot overflow. $null when every volume passes, else the first shortfall as text; a failed measurement is
    # a shortfall (fail closed). It may over-refuse; it reserves nothing and does not guard against other writers.
    param([string[]] $Roots, [long] $AdditionalBytes)
    foreach ($root in $Roots) {
        try {
            $drive = New-Object IO.DriveInfo($root)
            if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) {
                return "$root is not a ready fixed local volume"
            }
            $free = [long]$drive.AvailableFreeSpace
        } catch {
            return "$root is unmeasured ($($_.Exception.GetType().FullName))"
        }
        if ($free -lt $DiskFloorBytes -or $AdditionalBytes -gt ($free - $DiskFloorBytes)) {
            return "$root free=$free, additional=$AdditionalBytes, floor=$DiskFloorBytes"
        }
    }
    return $null
}

function Test-WdShadowOffSwitch {
    # The operator off switch: any entry with the fixed name, read for its existence only (never its content). Only a
    # positive not-found answer under an existing run root counts as off; anything else counts as on (fail closed).
    # Follow-up (Lead, after RCO1's static F1): read with File.GetAttributes, never the PowerShell provider, which may
    # report a denied read as not found (suspected from static code, not observed); only FileNotFound counts as off
    # (DirectoryNotFound, denied or unknown: on).
    param([string] $Path)
    try {
        if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($Path))) { return $true }
        [void][IO.File]::GetAttributes($Path)
        return $true
    } catch [IO.FileNotFoundException] {
        return $false
    } catch {
        return $true
    }
}

function Get-WdShadowReparseAncestor {
    # Follow-up (Lead, after Root's static finding at 386517ac): a textual drive root measures the backing volume
    # only when no existing component of the path is a reparse point (a junction, a symbolic link or a volume mount
    # point). Walks from the path up to its root, skipping a component that does not exist yet; $null when every
    # existing component is ordinary, else the first reparse or unreadable component as text (fail closed). It may
    # over-refuse (cloud file placeholders are reparse points too) and pins nothing, so a later change stays possible.
    # Follow-up (Lead, after RCO1's static F1): read with File.GetAttributes, never the PowerShell provider; only
    # FileNotFound or DirectoryNotFound skips a component, and a denied or unknown answer refuses. A subst or other
    # DOS device drive letter is invisible here: reparse points in its target path are not seen (not observed).
    param([string] $Path)
    for ($current = $Path; -not [string]::IsNullOrEmpty($current); $current = [IO.Path]::GetDirectoryName($current)) {
        try {
            $attributes = [IO.File]::GetAttributes($current)
        } catch [IO.FileNotFoundException] {
            continue
        } catch [IO.DirectoryNotFoundException] {
            continue
        } catch {
            return "$current is unreadable ($($_.Exception.GetBaseException().GetType().FullName))"
        }
        if ($attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { return "$current is a reparse point" }
    }
    return $null
}

function Resolve-WdShadowDirectoryPath {
    # PR1915 follow-up B2 (bot P1, RCO1 P1-a, RCO2 B2; Lead Source-GO): a volume root is refused BEFORE its trailing
    # separator is trimmed, because a trimmed volume root is drive-relative and names the current directory of that
    # drive, so the existence, reparse and containment checks and the records would follow that directory. Any other
    # full path keeps its root and loses only its trailing separators; the later guards are unchanged.
    param([string] $Name, [string] $Value)
    $full = [IO.Path]::GetFullPath($Value)
    $root = [IO.Path]::GetPathRoot($full)
    if ([string]::IsNullOrEmpty($root) -or [string]::Equals($full, $root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refused: $Name is a volume root; name a directory below it"
    }
    $trimmed = $full.TrimEnd('\')
    if ($trimmed.Length -le $root.Length -or -not $trimmed.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refused: $Name does not name a directory below its volume root"
    }
    return $trimmed
}

function Get-WdShadowGuardStop {
    # One reading of both guards: the off switch first, then the floor itself (the bound is spent while the job runs).
    # A reparse point that appears in a guarded path counts as a floor hit: the textual roots would then no longer
    # establish the backing volume (fail closed).
    param([string] $OffSwitchPath, [string[]] $Roots, [string[]] $Paths)
    if (Test-WdShadowOffSwitch -Path $OffSwitchPath) { return 'off_switch' }
    foreach ($guarded in $Paths) { if ($null -ne (Get-WdShadowReparseAncestor -Path $guarded)) { return 'disk_floor' } }
    if ($null -ne (Get-WdShadowDiskShortfall -Roots $Roots -AdditionalBytes 0)) { return 'disk_floor' }
    return $null
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
    # G3: root exit alone never completes, and a drain needs an observed root exit as well (D1); G2: the lock must be
    # reacquired; G4: any unknown value is never positive. A guard stop needs exactly what the deadline stop needs.
    param([bool] $Drained, [bool] $RootExited, [bool] $StopCalled, [bool] $StopOk, [bool] $StopConfirmed,
        [bool] $LockAcquired, [string] $GuardStop, [System.Collections.IList] $Unknown)
    if ($Unknown.Count -eq 0 -and $Drained -and $RootExited -and -not $StopCalled -and $LockAcquired -and
        -not $GuardStop) {
        return 'completed'
    }
    if ($Unknown.Count -eq 0 -and $StopCalled -and $StopOk -and $StopConfirmed -and $RootExited -and $LockAcquired -and
        $GuardStop -ceq 'off_switch') {
        return 'stopped_by_off_switch'
    }
    if ($Unknown.Count -eq 0 -and $StopCalled -and $StopOk -and $StopConfirmed -and $RootExited -and $LockAcquired -and
        $GuardStop -ceq 'disk_floor') {
        return 'stopped_at_disk_floor'
    }
    if ($Unknown.Count -eq 0 -and $StopCalled -and $StopOk -and $StopConfirmed -and $RootExited -and $LockAcquired -and
        -not $GuardStop) {
        return 'stopped_at_deadline'
    }
    return 'stop_unconfirmed'
}

Assert-WdShadowValue -Name 'ExpectedSourceCommit' -Value $ExpectedSourceCommit -Pattern '^[0-9a-f]{40}$'
Assert-WdShadowValue -Name 'ExpectedManifestSha256' -Value $ExpectedManifestSha256 -Pattern '^[0-9A-F]{64}$'
Assert-WdShadowValue -Name 'ExpectedAdapterSha256' -Value $ExpectedAdapterSha256 -Pattern '^[0-9A-F]{64}$'
Assert-WdShadowValue -Name 'ApprovedRunRoot' -Value $ApprovedRunRoot -Pattern '^[A-Za-z]:\\'
Assert-WdShadowValue -Name 'LiveDir' -Value $LiveDir -Pattern '^[A-Za-z]:\\'
Assert-WdShadowValue -Name 'ApprovedUserSid' -Value $ApprovedUserSid -Pattern '^S-1-5-21-\d+-\d+-\d+-\d+$'

# T1: classify by the actual mandatory integrity label (its S-1-16 SID, language independent) and bind the token user
# to the explicitly approved user SID; group membership is never used and nothing is defaulted from the caller.
# Trust path (cp3, RCO1): an environment variable can point whoami.exe at another file that fakes the label, so the path
# comes from the OS system directory ([Environment]::SystemDirectory, GetSystemDirectory) and must be an absolute
# regular file with no reparse point on the file or on any parent directory before it runs.
$systemDir = [Environment]::SystemDirectory
if (-not $systemDir -or $systemDir -cnotmatch '^[A-Za-z]:\\[^<>:"/|?*]+$' -or
    [IO.Path]::GetFullPath($systemDir) -cne $systemDir) {
    throw 'refused: the OS system directory is not an absolute local path'
}
$whoami = [IO.Path]::Combine($systemDir, 'whoami.exe')
$whoamiInfo = New-Object IO.FileInfo($whoami)
if (-not $whoamiInfo.Exists -or
    (($whoamiInfo.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw 'refused: whoami.exe in the OS system directory is not a regular file without a reparse point'
}
for ($dir = $whoamiInfo.Directory; $null -ne $dir; $dir = $dir.Parent) {
    if (($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "refused: $($dir.FullName) on the whoami.exe path is a reparse point"
    }
}
$global:LASTEXITCODE = 0
$groupRows = @(& $whoami '/groups' '/fo' 'csv' '/nh')
if ($LASTEXITCODE -ne 0) { throw 'refused: the token groups could not be read' }
$labels = @($groupRows | ConvertFrom-Csv -Header 'name', 'type', 'sid', 'attributes' |
    Where-Object { [string]$_.sid -cmatch '^S-1-16-(\d+)$' })
if ($labels.Count -ne 1) { throw "refused: expected one mandatory integrity label, found $($labels.Count)" }
$rid = ''
if ([string]$labels[0].sid -cmatch '^S-1-16-(\d+)$') { $rid = $Matches[1] }
$levels = @{ '8192' = 'medium'; '12288' = 'high' }
$integrity = $levels[$rid]
if (-not $integrity) { throw "refused: the mandatory integrity level S-1-16-$rid is not supported" }
if ($integrity -cne $ApprovedIntegrityLevel) {
    throw "refused: the actual integrity level $integrity is not the approved $ApprovedIntegrityLevel"
}
$actualUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($actualUserSid -cne $ApprovedUserSid) { throw 'refused: the token user SID is not the approved user SID' }

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
# PR1915 follow-up B1 (bot P1, RCO1 P1-b, RCO2 B1; Lead Source-GO): the root executes this file, so containment and the
# name are not enough. The pin must be exactly the bundle-root wrapper, no component of its path may be a reparse
# point, and its bytes must hash to the manifest's own pin, as the lane loop's do. Residual limit: the root reads the
# file again later, so a change between this check and that read stays possible; no OS isolation is claimed.
$wrapperPath = [IO.Path]::GetFullPath((Join-Path $bundle $WrapperName))
$wrapperSelected = [IO.Path]::GetFullPath($pins['WD_BRIDGE_PYTHON_WRAPPER'])
if (-not [string]::Equals($wrapperSelected, $wrapperPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "refused: WD_BRIDGE_PYTHON_WRAPPER is not exactly the bundle-root wrapper $wrapperPath"
}
$wrapperReparse = Get-WdShadowReparseAncestor -Path $wrapperPath
if ($null -ne $wrapperReparse) { throw "refused: $wrapperReparse on the wrapper path" }
$wrapperPin = $manifest.files.PSObject.Properties[$WrapperName]
if ($null -eq $wrapperPin -or [string]$wrapperPin.Value -cnotmatch '^[0-9A-Fa-f]{64}$') {
    throw "refused: the manifest has no sha256 pin for $WrapperName"
}
if ((Read-WdShadowFileSnapshot -Path $wrapperPath).Hash -cne ([string]$wrapperPin.Value).ToUpperInvariant()) {
    throw 'refused: the wrapper bytes do not match the manifest'
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
# PR1915 follow-up B2: a volume root is refused before its separator could be trimmed (Resolve-WdShadowDirectoryPath).
$runRootFull = Resolve-WdShadowDirectoryPath -Name 'ApprovedRunRoot' -Value $ApprovedRunRoot
$liveFull = Resolve-WdShadowDirectoryPath -Name 'LiveDir' -Value $LiveDir
if (-not (Test-Path -LiteralPath $runRootFull -PathType Container)) { throw 'refused: the run root is not a directory' }
# Follow-up (Lead): the volume and NTFS checks below read textual roots, so neither path may pass through a reparse
# point or a volume mount point; refused before anything is written.
$guardPaths = @($runRootFull, $liveFull)
foreach ($guarded in $guardPaths) {
    $reparse = Get-WdShadowReparseAncestor -Path $guarded
    if ($null -ne $reparse) { throw "refused: $reparse, so its textual root would not measure its backing volume" }
}
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
# Guards before anything is written: the off switch must be off, the bound known and positive, and every volume must
# hold the floor plus the bound.
$offSwitchPath = Join-Path $runRootFull $OffSwitchName
if (Test-WdShadowOffSwitch -Path $offSwitchPath) {
    throw "refused: the operator off switch $offSwitchPath is on (or unreadable); nothing was started"
}
if ($ExpectedPeakAdditionalBytes -le 0) {
    throw 'refused: ExpectedPeakAdditionalBytes is unknown or not positive; supply the reviewed bound'
}
$guardRoots = @(@('C:\', [IO.Path]::GetPathRoot($runRootFull), [IO.Path]::GetPathRoot($liveFull)) |
    ForEach-Object { $_.ToUpperInvariant() } | Select-Object -Unique)
$shortfall = Get-WdShadowDiskShortfall -Roots $guardRoots -AdditionalBytes $ExpectedPeakAdditionalBytes
if ($null -ne $shortfall) { throw "refused: the disk budget is not met before the start ($shortfall)" }

# cp5: the root inherits this process's environment (Root's static reading of the type), so a run id variable here
# would conflict with the launch id; it is refused, never reused, and this environment is never changed.
if ($null -ne [Environment]::GetEnvironmentVariable('WD_MIND_SHADOW_RUN_ID', 'Process')) {
    throw 'refused: the run id variable already exists in the environment the root inherits; it is never reused'
}

# A fresh launch id chosen before the create: a future launch id, never a claimed native conversation id.
$launchId = [guid]::NewGuid().ToString('D')
$runDir = Join-Path $runRootFull $launchId
[void](New-Item -ItemType Directory -Path $runDir)

# D2: every fallible preparation (input file, host, arguments, verified type) comes before run_intent.
$inputPath = Join-Path $runDir 'root.stdin.empty'
(New-Object IO.FileStream($inputPath, [IO.FileMode]::CreateNew)).Dispose()
$hostExe = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
# Root witness (Lead decision v1): fixed names under the fresh validated run directory, never caller-selected; the
# data are built here before run_intent (D2) and reach the child only as escaped literals.
$rootTokenPath = Join-Path $runDir 'root.token'
$rootStartPath = Join-Path $runDir 'root_member_start.json'
$rootStartTemporary = Join-Path $runDir '.root_member_start.json.tmp'
$rootTokenData = ConvertTo-WdShadowBase64Json -Record ([ordered]@{
    schema = 'wd.mind-shadow-root-token.v1'; run_id = $launchId; authority = 'none'
})
$rootStartData = ConvertTo-WdShadowBase64Json -Record ([ordered]@{
    schema = 'wd.mind-shadow-root-member-start.v1'; record = 'root_member_start'; authority = 'none'
    binding = [ordered]@{
        run_id = $launchId; source_commit = $ExpectedSourceCommit; manifest_sha256 = $ExpectedManifestSha256
    }
    token = 'root.token'
})
$rootWitness = '; $wdRootTokenPath = ' + (ConvertTo-WdShadowCommandLiteral -Value $rootTokenPath) +
    '; $wdRootTokenData = ' + (ConvertTo-WdShadowCommandLiteral -Value $rootTokenData) +
    '; $wdRootStartTemporary = ' + (ConvertTo-WdShadowCommandLiteral -Value $rootStartTemporary) +
    '; $wdRootStartPath = ' + (ConvertTo-WdShadowCommandLiteral -Value $rootStartPath) +
    '; $wdRootStartData = ' + (ConvertTo-WdShadowCommandLiteral -Value $rootStartData) + $RootWitnessPrologue
$rootCommand = $RootRunIdPrefix + (ConvertTo-WdShadowCommandLiteral -Value $launchId) + $rootWitness + '; & ' + ((@(
    $env:WD_BRIDGE_PYTHON_WRAPPER, 'sandbox/mind/mind_capture_producer.py', '--src', $env:WD_BRIDGE_CODE_ROOT,
    '--bin', $env:WD_BRIDGE_BIN, '--live-dir', $liveFull) |
    ForEach-Object { ConvertTo-WdShadowCommandLiteral -Value $_ }) -join ' ') + $RootExitShim
$arguments = [string[]]@('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', $rootCommand)
Initialize-WdNativeTurnType
$managedType = 'WdManagedTurnProcess' -as [type]
$constructorTypes = [type[]]@([string], [string[]], [string], [string], [string], [string])
if ($null -eq $managedType -or $null -eq $managedType.GetConstructor($constructorTypes) -or
    $null -eq $managedType.GetMethod('Stop') -or $null -eq $managedType.GetMethod('Wait') -or
    $null -eq $managedType.GetProperty('ActiveProcesses') -or $null -eq $managedType.GetProperty('ExitCode')) {
    throw 'refused: the verified lane loop did not initialize the expected WdManagedTurnProcess type'
}
$unknown = New-Object System.Collections.ArrayList
$managed = $null
$drained = $false
$stopCalled = $false
$stopOk = $false
$stopConfirmed = $false
$stopError = $null
$stopAt = $null
$rootExitAt = $null
$rootExitCode = 'UNKNOWN'
$drainAt = $null
$before = 'UNKNOWN'
$after = 'UNKNOWN'
$guardStop = $null
$lock = [ordered]@{ attempted = $false; acquired = $false; released = $false; error = $null }
$clock = [Diagnostics.Stopwatch]::StartNew()
$script:LaunchUtc = [DateTime]::UtcNow
$binding = [ordered]@{
    run_id = $launchId
    source_commit = $ExpectedSourceCommit
    manifest_sha256 = $ExpectedManifestSha256
    adapter = [ordered]@{ path = $adapterPath; sha256 = $adapterHash }
    identity = [ordered]@{ integrity_level = $integrity; user_sid_bound = $true }
    root = [ordered]@{ pid = 'UNKNOWN'; creation_time_utc = 'UNKNOWN' }
    deadline = [ordered]@{
        duration_s = $DeadlineSeconds
        absolute_utc = Get-WdShadowUtc -ElapsedSeconds $DeadlineSeconds
        launch_utc = Get-WdShadowUtc -ElapsedSeconds 0
        clock = 'Stopwatch (monotonic); every *_utc value is launch_utc plus elapsed seconds'
    }
    guard = [ordered]@{
        disk_floor_bytes = $DiskFloorBytes; expected_peak_additional_bytes = $ExpectedPeakAdditionalBytes
        volumes = @($guardRoots); off_switch = $offSwitchPath
    }
}
$intent = [ordered]@{
    schema = $Schema; record = 'run_intent'; binding = $binding; live_dir = $liveFull
    monotonic_elapsed_s = $clock.Elapsed.TotalSeconds
    written_utc = Get-WdShadowUtc -ElapsedSeconds $clock.Elapsed.TotalSeconds
}
try {
    Publish-WdShadowRecord -Directory $runDir -Name 'run_intent' -Record $intent
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
            # Guards: one reading per slice before the wait; a hit leaves the job undrained, so the Stop below runs.
            try {
                $guardStop = Get-WdShadowGuardStop -OffSwitchPath $offSwitchPath -Roots $guardRoots -Paths $guardPaths
            } catch {
                $guardStop = 'guard_error'
                Add-WdShadowUnknown $unknown 'guard' $_.Exception.GetType().FullName
            }
            if ($null -ne $guardStop) { break }
            # Tools P2 (late drain): the guards may have used up the time; never wait or credit a drain at or after
            # the deadline.
            if ($clock.ElapsedMilliseconds -ge $deadlineMs) { break }
            $slice = [int][Math]::Max(1, [Math]::Min($PollMilliseconds, $deadlineMs - $clock.ElapsedMilliseconds))
            try {
                if ($null -eq $rootExitAt) {
                    if ($managed.Wait($slice)) { $rootExitAt = $clock.Elapsed.TotalSeconds }
                } else {
                    Start-Sleep -Milliseconds ([Math]::Min(250, $slice))
                }
                if ($null -ne $rootExitAt -and $managed.ActiveProcesses -eq 0) {
                    # Tools P2: completed needs the drain observed strictly before the deadline, by one clock
                    # reading taken after the observation; a later first observation leaves the job undrained, so
                    # the Stop below runs.
                    $observed = $clock.Elapsed
                    if ($observed.TotalMilliseconds -lt $deadlineMs) {
                        $drained = $true
                        $drainAt = $observed.TotalSeconds
                    }
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
            if ($stopConfirmed) {
                $drainAt = $clock.Elapsed.TotalSeconds
                # D1: the confirmed drain must agree with an actual root-exit observation on the same managed handle.
                try {
                    $rootGone = $managed.Wait(0)
                } catch {
                    $rootGone = $null
                    Add-WdShadowUnknown $unknown 'root_exit_utc' $_.Exception.GetType().FullName
                }
                if ($rootGone -eq $true -and $null -eq $rootExitAt) { $rootExitAt = $clock.Elapsed.TotalSeconds }
                if ($rootGone -eq $false) {
                    Add-WdShadowUnknown $unknown 'root_exit_utc' 'the root has not exited after a confirmed job drain'
                }
            }
            $after = Get-WdShadowDescendants -Managed $managed -Unknown $unknown -Field 'live_descendants_after_stop'
        } else {
            $before = @()
            $after = @()
        }
        if ($drained -or $stopConfirmed) {
            $lock = Test-WdShadowLiveDirLock -Path (Join-Path $liveFull 'producer.lock')
        } else {
            Add-WdShadowUnknown $unknown 'lock_reacquire' 'not attempted without a positively observed job drain'
        }
        # cp4: the raw root exit code, read only after a positively observed root exit and before Dispose. A failed
        # query stays UNKNOWN (never 0); the containment outcome never reads it and it never means producer success.
        if ($null -ne $rootExitAt) {
            try {
                $rootExitCode = $managed.ExitCode
            } catch {
                Add-WdShadowUnknown $unknown 'root_exit_code' $_.Exception.GetType().FullName
            }
        }
    }
} finally {
    if ($null -ne $managed) { $managed.Dispose() }
}
$rootExited = $null -ne $rootExitAt
$jobDrained = $drained -or $stopConfirmed
if (-not $rootExited -and -not @($unknown | Where-Object { $_['field'] -ceq 'root_exit_utc' }).Count) {
    Add-WdShadowUnknown $unknown 'root_exit_utc' 'root exit not observed'
}
if (-not $jobDrained) { Add-WdShadowUnknown $unknown 'job_drained' 'drain not confirmed' }
$lockOk = [bool]$lock.acquired -and [bool]$lock.released
$outcome = Resolve-WdShadowOutcome -Drained $drained -RootExited $rootExited -StopCalled $stopCalled `
    -StopOk $stopOk -StopConfirmed $stopConfirmed -LockAcquired $lockOk -GuardStop $guardStop -Unknown $unknown
$rootExitUtc = 'UNKNOWN'
if ($rootExited) { $rootExitUtc = Get-WdShadowUtc -ElapsedSeconds $rootExitAt }
$drainUtc = 'UNKNOWN'
if ($null -ne $drainAt) { $drainUtc = Get-WdShadowUtc -ElapsedSeconds $drainAt }
$stopUtc = $null
if ($null -ne $stopAt) { $stopUtc = Get-WdShadowUtc -ElapsedSeconds $stopAt }
Publish-WdShadowRecord -Directory $runDir -Name 'controller_outcome' -Record ([ordered]@{
    schema = $Schema; record = 'controller_outcome'; binding = $binding; outcome = $outcome
    guard_stop = $guardStop
    root_exit_utc = $rootExitUtc
    root_exit_code = $rootExitCode
    job_drained = [ordered]@{ drained = $jobDrained; after_stop = $stopConfirmed
        active_processes_zero_utc = $drainUtc }
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

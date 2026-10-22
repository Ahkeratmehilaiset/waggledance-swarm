#requires -Version 5.1
<#
.SYNOPSIS
    The one synthetic lifetime member of the opt-in native acceptance lifetime drivers (A1, A2, A3, A5, A6). It
    refuses by default and is never collected or run by CI.

.DESCRIPTION
    Lead's v7 GO (2026-10-22T20:06:21Z). An approved executor's lifetime driver starts this script as the root
    member of an owned job; each member starts the members the fixed tree names for its sub-case and holds its
    lifetime witness until it ends. It writes only into a fresh run-bound witness directory
    (<.codex-audit>\<run folder>\<launch id>) that the driver created with a members.registration file naming exactly
    the preregistered members of the sub-case; any other name, run, sub-case or directory is refused before any file
    is written. The witness is <member>.witness, created with CreateNew, holding one start line "<launch id>
    <member>" and held with FileShare.None until the member ends (the OS releases it then, also on a kill). The A6
    root also holds the producer.lock of its witness directory the same way. No bridge, data, network, provider or
    producer is touched; the explicit opt-in (the switch and WD_MIND_SHADOW_SYNTHETIC_MEMBER=opt-in) is an accident
    guard, never runtime authority.

    fable-5 wrote this file; the member witness is RCO2's D7 example (Lead's decision).
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [switch] $OptInSyntheticMember,
    [string] $LaunchId,
    [string] $Member,
    [string] $Subcase,
    [string] $WitnessDirectory,
    [int] $LifetimeSeconds
)
if (-not $OptInSyntheticMember -or [string]$env:WD_MIND_SHADOW_SYNTHETIC_MEMBER -cne 'opt-in') {
    Write-Error 'refused: the synthetic member runs only on an explicit opt-in by an approved executor'
    exit 3
}
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-WdShadowLifetimeMembers {
    # The preregistered synthetic members of each lifetime sub-case, root first; the member fixture holds the same
    # table and refuses any other name, sub-case or tree.
    return [ordered]@{
        'A1/deadline' = @('root')
        'A1/early_root_exit_orphan' = @('root', 'orphan')
        'A1/pipe_holding_grandchild' = @('root', 'child', 'pipe_grandchild')
        'A1/off_switch_during_run' = @('root')
        'A1/disk_floor_hit' = @('root')
        'A1/single_confirmed_stop' = @('root')
        'A2/survivors' = @('root', 'child', 'grandchild', 'late_child')
        'A3/outer_no_limits' = @('root')
        'A3/outer_kill_on_close' = @('root')
        'A3/planned_launch_context' = @('root')
        'A5/controller_death' = @('root', 'child', 'grandchild')
        'A6/inherited_handles' = @('root')
    }
}

function Get-WdShadowMemberChildren {
    # The fixed tree: the members this member starts in its sub-case (none for any other pair).
    param([string] $Subcase, [string] $Member)
    switch -CaseSensitive ($Subcase + '|' + $Member) {
        'A1/early_root_exit_orphan|root' { return [string[]]@('orphan') }
        'A1/pipe_holding_grandchild|root' { return [string[]]@('child') }
        'A1/pipe_holding_grandchild|child' { return [string[]]@('pipe_grandchild') }
        'A2/survivors|root' { return [string[]]@('child') }
        'A2/survivors|child' { return [string[]]@('grandchild', 'late_child') }
        'A5/controller_death|root' { return [string[]]@('child') }
        'A5/controller_death|child' { return [string[]]@('grandchild') }
    }
    return [string[]]@()
}

function Test-WdShadowMemberRun {
    # The member's run, refused unless it is exactly preregistered: a known sub-case, one of its members, a lowercase
    # GUID launch id, a bounded lifetime, and an existing witness directory named by that launch id inside a run folder
    # directly in a .codex-audit folder, whose members.registration lists exactly the sub-case's members for this
    # launch. Returns the full witness directory.
    param([string] $LaunchId, [string] $Member, [string] $Subcase, [string] $WitnessDirectory, [int] $LifetimeSeconds)
    $registry = Get-WdShadowLifetimeMembers
    if (-not $registry.Contains($Subcase)) { throw 'refused: unknown lifetime sub-case' }
    $members = [string[]]@($registry[$Subcase])
    if ($members -cnotcontains $Member) { throw 'refused: the member is not preregistered for this sub-case' }
    if ($LaunchId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') {
        throw 'refused: the launch id is not a lowercase GUID'
    }
    if ($LifetimeSeconds -lt 1 -or $LifetimeSeconds -gt 86400) { throw 'refused: the lifetime is not 1 to 86400 s' }
    if ($WitnessDirectory -cnotmatch '^[\x21-\x7E][\x20-\x7E]*\z' -or $WitnessDirectory.Contains('"') -or
        -not [IO.Path]::IsPathRooted($WitnessDirectory)) {
        throw 'refused: the witness directory is not a rooted printable ASCII path'
    }
    $directory = [IO.Path]::GetFullPath($WitnessDirectory).TrimEnd('\')
    $runFolder = [IO.Path]::GetDirectoryName($directory)
    if ([IO.Path]::GetFileName($directory) -cne $LaunchId -or
        [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($runFolder)) -cne '.codex-audit') {
        throw 'refused: the witness directory is not <.codex-audit>\<run folder>\<launch id>'
    }
    if (-not [IO.Directory]::Exists($directory)) { throw 'refused: the witness directory does not exist' }
    $registration = $directory + '\members.registration'
    if (-not [IO.File]::Exists($registration)) { throw 'refused: the run has no members.registration' }
    $wanted = (@($members | ForEach-Object { $LaunchId + ' ' + $_ }) -join "`n") + "`n"
    if ([IO.File]::ReadAllText($registration) -cne $wanted) {
        throw 'refused: members.registration is not exactly the preregistered members of this run'
    }
    return $directory
}

function Open-WdShadowMemberWitness {
    # The member's lifetime witness: created once (CreateNew), one start line, held with FileShare.None until the
    # member ends. An existing witness is refused, never reused.
    param([string] $Directory, [string] $LaunchId, [string] $Member)
    $path = $Directory + '\' + $Member + '.witness'
    try {
        $stream = New-Object IO.FileStream($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
            [IO.FileShare]::None)
    } catch {
        throw 'refused: the member witness exists already or cannot be created'
    }
    $bytes = [Text.Encoding]::ASCII.GetBytes($LaunchId + ' ' + $Member + "`n")
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush($true)
    return $stream
}

function Open-WdShadowMemberLock {
    # The A6 root's stand-in for the producer lock: the witness directory's producer.lock, held with FileShare.None
    # until the member ends.
    param([string] $Directory)
    return New-Object IO.FileStream(($Directory + '\producer.lock'), [IO.FileMode]::CreateNew,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
}

function Start-WdShadowMemberProcess {
    # The real start of one child member (an approved executor's opt-in run only): this host and this script with the
    # same opt-in, run and sub-case and the child's name; no shell, so the child inherits this member's standard
    # handles (the pipe-holding grandchild keeps the root's output pipe open) and stays in the owned job. The process
    # object is released at once; nothing is waited for or killed here.
    param([string] $Subcase, [string] $Child, [string] $Directory, [string] $LaunchId, [int] $LifetimeSeconds)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    $info.Arguments = '-NoProfile -NonInteractive -File "' + $PSCommandPath + '" -OptInSyntheticMember -LaunchId ' +
        $LaunchId + ' -Member ' + $Child + ' -Subcase ' + $Subcase + ' -WitnessDirectory "' + $Directory +
        '" -LifetimeSeconds ' + $LifetimeSeconds
    $info.UseShellExecute = $false
    $process = [Diagnostics.Process]::Start($info)
    $process.Dispose()
}

$directory = Test-WdShadowMemberRun -LaunchId $LaunchId -Member $Member -Subcase $Subcase `
    -WitnessDirectory $WitnessDirectory -LifetimeSeconds $LifetimeSeconds
$witness = Open-WdShadowMemberWitness -Directory $directory -LaunchId $LaunchId -Member $Member
$lock = $null
try {
    if ($Subcase -ceq 'A6/inherited_handles') { $lock = Open-WdShadowMemberLock -Directory $directory }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    foreach ($child in (Get-WdShadowMemberChildren -Subcase $Subcase -Member $Member)) {
        if ($child -ceq 'late_child') {
            # Started near the driver's deadline Stop (the member lifetime is the deadline plus 60 s).
            $late = [Math]::Max(0, $LifetimeSeconds - 60 - [int]$clock.Elapsed.TotalSeconds)
            [Threading.Thread]::Sleep([int]($late * 1000))
        }
        Start-WdShadowMemberProcess -Subcase $Subcase -Child $child -Directory $directory -LaunchId $LaunchId `
            -LifetimeSeconds $LifetimeSeconds
    }
    if ($Subcase -ceq 'A1/early_root_exit_orphan' -and $Member -ceq 'root') {
        # The root ends early, but only once the orphan has its witness (at most 30 s).
        $orphan = $directory + '\orphan.witness'
        while (-not [IO.File]::Exists($orphan) -and $clock.Elapsed.TotalSeconds -lt 30) {
            [Threading.Thread]::Sleep(100)
        }
    } else {
        $rest = [Math]::Max(0, $LifetimeSeconds - [int]$clock.Elapsed.TotalSeconds)
        [Threading.Thread]::Sleep([int]($rest * 1000))
    }
} finally {
    if ($null -ne $lock) { $lock.Dispose() }
    $witness.Dispose()
}
exit 0

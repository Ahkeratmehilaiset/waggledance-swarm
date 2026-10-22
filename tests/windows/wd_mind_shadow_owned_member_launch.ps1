# Synthetic mind-shadow owned-member launch helpers (Lead's v10 GO, 2026-10-22T21:22:07Z). Test and harness Source
# only: this file defines functions and nothing else; running it directly refuses (exit 3). Neither function starts a
# process, runs the member, touches a job or a native handle, or changes any environment, and neither is authority:
# a future approved caller must still do the raw file, ancestry and admission binding and bind the ACTUAL executed
# argv and command hash to its approved run; any real use needs its own separate runtime decision.
#
# Get-WdShadowFixedMemberLaunchPlan is a PURE plan builder: from explicit, validated inputs it returns one versioned
# data object with the bound host, member, run and sub-case and the exact argv
# (-NoProfile, -NonInteractive, -Command, <fixed literal command>). The fixed command verifies the pinned member bytes
# (exit 4 on mismatch), sets WD_MIND_SHADOW_SYNTHETIC_MEMBER=opt-in inside the child PowerShell only and invokes ONLY
# the literal member path with the fixed switch and arguments (the form of Root's Get-WdA5MemberCommand). Every
# literal is single-quoted with apostrophes doubled, so nothing is evaluated in the parent. The member is reopened
# by path after its hash check: a same-user file-reopen TOCTOU remains, this is not OS isolation.
#
# New-WdShadowOwnedOutputObserver returns an explicit opt-in closure bound to ONE caller-owned managed object, one
# approved evidence directory with the fixed root.stdout.log and root.stderr.log, a finite positive byte bound (at
# most 8 MiB) and the C: floor. Each observation reads both actual lengths and the free space; a missing, unreadable,
# reparse or non-file log, a total over the bound, the floor or any arithmetic doubt requests Stop on that owned
# object ONCE (never a process or PID kill, never a retry after a failed stop) and the trigger stays the reported
# status. Evidence is never truncated or deleted. A within-budget result is observed bytes only, never native PASS;
# growth between observations and OS blocking remain: it is not a hard quota, a guaranteed bound or preemption. A
# future caller must observe at every owned lifetime poll, wait and witness seam and keep its ordinary cleanup and
# drain after a stop.

function Get-WdShadowOwnedLaunchMembers {
    # The fixed lifetime sub-cases the member accepts (the same names as the harness and member registry); the plan
    # launches only their root member.
    return @('A1/deadline', 'A1/early_root_exit_orphan', 'A1/pipe_holding_grandchild', 'A1/off_switch_during_run',
        'A1/disk_floor_hit', 'A1/single_confirmed_stop', 'A2/survivors', 'A3/outer_no_limits', 'A3/outer_kill_on_close',
        'A3/planned_launch_context', 'A5/controller_death', 'A6/inherited_handles')
}

function Test-WdShadowOwnedLaunchPath {
    # A full normalized printable ASCII C:\ path with no double quote, control character or trailing separator.
    param($Path, [string] $What)
    if ($Path -isnot [string] -or $Path -cnotmatch '^C:\\[\x20-\x7E]+\z' -or $Path.Contains('"') -or
        $Path.EndsWith('\') -or [IO.Path]::GetFullPath($Path) -cne $Path) {
        throw "refused: $What is not a full normalized printable ASCII C:\ path"
    }
}

function ConvertTo-WdShadowOwnedLaunchLiteral {
    # One PowerShell single-quoted literal: apostrophes doubled, never evaluated.
    param([string] $Text)
    return "'" + $Text.Replace("'", "''") + "'"
}

function Get-WdShadowFixedMemberLaunchPlan {
    param($HostPath, $MemberPath, $WitnessDirectory, $LaunchId, $Subcase, $LifetimeSeconds, $ExpectedMemberSha256)
    Test-WdShadowOwnedLaunchPath $HostPath 'the host path'
    Test-WdShadowOwnedLaunchPath $MemberPath 'the member path'
    Test-WdShadowOwnedLaunchPath $WitnessDirectory 'the witness directory'
    if ($LaunchId -isnot [string] -or
        $LaunchId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') {
        throw 'refused: the launch id is not a lowercase GUID'
    }
    if ([IO.Path]::GetFileName($WitnessDirectory) -cne $LaunchId -or
        [IO.Path]::GetFileName([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($WitnessDirectory))) -cne
        '.codex-audit') {
        throw 'refused: the witness directory is not <.codex-audit>\<run>\<launch id>'
    }
    if ($Subcase -isnot [string] -or @(Get-WdShadowOwnedLaunchMembers) -cnotcontains $Subcase) {
        throw 'refused: the sub-case is not a fixed lifetime sub-case'
    }
    if (($LifetimeSeconds -isnot [int] -and $LifetimeSeconds -isnot [long]) -or $LifetimeSeconds -lt 1 -or
        $LifetimeSeconds -gt 86400) { throw 'refused: the lifetime is not an integer from 1 to 86400 seconds' }
    if ($ExpectedMemberSha256 -isnot [string] -or $ExpectedMemberSha256 -cnotmatch '^[0-9A-F]{64}\z') {
        throw 'refused: the member pin is not an uppercase SHA-256'
    }
    $life = ([long]$LifetimeSeconds).ToString([Globalization.CultureInfo]::InvariantCulture)
    $member = ConvertTo-WdShadowOwnedLaunchLiteral $MemberPath
    $command = '$ErrorActionPreference = ''Stop''; ' +
        '$wdMemberBytes = [IO.File]::ReadAllBytes(' + $member + '); ' +
        '$wdMemberSha = [Security.Cryptography.SHA256]::Create(); ' +
        '$wdMemberHash = -join @($wdMemberSha.ComputeHash($wdMemberBytes) | ForEach-Object { $_.ToString(''X2'') }); ' +
        '$wdMemberSha.Dispose(); ' +
        'if ($wdMemberHash -cne ' + (ConvertTo-WdShadowOwnedLaunchLiteral $ExpectedMemberSha256) + ') { ' +
        '[Console]::Error.WriteLine(''WD-MEMBER-SETUP the member is not its pinned bytes''); exit 4 }; ' +
        '$env:WD_MIND_SHADOW_SYNTHETIC_MEMBER = ''opt-in''; ' +
        '& ' + $member + ' -OptInSyntheticMember -LaunchId ' + (ConvertTo-WdShadowOwnedLaunchLiteral $LaunchId) +
        ' -Member ''root'' -Subcase ' + (ConvertTo-WdShadowOwnedLaunchLiteral $Subcase) +
        ' -WitnessDirectory ' + (ConvertTo-WdShadowOwnedLaunchLiteral $WitnessDirectory) + ' -LifetimeSeconds ' +
        $life + '; $wdMemberCode = $LASTEXITCODE; if ($wdMemberCode -isnot [int]) { $wdMemberCode = 0 }; ' +
        'exit $wdMemberCode'
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $commandSha = -join @($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($command)) |
            ForEach-Object { $_.ToString('X2') })
    } finally { $sha.Dispose() }
    return [ordered]@{
        schema = 'wd.mind-shadow-fixed-member-launch-plan.v1'
        host_path = $HostPath; member_path = $MemberPath; member_sha256 = $ExpectedMemberSha256
        witness_directory = $WitnessDirectory; launch_id = $LaunchId; subcase = $Subcase; member = 'root'
        lifetime_seconds = [long]$LifetimeSeconds
        argv = [string[]]@('-NoProfile', '-NonInteractive', '-Command', $command)
        command_sha256 = $commandSha
        child_only_opt_in = [ordered]@{
            variable = 'WD_MIND_SHADOW_SYNTHETIC_MEMBER'; value = 'opt-in'; scope = 'child_process_only'
        }
        limits = ('a same-user file-reopen TOCTOU remains between the member hash check and its execution (TOCTOU, ' +
            'not OS isolation); the caller must bind this exact argv and command hash to its approved run')
        authority = 'none: a plan, never a launch'
    }
}

function New-WdShadowOwnedOutputObserver {
    param([switch] $ApprovedObservation, $Managed, $EvidenceDirectory, [long] $MaxOutputBytes,
        [long] $FloorBytes = 3221225472)
    if (-not $ApprovedObservation) {
        throw 'refused: the output observer needs the explicit opt-in (an accident guard, never authority)'
    }
    if ($null -eq $Managed -or $Managed -is [array] -or $null -eq $Managed.PSObject.Methods['Stop']) {
        throw 'refused: the observer is bound to exactly one owned managed object with a Stop method'
    }
    Test-WdShadowOwnedLaunchPath $EvidenceDirectory 'the evidence directory'
    if ([IO.Path]::GetFileName([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($EvidenceDirectory))) -cne
        '.codex-audit') { throw 'refused: the evidence directory is not <.codex-audit>\<run>\<launch id>' }
    if ($MaxOutputBytes -lt 1 -or $MaxOutputBytes -gt 8388608) {
        throw 'refused: the output bound is not from 1 byte to 8 MiB'
    }
    if ($FloorBytes -lt 3221225472) { throw 'refused: the floor is below 3 GiB' }
    $owned = $Managed
    $directory = [string]$EvidenceDirectory
    $max = $MaxOutputBytes
    $floor = $FloorBytes
    $state = @{ trigger = $null; stop = 'not_requested'; observations = 0 }
    return {
        $state.observations++
        $reason = $null; $status = 'WITHIN_OBSERVED_BUDGET'
        $sizes = [ordered]@{ stdout = $null; stderr = $null }; $total = $null; $free = $null
        try {
            $current = $directory
            while ($current.Length -gt 3) {
                if (-not [IO.Directory]::Exists($current) -or
                    ([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "the evidence ancestry is missing or a reparse point at $current"
                }
                $current = [IO.Path]::GetDirectoryName($current)
            }
            foreach ($key in @('stdout', 'stderr')) {
                $path = $directory + '\root.' + $key + '.log'
                if (-not [IO.File]::Exists($path)) { throw "root.$key.log is missing or not a file" }
                if ([IO.File]::GetAttributes($path) -band [IO.FileAttributes]::ReparsePoint) {
                    throw "root.$key.log is a reparse point"
                }
                $length = (New-Object IO.FileInfo($path)).Length
                if ($length -isnot [long] -or $length -lt 0) { throw "root.$key.log has no exact length" }
                $sizes[$key] = $length
            }
            $total = [decimal]$sizes['stdout'] + [decimal]$sizes['stderr']
            $free = (New-Object IO.DriveInfo('C')).AvailableFreeSpace
            if ($free -isnot [long] -or $free -lt 0) { throw 'the free space has no exact value' }
            if ($total -gt $max) { $status = 'OVER_BOUND'; $reason = "the logs hold $total bytes over the bound $max" }
            elseif ($free -lt $floor) { $status = 'DISK_FLOOR'; $reason = "C: free $free is below the floor $floor" }
        } catch {
            $status = 'UNKNOWN'; $reason = [string]$_.Exception.Message
        }
        if ($null -ne $state.trigger) {
            $status = $state.trigger.status; $reason = $state.trigger.reason
        } elseif ($status -cne 'WITHIN_OBSERVED_BUDGET') {
            $state.trigger = @{ status = $status; reason = $reason }
            try { $owned.Stop(); $state.stop = 'requested' } catch { $state.stop = 'failed' }
        }
        return [ordered]@{
            schema = 'wd.mind-shadow-owned-output-observation.v1'; status = $status; reason = $reason
            stdout_bytes = $sizes['stdout']; stderr_bytes = $sizes['stderr']; total_bytes = $total
            max_output_bytes = $max; free_bytes = $free; floor_bytes = $floor; stop = $state.stop
            observation = $state.observations; native_pass = $false
        }
    }.GetNewClosure()
}

if ($MyInvocation.InvocationName -cne '.') {
    [Console]::Error.WriteLine('refused: this helper only defines functions; dot-source it from an approved caller')
    exit 3
}

#requires -Version 5.1
<#
.SYNOPSIS
    Opt-in native acceptance harness for ops/windows/reboot/Start-WdMindShadowJob.ps1 (cases A1-A7). It refuses by
    default and is never collected or run by CI.

.DESCRIPTION
    Checkpoint 1 holds only the default refusal, the fail-closed known-member witness (G1/D7) and the run-bound case
    record publication (G4). Every case is UNKNOWN until its body exists and an approved executor runs it on the target
    host at the approved token (D4/D5). Nothing here starts a process, loads a native type or imports the adapter.

    Witness (D7 baseline, RCO2's per-member example): each synthetic member creates <witness dir>\<member>.witness with
    CreateNew, writes one start line "<launch id> <member id>" and holds the handle with FileShare.None until it ends;
    the OS releases the handle then. The observer opens each file exclusively and holds no job handle. A missing,
    invalid or foreign start line, a held file (a survivor) or an unknown state is never PASS. No claim is made about
    processes created through a broker or a service.

    Design: RCO1 (A1-A7), RCO2 (G1-G4 and the witness), Lead (decisions); fable-5 wrote this file.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [switch] $OptInNativeAcceptance
)
if (-not $OptInNativeAcceptance -or [string]$env:WD_MIND_SHADOW_NATIVE_ACCEPTANCE -cne 'opt-in') {
    Write-Error 'refused: the native acceptance harness runs only on an explicit opt-in by an approved executor'
    exit 3
}
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-WdShadowMemberObservation {
    param([string] $WitnessDirectory, [object[]] $Expected)
    $observed = @{}
    foreach ($member in @($Expected)) {
        $path = Join-Path $WitnessDirectory ([string]$member + '.witness')
        $entry = @{ start_witness = 'missing'; released = 'UNKNOWN'; launch_id = '' }
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $entry.start_witness = 'present'
            try {
                $stream = New-Object IO.FileStream($path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                    [IO.FileShare]::None)
                try {
                    $line = [string](New-Object IO.StreamReader($stream)).ReadLine()
                    $parts = @($line.Split(' '))
                    if ($parts.Count -eq 2 -and $parts[1] -ceq [string]$member) {
                        $entry.launch_id = $parts[0]
                    } else {
                        $entry.start_witness = 'invalid'
                    }
                    $entry.released = $true
                } finally { $stream.Dispose() }
            } catch [IO.IOException] {
                $entry.released = $false
            }
        }
        $observed[[string]$member] = $entry
    }
    return $observed
}

function Test-WdShadowMemberWitness {
    # G1 (RCO2) / D7 (Lead): PASS only for a non-empty preregistered member set in which every member has a start
    # witness bound to this launch and an OS-released lifetime witness; anything missing or unknown is not PASS.
    param([object[]] $Expected, [hashtable] $Observed, [string] $LaunchId)
    if ($null -eq $Expected -or $Expected.Count -eq 0 -or -not $LaunchId) { return 'UNKNOWN' }
    foreach ($member in $Expected) {
        $seen = $Observed[[string]$member]
        if ($null -eq $seen) { return 'FAIL' }
        if ([string]$seen.start_witness -cne 'present') { return 'FAIL' }
        if ([string]$seen.launch_id -cne $LaunchId) { return 'FAIL' }
        if ($seen.released -isnot [bool]) { return 'UNKNOWN' }
        if (-not $seen.released) { return 'FAIL' }
    }
    return 'PASS'
}

function Publish-WdShadowCaseRecord {
    # G4: one record per case, bound to the run, published once without overwrite.
    param([string] $Directory, [string] $CaseId, $Record)
    $final = Join-Path $Directory ('case_' + $CaseId + '.json')
    $temporary = Join-Path $Directory ('.case_' + $CaseId + '.json.tmp')
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes((($Record | ConvertTo-Json -Depth 8) + "`n"))
    $stream = New-Object IO.FileStream($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    [IO.File]::Move($temporary, $final)
}

# Checkpoint 1: no case body exists, so every case stays UNKNOWN (NOT_RUN) and the harness refuses even on opt-in.
$cases = [ordered]@{
    'A1' = 'UNKNOWN'
    'A2' = 'UNKNOWN'
    'A3' = 'UNKNOWN'
    'A4' = 'UNKNOWN'
    'A5' = 'UNKNOWN'
    'A6' = 'UNKNOWN'
    'A7' = 'UNKNOWN'
}
Write-Error ('refused: checkpoint 1 has no case bodies; ' + (@($cases.Keys) -join ', ') + ' stay UNKNOWN (NOT_RUN)')
exit 3

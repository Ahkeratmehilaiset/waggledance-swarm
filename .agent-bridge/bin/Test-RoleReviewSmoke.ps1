#requires -Version 5.1
<#
.SYNOPSIS
    R20.5 / R16 smoke test — Invoke-RoleReview.ps1 wires three
    independent role events + one synthesis event correctly.

.DESCRIPTION
    Runs Invoke-RoleReview.ps1 in -DryRun mode against a fresh
    AGENT_BRIDGE_RUNTIME_ROOT and asserts:

      1. exit 0
      2. exactly three per-role events appear in the bridge
         (architect / security / reliability) with sub-task_ids
      3. exactly one synthesis event appears with the same
         base task id and references all three role sub-task_ids
         in its message
      4. -Roles can subset (architect+security only -> two role
         events + one synthesis)
      5. -Synthesis off skips the synthesis event entirely
      6. invalid -Role rejected before any event is emitted

    Exit 0 on all expectations met, 1 otherwise. Temp runtime root is
    cleaned up on success and failure so reruns are safe.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$invokeRoleReview = Join-Path $bridgeBin 'Invoke-RoleReview.ps1'
$readBridge = Join-Path $bridgeBin 'Read-AgentBridge.ps1'

$results = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name = $Name; passed = $Passed; detail = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color  = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

function Read-AllEvents {
    param([string] $RuntimeRoot)
    $eventsPath = Join-Path (Join-Path $RuntimeRoot 'shared') 'events.jsonl'
    if (-not (Test-Path -LiteralPath $eventsPath -PathType Leaf)) {
        return @()
    }
    $out = New-Object System.Collections.Generic.List[object]
    Get-Content -Path $eventsPath -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) { return }
        try { [void]$out.Add(($line | ConvertFrom-Json)) }
        catch { }
    }
    , $out.ToArray()
}

# Generate a fresh runtime root so this test is hermetic
$tempRoot = Join-Path $env:TEMP `
    "role-review-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$savedEnv = $env:AGENT_BRIDGE_RUNTIME_ROOT

try {
    Write-Host 'Invoke-RoleReview smoke test' -ForegroundColor Cyan
    Write-Host '============================='

    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempRoot

    # ── 1. all-roles dry-run ─────────────────────────────────────
    Write-Host ''
    Write-Host 'Check 1: all-roles dry-run emits 3 role events + 1 synthesis'
    $target1 = "PR-smoke-1"
    $r1 = & $invokeRoleReview -Target $target1 -DryRun
    Add-Check 'invoke succeeds' ($null -ne $r1) "got: $($r1 | Out-String)"

    $events = Read-AllEvents -RuntimeRoot $tempRoot
    $base1 = "$target1-role-review-$(Get-Date -Format 'yyyy-MM-dd')"
    $roleEvents1 = @($events | Where-Object {
        $_.task_id -match "^${base1}-(architect|security|reliability)$"
    })
    $synthEvents1 = @($events | Where-Object {
        $_.task_id -eq "${base1}-synthesis"
    })

    Add-Check 'three per-role events' ($roleEvents1.Count -eq 3) `
        "found $($roleEvents1.Count) role events"
    Add-Check 'one synthesis event' ($synthEvents1.Count -eq 1) `
        "found $($synthEvents1.Count) synthesis events"

    if ($synthEvents1.Count -eq 1) {
        $synthMsg = [string]$synthEvents1[0].message
        $allReferenced = $true
        foreach ($role in @('architect','security','reliability')) {
            if ($synthMsg -notmatch "${base1}-$role") {
                $allReferenced = $false
            }
        }
        Add-Check 'synthesis references all three sub-task_ids' $allReferenced `
            "msg: $synthMsg"

        Add-Check 'synthesis verdict approves on dry-run' `
            ($synthMsg -match 'all 3 roles approve') `
            "msg: $synthMsg"
    }

    # ── 2. role subset ───────────────────────────────────────────
    Write-Host ''
    Write-Host 'Check 2: -Roles architect,security emits 2 role events + 1 synthesis'
    $target2 = "PR-smoke-2"
    $r2 = & $invokeRoleReview -Target $target2 -Roles architect,security -DryRun

    $events2 = Read-AllEvents -RuntimeRoot $tempRoot
    $base2 = "$target2-role-review-$(Get-Date -Format 'yyyy-MM-dd')"
    $roleEvents2 = @($events2 | Where-Object {
        $_.task_id -match "^${base2}-(architect|security|reliability)$"
    })
    $synthEvents2 = @($events2 | Where-Object {
        $_.task_id -eq "${base2}-synthesis"
    })
    Add-Check 'two per-role events' ($roleEvents2.Count -eq 2) `
        "found $($roleEvents2.Count)"
    Add-Check 'no reliability event' (
        @($events2 | Where-Object { $_.task_id -eq "${base2}-reliability" }).Count -eq 0
    ) ''
    Add-Check 'one synthesis event for subset' ($synthEvents2.Count -eq 1) ''

    # ── 3. -Synthesis off ────────────────────────────────────────
    Write-Host ''
    Write-Host 'Check 3: -Synthesis off skips the synthesis event'
    $target3 = "PR-smoke-3"
    $r3 = & $invokeRoleReview -Target $target3 -Synthesis off -DryRun
    $events3 = Read-AllEvents -RuntimeRoot $tempRoot
    $base3 = "$target3-role-review-$(Get-Date -Format 'yyyy-MM-dd')"
    $roleEvents3 = @($events3 | Where-Object {
        $_.task_id -match "^${base3}-(architect|security|reliability)$"
    })
    $synthEvents3 = @($events3 | Where-Object {
        $_.task_id -eq "${base3}-synthesis"
    })
    Add-Check 'three per-role events with -Synthesis off' ($roleEvents3.Count -eq 3) ''
    Add-Check 'no synthesis event' ($synthEvents3.Count -eq 0) ''

    # ── 4. invalid role rejected ──────────────────────────────────
    Write-Host ''
    Write-Host 'Check 4: invalid role rejected before any event emitted'
    $eventsBefore = (Read-AllEvents -RuntimeRoot $tempRoot).Count
    $invalidThrew = $false
    try {
        & $invokeRoleReview -Target 'PR-smoke-4' -Roles architect,bogus -DryRun 2>$null
    } catch {
        $invalidThrew = $true
    }
    $eventsAfter = (Read-AllEvents -RuntimeRoot $tempRoot).Count
    Add-Check 'invalid role throws' $invalidThrew ''
    Add-Check 'no events leaked from invalid invocation' `
        ($eventsAfter -eq $eventsBefore) `
        "before=$eventsBefore after=$eventsAfter"

    # ── Summary ──────────────────────────────────────────────────
    Write-Host ''
    $pass = @($results | Where-Object { $_.passed }).Count
    $fail = @($results | Where-Object { -not $_.passed }).Count
    $resultColor = if ($fail -eq 0) { 'Green' } else { 'Red' }
    Write-Host ("Result: {0} passed / {1} failed" -f $pass, $fail) `
        -ForegroundColor $resultColor
    if ($fail -gt 0) { exit 1 } else { exit 0 }
}
finally {
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedEnv
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

#requires -Version 5.1
<#
.SYNOPSIS
    Smoke test for Restore-BridgeSpool.ps1 in an isolated temp bridge root.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:failures = 0
function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail = '')
    if ($Passed) { Write-Host "  PASS $Name" -ForegroundColor Green }
    else { Write-Host "  FAIL $Name :: $Detail" -ForegroundColor Red; $script:failures++ }
}

$tempRoot = Join-Path $env:TEMP "bridge-spool-replay-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$replayScript = Join-Path $PSScriptRoot 'Restore-BridgeSpool.ps1'

try {
    Write-Host 'Bridge spool replay smoke test' -ForegroundColor Cyan
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'shared') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $tempRoot 'spool') -Force)
    $eventsPath = Join-Path (Join-Path $tempRoot 'shared') 'events.jsonl'

    # 1. Empty spool -> no-op
    $out = & $replayScript -BridgeRoot $tempRoot
    Add-Check -Name 'empty spool is a no-op' -Passed ($out -match 'nothing to replay')

    # 2. A valid spooled event replays into the shared log and archives
    $event = '{"ts_utc":"2026-07-02T10:00:00Z","agent":"fable-5","type":"message","task_id":"spool-replay-smoke","status":"info","message":"recovered"}'
    $spoolFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T100000000-1234.jsonl'
    Set-Content -LiteralPath $spoolFile -Value $event -Encoding UTF8 -NoNewline

    $out = & $replayScript -BridgeRoot $tempRoot
    Add-Check -Name 'replay reports one replayed' -Passed ($out -match 'replayed=1 failed=0')
    $logged = Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8
    Add-Check -Name 'event appended to shared log' -Passed ($logged -match 'spool-replay-smoke')
    Add-Check -Name 'spool file archived' -Passed (
        (-not (Test-Path -LiteralPath $spoolFile)) -and
        (Test-Path -LiteralPath (Join-Path (Join-Path (Join-Path $tempRoot 'spool') 'replayed') (Split-Path -Leaf $spoolFile)))
    )

    # 3. Idempotent rerun -> nothing to replay
    $out = & $replayScript -BridgeRoot $tempRoot
    Add-Check -Name 'rerun is a no-op' -Passed ($out -match 'nothing to replay')

    # 4. Malformed spool file is SKIPPED (kept in place), never appended
    $badFile = Join-Path (Join-Path $tempRoot 'spool') 'failed-append-fable-5-20260702T110000000-9.jsonl'
    Set-Content -LiteralPath $badFile -Value '{not json' -Encoding UTF8 -NoNewline
    $before = (Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8)
    $out = & $replayScript -BridgeRoot $tempRoot 3>$null  # suppress warning stream
    $after = (Get-Content -LiteralPath $eventsPath -Raw -Encoding UTF8)
    Add-Check -Name 'malformed file skipped and kept' -Passed (
        ($out -match 'failed=1') -and (Test-Path -LiteralPath $badFile) -and ($before -eq $after)
    )

    # 5. DryRun neither appends nor archives
    Remove-Item -LiteralPath $badFile -Force
    Set-Content -LiteralPath $spoolFile -Value $event -Encoding UTF8 -NoNewline
    $out = & $replayScript -BridgeRoot $tempRoot -DryRun
    Add-Check -Name 'dry run lists but keeps file' -Passed (
        ($out -match 'would replay') -and (Test-Path -LiteralPath $spoolFile)
    )
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($script:failures -gt 0) {
    Write-Host "SMOKE FAILED: $script:failures check(s)" -ForegroundColor Red
    exit 1
}
Write-Host 'SMOKE PASSED' -ForegroundColor Green
exit 0

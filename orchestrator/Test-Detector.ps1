#requires -Version 5.1
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libDir = Join-Path $PSScriptRoot 'lib'
. (Join-Path $libDir 'Detector.ps1')

$script:tests = 0; $script:passes = 0; $script:fails = @()

function _Merge {
    param([hashtable] $Base, [hashtable] $Over)
    $r = @{}
    foreach ($k in $Base.Keys) { $r[$k] = $Base[$k] }
    foreach ($k in $Over.Keys) { $r[$k] = $Over[$k] }
    return $r
}

function Invoke-VerdictTest {
    param([string] $Name, [hashtable] $VerdictArgs, [string] $ExpectedStatus)
    $script:tests++
    try { $verdict = Get-DetectorVerdict @VerdictArgs }
    catch { Write-Host "FAIL  $Name : threw: $($_.Exception.Message)" -ForegroundColor Red; $script:fails += $Name; return }
    if ($verdict.status -eq $ExpectedStatus) {
        $script:passes++; Write-Host "PASS  $Name" -ForegroundColor Green
    } else {
        Write-Host ("FAIL  {0}: expected={1}  got={2}  reason={3}" -f $Name, $ExpectedStatus, $verdict.status, $verdict.reason) -ForegroundColor Red
        $script:fails += $Name
    }
}

$now          = [datetime]::UtcNow
$startedShort = $now.AddMinutes(-5)
$startedLong  = $now.AddMinutes(-180)

$base = @{
    InteractivePromptPatterns  = @('Do you want to proceed', 'Continue\? \[y/N\]', '\[y/N\]\s*$')
    CompletedPromptPatterns    = @('PS [A-Z]:[^>\r\n]*> *$')
    StableThresholdSeconds     = 25
    RunTimeoutMinutes          = 120
    RunStartedUtc              = $startedShort
    NowUtc                     = $now
    ExitMarker                 = '##WAGGLE_RUN_COMPLETE##'
    ReportLastWriteUtc         = $null
    ExecutionMode              = 'interactiveTranscriptFallback'
    CompletionSignalPresent    = $false
    FailureSignalPresent       = $false
    ProcessExited              = $null
    ProcessExitCode            = $null
}

Invoke-VerdictTest -Name 'Fallback: recent growth -> RUNNING' -ExpectedStatus 'RUNNING' -VerdictArgs (_Merge $base @{
    TranscriptLines = @('processing...', 'still working...')
    TranscriptLastWriteUtc = $now.AddSeconds(-2)
})

Invoke-VerdictTest -Name 'Fallback: interactive prompt -> NEEDS_MANUAL_ACTION' -ExpectedStatus 'NEEDS_MANUAL_ACTION' -VerdictArgs (_Merge $base @{
    TranscriptLines = @('analyzing...', 'Do you want to proceed? [y/N]')
    TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Fallback: stable + PS prompt -> COMPLETED' -ExpectedStatus 'COMPLETED' -VerdictArgs (_Merge $base @{
    TranscriptLines = @('Task done.', 'PS C:\Users\jani> ')
    TranscriptLastWriteUtc = $now.AddSeconds(-30)
})

Invoke-VerdictTest -Name 'Fallback: PS prompt unstable -> RUNNING' -ExpectedStatus 'RUNNING' -VerdictArgs (_Merge $base @{
    TranscriptLines = @('Task done.', 'PS C:\Users\jani> ')
    TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Fallback: timeout' -ExpectedStatus 'TIMEOUT' -VerdictArgs (_Merge $base @{
    RunStartedUtc = $startedLong
    TranscriptLines = @('still going...')
    TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Fallback: exit marker -> COMPLETED' -ExpectedStatus 'COMPLETED' -VerdictArgs (_Merge $base @{
    TranscriptLines = @('all done', '##WAGGLE_RUN_COMPLETE##', 'PS C:\> ')
    TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Fallback: empty transcript -> RUNNING' -ExpectedStatus 'RUNNING' -VerdictArgs (_Merge $base @{
    TranscriptLines = @()
    TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Fallback: interactive beats timeout' -ExpectedStatus 'NEEDS_MANUAL_ACTION' -VerdictArgs (_Merge $base @{
    RunStartedUtc = $startedLong
    TranscriptLines = @('processing...', 'Continue? [y/N]')
    TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Print: completion signal -> COMPLETED (preliminary)' -ExpectedStatus 'COMPLETED' -VerdictArgs (_Merge $base @{
    ExecutionMode = 'print'; CompletionSignalPresent = $true
    ProcessExited = $true; ProcessExitCode = 0
    TranscriptLines = @('out'); TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Print: failure signal -> FAILED' -ExpectedStatus 'FAILED' -VerdictArgs (_Merge $base @{
    ExecutionMode = 'print'; FailureSignalPresent = $true
    ProcessExited = $true; ProcessExitCode = 0
    TranscriptLines = @(); TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Print: exit 0 -> COMPLETED (preliminary; verifier may downgrade)' -ExpectedStatus 'COMPLETED' -VerdictArgs (_Merge $base @{
    ExecutionMode = 'print'; ProcessExited = $true; ProcessExitCode = 0
    TranscriptLines = @(); TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Print: exit 1 -> FAILED' -ExpectedStatus 'FAILED' -VerdictArgs (_Merge $base @{
    ExecutionMode = 'print'; ProcessExited = $true; ProcessExitCode = 1
    TranscriptLines = @(); TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Invoke-VerdictTest -Name 'Print: not exited, marker in stdout -> COMPLETED' -ExpectedStatus 'COMPLETED' -VerdictArgs (_Merge $base @{
    ExecutionMode = 'print'; ProcessExited = $false; ProcessExitCode = $null
    TranscriptLines = @('working...', '##WAGGLE_RUN_COMPLETE##')
    TranscriptLastWriteUtc = $now.AddSeconds(-3)
})

Write-Host ''
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0

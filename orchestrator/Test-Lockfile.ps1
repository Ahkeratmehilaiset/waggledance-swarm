#requires -Version 5.1
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libDir = Join-Path $PSScriptRoot 'lib'
. (Join-Path $libDir 'Lockfile.ps1')

$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("waggle-lock-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

$script:tests = 0; $script:passes = 0; $script:fails = @()
function Pass($n) { $script:tests++; $script:passes++; Write-Host "PASS  $n" -ForegroundColor Green }
function Fail($n, $detail) { $script:tests++; Write-Host "FAIL  $n : $detail" -ForegroundColor Red; $script:fails += $n }

try {
    $lockPath = Join-Path $tmpDir 'orchestrator.lock'

    # 1) Acquire on empty
    $l1 = Acquire-WaggleLock -Path $lockPath -IterationId 'iter-1'
    if (Test-Path $lockPath) { Pass 'acquire creates lock file' } else { Fail 'acquire creates lock file' '' }
    if ($l1.lock_id) { Pass 'lock has lock_id GUID' } else { Fail 'lock has lock_id GUID' '' }
    if ($l1.pid -eq $PID) { Pass 'lock pid is current process' } else { Fail 'lock pid' "got $($l1.pid)" }

    # 2) Re-acquire while live should throw (no override)
    $threw = $false
    try { Acquire-WaggleLock -Path $lockPath -IterationId 'iter-2' | Out-Null } catch { $threw = $true }
    if ($threw) { Pass 're-acquire while live throws' } else { Fail 're-acquire while live throws' 'no throw' }

    # 3) Release without lock_id refuses
    $rel = Release-WaggleLock -Path $lockPath
    if ($rel -eq $false -and (Test-Path $lockPath)) { Pass 'release without lock_id refuses' } else { Fail 'release without lock_id refuses' '' }

    # 4) Release with WRONG lock_id refuses
    $rel = Release-WaggleLock -Path $lockPath -LockId 'wrong-id'
    if ($rel -eq $false -and (Test-Path $lockPath)) { Pass 'release with wrong lock_id refuses' } else { Fail 'release with wrong lock_id refuses' '' }

    # 5) Release with correct lock_id removes
    $rel = Release-WaggleLock -Path $lockPath -LockId $l1.lock_id
    if ($rel -eq $true -and -not (Test-Path $lockPath)) { Pass 'release with correct lock_id removes' } else { Fail 'release with correct lock_id removes' '' }

    # 6) Stale lock (fake dead pid)
    $stale = [pscustomobject]@{
        lock_id      = ([Guid]::NewGuid().ToString())
        pid          = 999999
        hostname     = [System.Net.Dns]::GetHostName()
        started_at   = (Get-Date).AddDays(-1).ToUniversalTime().ToString('o')
        iteration_id = 'iter-stale'
    }
    $stale | ConvertTo-Json | Set-Content -Path $lockPath -Encoding UTF8

    $threw = $false
    try { Acquire-WaggleLock -Path $lockPath -IterationId 'iter-x' | Out-Null } catch { $threw = $true }
    if ($threw) { Pass 'stale lock without -ForceStaleLock throws' } else { Fail 'stale lock without flag throws' 'no throw' }

    # 7) Stale lock + -ForceStaleLock reclaims
    $stale | ConvertTo-Json | Set-Content -Path $lockPath -Encoding UTF8
    $l2 = Acquire-WaggleLock -Path $lockPath -IterationId 'iter-rec' -ForceStaleLock
    if ($l2.iteration_id -eq 'iter-rec') { Pass 'stale + ForceStaleLock reclaims' } else { Fail 'reclaim' '' }
    [void](Release-WaggleLock -Path $lockPath -LockId $l2.lock_id)

    # 8) Live lock + -DangerouslyOverrideLiveLock
    $l3 = Acquire-WaggleLock -Path $lockPath -IterationId 'iter-live'
    $l4 = Acquire-WaggleLock -Path $lockPath -IterationId 'iter-override' -DangerouslyOverrideLiveLock
    if ($l4.iteration_id -eq 'iter-override') { Pass 'DangerouslyOverrideLiveLock replaces live' } else { Fail 'override' '' }
    [void](Release-WaggleLock -Path $lockPath -LockId $l4.lock_id)

    # 9) Cross-host treated as alive
    $foreign = [pscustomobject]@{
        lock_id  = 'x'; pid = $PID; hostname = 'some-other-host'
        started_at = (Get-Date).ToUniversalTime().ToString('o'); iteration_id = 'iter-foreign'
    }
    if (Test-LockHolderAlive -Lock $foreign) { Pass 'cross-host treated as alive' } else { Fail 'cross-host alive' '' }

    # 10) Atomic create: two parallel attempts after release: only one wins
    if (-not (Test-Path $lockPath)) {
        $a = Acquire-WaggleLock -Path $lockPath -IterationId 'iter-a'
        $threw = $false
        try { Acquire-WaggleLock -Path $lockPath -IterationId 'iter-b' | Out-Null } catch { $threw = $true }
        if ($threw) { Pass 'second concurrent acquire throws' } else { Fail 'second acquire' '' }
        [void](Release-WaggleLock -Path $lockPath -LockId $a.lock_id)
    }

    # 11) Phase 2A-4 REL-001: try/finally pattern releases lock on throw.
    if (Test-Path $lockPath) { Remove-Item -Force $lockPath }
    $caught = $false
    $lockedFor = $null
    try {
        $lockedFor = Acquire-WaggleLock -Path $lockPath -IterationId 'iter-throw'
        try {
            throw 'simulated mid-iteration failure'
        }
        finally {
            [void](Release-WaggleLock -Path $lockPath -LockId $lockedFor.lock_id)
        }
    } catch { $caught = $true }
    if ($caught -and -not (Test-Path $lockPath)) {
        Pass 'REL-001: try/finally releases lock on throw inside try'
    } else {
        Fail 'REL-001: try/finally releases lock on throw inside try' "caught=$caught lockExists=$(Test-Path $lockPath)"
    }

    # 12) Phase 2A-4 REL-003: Invoke-WaggleIteration must acquire the
    # lock BEFORE evaluating the resume short-circuit. Static source
    # check.
    $iterSrc = Get-Content -Raw -Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'orchestrator/Invoke-WaggleIteration.ps1') -Encoding UTF8
    $lines = $iterSrc -split "(?:\r\n|\r|\n)"
    $acquireLine = -1; $resumeLine = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($acquireLine -lt 0 -and $lines[$i] -match '^\s*\$lock\s*=\s*Acquire-WaggleLock') { $acquireLine = $i }
        if ($resumeLine  -lt 0 -and $lines[$i] -match '\$ResumeIteration\s+-and\s+\(Test-Path\s+\$stateFile\)') { $resumeLine = $i }
    }
    if ($acquireLine -gt 0 -and $resumeLine -gt 0 -and $acquireLine -lt $resumeLine) {
        Pass "REL-003: Acquire-WaggleLock at line $acquireLine precedes resume short-circuit at line $resumeLine"
    } else {
        Fail 'REL-003: Acquire-WaggleLock must precede resume short-circuit in Invoke-WaggleIteration.ps1' "acquireLine=$acquireLine resumeLine=$resumeLine"
    }

    # 13) Phase 2A-4 REL-001 in Invoke-WaggleReview: source-level
    # check that the review runner has try{ ... } finally { Release }.
    $rvSrc = Get-Content -Raw -Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'orchestrator/Invoke-WaggleReview.ps1') -Encoding UTF8
    $hasReleasePath = ($rvSrc -match 'Acquire-WaggleLock[\s\S]*?try\s*\{[\s\S]*?finally\s*\{[\s\S]*?Release-WaggleLock\s+-Path\s+\$lockPath\s+-LockId\s+\$lock\.lock_id')
    if ($hasReleasePath) {
        Pass 'REL-001: Invoke-WaggleReview Acquire -> try -> finally -> Release shape present'
    } else {
        Fail 'REL-001: Invoke-WaggleReview missing Acquire -> try/finally -> Release shape' ''
    }
}
finally {
    Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0

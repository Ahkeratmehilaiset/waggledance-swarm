#requires -Version 5.1
<#
.SYNOPSIS
    Focused smoke test for Invoke-BridgeGit.ps1 +
    Test-BridgeBranchSwitchSafe.ps1.

.DESCRIPTION
    Exercises branch-guard behavior against synthetic claims in an isolated
    bridge root under .codex-audit. An isolated local Git repository proves
    that a foreign write in a verified different worktree does not serialize
    this worktree. Same-worktree and unverifiable claims still fail closed.

    No real branch can change: every allowed probe asks Git to switch to a
    deliberately nonexistent branch. Exit code 0 means all expectations met.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeBin = $PSScriptRoot
$invokeGit = Join-Path $bridgeBin 'Invoke-BridgeGit.ps1'
$checkScript = Join-Path $bridgeBin 'Test-BridgeBranchSwitchSafe.ps1'
$bridgeStatus = Join-Path $bridgeBin 'Get-AgentBridgeStatus.ps1'
$results = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [bool] $Passed,
        [string] $Detail = ''
    )
    [void]$results.Add([pscustomobject]@{
        name    = $Name
        passed  = $Passed
        detail  = $Detail
    })
    $marker = if ($Passed) { 'PASS' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $marker, $Name) -ForegroundColor $color
    if ($Detail) { Write-Host "        $Detail" }
}

function Invoke-GuardProbe {
    param(
        [string] $Agent = 'claude',
        [switch] $Force
    )
    $previousEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        if ($Force) {
            $raw = @(
                & $invokeGit -Agent $Agent -Force -- switch --no-guess `
                    __bridge_guard_nonexistent_branch__ 2>&1
            )
        } else {
            $raw = @(
                & $invokeGit -Agent $Agent -- switch --no-guess `
                    __bridge_guard_nonexistent_branch__ 2>&1
            )
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEAP
    }
    $output = ($raw | ForEach-Object { [string]$_ }) -join "`n"
    return [pscustomobject]@{
        exit_code = $exitCode
        output    = $output
        blocked   = ($output -match 'BLOCKED|branch-moving git')
    }
}

function Invoke-PassiveJsonProbe {
    param([string] $Agent = 'claude')

    $previousEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $raw = @(& $checkScript -Agent $Agent -Json 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEAP
    }
    $output = ($raw | ForEach-Object { [string]$_ }) -join "`n"
    $report = $null
    try { $report = $output | ConvertFrom-Json -ErrorAction Stop } catch {}
    return [pscustomobject]@{
        exit_code = $exitCode
        report    = $report
        output    = $output
    }
}

$repoRootLines = @(& git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0 -or $repoRootLines.Count -ne 1) {
    throw 'Bridge guard smoke must run inside exactly one Git worktree'
}
$repoRoot = [System.IO.Path]::GetFullPath([string]$repoRootLines[0])
$auditRoot = Join-Path $repoRoot '.codex-audit'
$tempRoot = Join-Path $auditRoot (
    'bridge-guard-smoke-' + [guid]::NewGuid().ToString('N')
)
$tempBridgeRoot = Join-Path $tempRoot 'bridge'
$claimsDir = Join-Path $tempBridgeRoot 'work_queue\claims'
$otherRepo = Join-Path $tempRoot 'other-repo'
$claimPath = Join-Path $claimsDir 'bridge-guard-smoke.json'
$previousRuntimeRoot = [Environment]::GetEnvironmentVariable(
    'AGENT_BRIDGE_RUNTIME_ROOT',
    'Process'
)
$utf8 = New-Object System.Text.UTF8Encoding($false)
$substDrive = $null
$substCreated = $false
$cleanupErrors = @()

function Write-TestClaim {
    param(
        [Parameter(Mandatory)] [string] $Mode,
        [Parameter(Mandatory)] [string] $Cwd,
        [string] $Agent = 'codex'
    )
    $now = (Get-Date).ToUniversalTime().ToString('o')
    $claim = [ordered]@{
        claimed_at_utc     = $now
        last_heartbeat_utc = $now
        agent              = $Agent
        task_id            = 'bridge-guard-smoke'
        summary            = 'isolated branch-guard smoke claim'
        mode               = $Mode
        write_scope        = @('tests/smoke_only')
        run_id             = 'bridge-guard-smoke'
        lease_seconds      = 300
        cwd                = $Cwd
        git_branch         = 'bridge-guard-smoke'
    }
    [System.IO.File]::WriteAllText(
        $claimPath,
        ($claim | ConvertTo-Json -Depth 6),
        $utf8
    )
}

function Clear-TestClaim {
    if (Test-Path -LiteralPath $claimPath -PathType Leaf) {
        Remove-Item -LiteralPath $claimPath -Force
    }
}

try {
    [void](New-Item -ItemType Directory -Path $claimsDir -Force)
    [void](New-Item -ItemType Directory -Path $otherRepo -Force)
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $tempBridgeRoot

    & git -C $otherRepo init --quiet
    if ($LASTEXITCODE -ne 0) { throw 'could not initialize isolated Git repo' }

    Write-Host 'Bridge guard smoke test' -ForegroundColor Cyan
    Write-Host '======================='

    Clear-TestClaim
    & $invokeGit -Agent claude -- status --short | Out-Null
    Add-Check -Name 'pass-through git status' `
        -Passed ($LASTEXITCODE -eq 0) `
        -Detail "expected exit 0, got $LASTEXITCODE"

    $probe = Invoke-GuardProbe
    Add-Check -Name 'no claim passes through to Git' `
        -Passed ($probe.exit_code -ne 2 -and -not $probe.blocked) `
        -Detail "exit=$($probe.exit_code) blocked=$($probe.blocked)"

    Write-TestClaim -Mode 'write' -Cwd $repoRoot
    $probe = Invoke-GuardProbe
    Add-Check -Name 'foreign write in same worktree blocks' `
        -Passed ($probe.exit_code -eq 2 -and $probe.blocked) `
        -Detail "exit=$($probe.exit_code) blocked=$($probe.blocked)"

    $passive = Invoke-PassiveJsonProbe
    Add-Check -Name 'passive guard blocks same-worktree write' `
        -Passed (
            $passive.exit_code -eq 2 -and
            $null -ne $passive.report -and
            $passive.report.safe -eq $false -and
            $passive.report.blocking_claims -eq 1 -and
            $passive.report.blocking_detail[0].reason -ceq
                'foreign_write_claim_in_current_worktree'
        ) -Detail "exit=$($passive.exit_code) output=$($passive.output)"

    & $invokeGit -Agent claude -- status --short | Out-Null
    Add-Check -Name 'foreign write leaves pass-through verbs unaffected' `
        -Passed ($LASTEXITCODE -eq 0)

    $forceProbe = Invoke-GuardProbe -Force
    Add-Check -Name 'non-privileged force remains rejected' `
        -Passed (
            $forceProbe.exit_code -eq 2 -and
            $forceProbe.output -match 'REJECTED|restricted'
        ) -Detail "exit=$($forceProbe.exit_code)"

    $previousEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $jsonForceRaw = @(
            & $checkScript -Agent operator -Json -Force 2>&1
        )
        $jsonForceExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEAP
    }
    $jsonForceOutput = (
        $jsonForceRaw | ForEach-Object { [string]$_ }
    ) -join "`n"
    Add-Check -Name 'JSON force cannot bypass canonical override audit' `
        -Passed (
            $jsonForceExit -eq 2 -and
            $jsonForceOutput -match 'unsupported'
        ) -Detail "exit=$jsonForceExit output=$jsonForceOutput"

    try {
        & $bridgeStatus -MaxUnresolved 10 | Out-Null
        $statusOk = $true
    } catch {
        $statusOk = $false
    }
    Add-Check -Name 'bridge status accepts isolated claim shape' `
        -Passed $statusOk

    Write-TestClaim -Mode 'read-only' -Cwd $repoRoot
    $probe = Invoke-GuardProbe
    $passive = Invoke-PassiveJsonProbe
    Add-Check -Name 'read-only claim does not own branch state' `
        -Passed (
            $probe.exit_code -ne 2 -and
            -not $probe.blocked -and
            $passive.exit_code -eq 0 -and
            $null -ne $passive.report -and
            $passive.report.safe -eq $true
        ) -Detail (
            "wrapper_exit=$($probe.exit_code) passive_exit=" +
            "$($passive.exit_code)"
        )

    Write-TestClaim -Mode 'write' -Cwd $otherRepo
    $probe = Invoke-GuardProbe
    $passive = Invoke-PassiveJsonProbe
    Add-Check -Name 'verified different Git worktree is independent' `
        -Passed (
            $probe.exit_code -ne 2 -and
            -not $probe.blocked -and
            $passive.exit_code -eq 0 -and
            $null -ne $passive.report -and
            $passive.report.safe -eq $true -and
            $passive.report.blocking_claims -eq 0
        ) -Detail (
            "wrapper_exit=$($probe.exit_code) passive_exit=" +
            "$($passive.exit_code)"
        )

    Write-TestClaim -Mode 'write' -Cwd (Join-Path $tempRoot 'missing-repo')
    $probe = Invoke-GuardProbe
    $passive = Invoke-PassiveJsonProbe
    Add-Check -Name 'unverifiable claim cwd fails closed' `
        -Passed (
            $probe.exit_code -eq 2 -and
            $probe.blocked -and
            $passive.exit_code -eq 2 -and
            $null -ne $passive.report -and
            $passive.report.safe -eq $false -and
            $passive.report.blocking_detail[0].reason -ceq
                'claim_cwd_not_verified_git_worktree'
        ) -Detail (
            "wrapper_exit=$($probe.exit_code) passive_exit=" +
            "$($passive.exit_code)"
        )

    $occupiedDriveNames = @(
        Get-PSDrive -PSProvider FileSystem | ForEach-Object { [string]$_.Name }
    )
    foreach ($driveCode in (90..84)) {
        $candidateName = [string][char]$driveCode
        if ($candidateName -notin $occupiedDriveNames) {
            $substDrive = $candidateName + ':'
            break
        }
    }
    if (-not $substDrive) {
        throw 'no unused drive letter available for isolated SUBST regression'
    }
    $substOutput = @(& subst.exe $substDrive $repoRoot 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw (
            "could not create isolated SUBST mapping $substDrive : " +
            (($substOutput | ForEach-Object { [string]$_ }) -join "`n")
        )
    }
    $substCreated = $true
    $substRoot = $substDrive + [System.IO.Path]::DirectorySeparatorChar
    Clear-TestClaim
    Push-Location -LiteralPath $substRoot
    try {
        $probe = Invoke-GuardProbe
        $passive = Invoke-PassiveJsonProbe
    } finally {
        Pop-Location
    }
    Add-Check -Name 'SUBST worktree alias fails closed' `
        -Passed (
            $probe.exit_code -eq 2 -and
            $probe.blocked -and
            $passive.exit_code -eq 2 -and
            $null -ne $passive.report -and
            $passive.report.safe -eq $false -and
            $passive.report.context_error -ceq
                'current_cwd_not_verified_git_worktree'
        ) -Detail (
            "drive=$substDrive wrapper_exit=$($probe.exit_code) passive_exit=" +
            "$($passive.exit_code)"
        )
} finally {
    if ($substCreated -and $substDrive) {
        $substCleanupOutput = @(& subst.exe $substDrive /D 2>&1)
        if ($LASTEXITCODE -ne 0) {
            $cleanupErrors += (
                "could not remove SUBST mapping $substDrive : " +
                (($substCleanupOutput | ForEach-Object { [string]$_ }) -join "`n")
            )
        }
    }

    if ($null -eq $previousRuntimeRoot) {
        Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue
    } else {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $previousRuntimeRoot
    }

    if (Test-Path -LiteralPath $tempRoot) {
        $auditFull = [System.IO.Path]::GetFullPath($auditRoot).TrimEnd(
            [char[]]@(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
        )
        $tempFull = [System.IO.Path]::GetFullPath($tempRoot)
        $requiredPrefix = $auditFull +
            [System.IO.Path]::DirectorySeparatorChar
        if (-not $tempFull.StartsWith(
            $requiredPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "refusing unsafe smoke cleanup target: $tempFull"
        }
        Remove-Item -LiteralPath $tempFull -Recurse -Force
    }
    if ($cleanupErrors.Count -gt 0) {
        throw ($cleanupErrors -join "`n")
    }
}

Write-Host ''
Write-Host 'Summary' -ForegroundColor Cyan
Write-Host '======='
$failed = @($results | Where-Object { -not $_.passed })
$passed = @($results | Where-Object { $_.passed })
Write-Host ("  passed: {0}" -f $passed.Count) -ForegroundColor Green
if ($failed.Count -gt 0) {
    Write-Host ("  failed: {0}" -f $failed.Count) -ForegroundColor Red
    foreach ($failure in $failed) {
        Write-Host (
            "    - {0}: {1}" -f $failure.name, $failure.detail
        ) -ForegroundColor Red
    }
    exit 1
}
exit 0

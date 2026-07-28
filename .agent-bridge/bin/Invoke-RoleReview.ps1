#requires -Version 5.1
<#
.SYNOPSIS
    R20.5 / R16 — orchestrate three independent role reviews
    (architect / security / reliability) and emit per-role + synthesis
    bridge events.

.DESCRIPTION
    Wraps `orchestrator/Invoke-WaggleReview.ps1` to make the
    "three perspectives" structure operationally real instead of
    theatrical: each role runs as a separate process, each emits a
    separate bridge event, and an optional synthesis pass produces a
    fourth bridge event that references all three sub-task_ids.

    Per the R16 proposal in
    iterations/codex_scout_tasks/r16_proposal_genuine_role_synthesis_2026_05_09.md
    and ratified in
    iterations/codex_scout_tasks/r20_synthesis_2026_05_09.md.

    DryRun mode skips the orchestrator subprocess and emits canned
    "no findings" events so smoke tests can verify the wiring without
    needing a real review.

.PARAMETER Agent
    Which agent is doing the review. Defaults to `claude` so the
    bridge attribution is correct.

.PARAMETER Target
    PR number, branch, iteration id, or any operator-meaningful
    identifier the review attaches to. Used to derive sub-task_ids.

.PARAMETER Roles
    One or more of: architect, security, reliability. Defaults to all
    three.

.PARAMETER Synthesis
    `on` (default) emits one extra synthesis event after the role
    events. `off` skips the synthesis.

.PARAMETER DryRun
    Skip the orchestrator subprocess; emit canned per-role events
    instead. Used by the smoke test.

.PARAMETER OrchestratorScript
    Path to orchestrator/Invoke-WaggleReview.ps1. Defaults to the
    repo-relative path; tests can override.

.PARAMETER OutputDir
    Per-role output directory passed to the orchestrator. Defaults to
    a fresh temp dir under $env:TEMP.

.EXAMPLE
    .\.agent-bridge\bin\Invoke-RoleReview.ps1 -Target "PR-175" -DryRun

.EXAMPLE
    .\.agent-bridge\bin\Invoke-RoleReview.ps1 -Target "PR-175" `
        -Roles architect,security
#>
[CmdletBinding()]
param(
    [string] $Agent = 'claude',

    [Parameter(Mandatory)]
    [string] $Target,

    [string[]] $Roles = @('architect','security','reliability'),

    [ValidateSet('on','off')]
    [string] $Synthesis = 'on',

    [switch] $DryRun,

    [string] $OrchestratorScript = '',

    [string] $OutputDir = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

# ── Resolve helper paths ─────────────────────────────────────────

$bridgeBin = $PSScriptRoot
$repoRoot  = Split-Path -Parent (Split-Path -Parent $bridgeBin)

if (-not $OrchestratorScript) {
    $OrchestratorScript = Join-Path $repoRoot 'orchestrator\Invoke-WaggleReview.ps1'
}

if (-not $OutputDir) {
    $OutputDir = Join-Path $env:TEMP ("invoke-role-review-{0}" -f ([guid]::NewGuid().ToString('N')))
}
if (-not (Test-Path -LiteralPath $OutputDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
}

$writeEvent = Join-Path $bridgeBin 'Write-AgentEvent.ps1'
if (-not (Test-Path -LiteralPath $writeEvent -PathType Leaf)) {
    throw "Write-AgentEvent.ps1 not found at $writeEvent"
}

# ── Validate role list ────────────────────────────────────────────

$validRoles = @('architect','security','reliability')
foreach ($r in $Roles) {
    if ($validRoles -notcontains $r) {
        throw "Invalid role $r. Must be one of: $($validRoles -join ', ')"
    }
}

# ── Run each role independently ──────────────────────────────────

$dateTag = Get-Date -Format 'yyyy-MM-dd'
$baseTaskId = "$Target-role-review-$dateTag"
$verdictsByRole = @{}

foreach ($role in $Roles) {
    $subTaskId = "$baseTaskId-$role"
    $verdict = $null

    if ($DryRun) {
        # Skeleton-mode: skip the orchestrator subprocess and emit a
        # canned no-findings verdict so the bridge wiring is testable
        # without a real review.
        $verdict = @{
            role           = $role
            verdict        = 'approve'
            findings_count = 0
            max_severity   = 'none'
            summary        = "Dry-run for $Target ($role): no findings"
            dry_run        = $true
        }
    } else {
        # Real path: invoke orchestrator/Invoke-WaggleReview.ps1
        # against the operator-supplied target. We deliberately do not
        # parse arbitrary orchestrator outputs here — Stage 1 just
        # records that the subprocess was spawned and what its exit
        # code was. Stage 2 follow-up wires the role's structured
        # verdict JSON into the bridge event payload.
        $perRoleOut = Join-Path $OutputDir $role
        if (-not (Test-Path -LiteralPath $perRoleOut)) {
            New-Item -ItemType Directory -Force -Path $perRoleOut | Out-Null
        }

        $procArgs = @(
            '-NoProfile', '-NonInteractive',
            '-File', $OrchestratorScript,
            '-Role', $role,
            '-OutputDir', $perRoleOut
        )

        $started = Get-Date
        try {
            $proc = Start-Process `
                -FilePath 'powershell.exe' `
                -ArgumentList $procArgs `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput (Join-Path $perRoleOut 'stdout.log') `
                -RedirectStandardError  (Join-Path $perRoleOut 'stderr.log')
            $exitCode = $proc.ExitCode
            $crashed  = $false
        } catch {
            $exitCode = -1
            $crashed  = $true
        }
        $elapsed = (Get-Date) - $started

        $verdict = @{
            role            = $role
            verdict         = if ($exitCode -eq 0) { 'approve' } else { 'finding_open' }
            findings_count  = if ($exitCode -eq 0) { 0 } else { 1 }
            max_severity    = if ($exitCode -eq 0) { 'none' } else { 'unknown' }
            summary         = "Orchestrator review for $Target ($role): exit=$exitCode, elapsed=$([int]$elapsed.TotalSeconds)s"
            dry_run         = $false
            orchestrator_exit_code = $exitCode
            orchestrator_crashed   = $crashed
            output_dir      = $perRoleOut
        }
    }

    # Emit the per-role bridge event
    $msg = "[$role] $($verdict.summary) - verdict=$($verdict.verdict) findings=$($verdict.findings_count)"
    & $writeEvent `
        -Agent $Agent `
        -Type 'message' `
        -Status 'answered' `
        -To 'operator' `
        -Severity 'low' `
        -Message $msg `
        -TaskId $subTaskId | Out-Null

    $verdictsByRole[$role] = $verdict
}

# ── Optional synthesis pass ──────────────────────────────────────

if ($Synthesis -eq 'on') {
    # Identify disagreements: any role with verdict not in {approve}
    $disagreements = @()
    foreach ($role in $Roles) {
        $v = $verdictsByRole[$role].verdict
        if ($v -ne 'approve') {
            $disagreements += "${role}:$v"
        }
    }

    $synthesisVerdict = if ($disagreements.Count -gt 0) {
        'finding_open'
    } else {
        'approve'
    }

    $synthSummary = if ($disagreements.Count -gt 0) {
        "Synthesis disagreement: " + ($disagreements -join '; ')
    } else {
        "Synthesis: all $($Roles.Count) roles approve $Target"
    }

    $synthTaskId = "$baseTaskId-synthesis"
    $subTaskRefs = ($Roles | ForEach-Object { "$baseTaskId-$_" }) -join ', '

    $msg = "$synthSummary | refs=[$subTaskRefs]"
    & $writeEvent `
        -Agent $Agent `
        -Type 'message' `
        -Status 'answered' `
        -To 'operator' `
        -Severity 'low' `
        -Message $msg `
        -TaskId $synthTaskId | Out-Null
}

# ── Emit a structured result on the pipeline ─────────────────────

[pscustomobject]@{
    target             = $Target
    roles              = $Roles
    base_task_id       = $baseTaskId
    verdicts_by_role   = $verdictsByRole
    synthesis_emitted  = ($Synthesis -eq 'on')
    output_dir         = $OutputDir
    dry_run            = [bool]$DryRun
}

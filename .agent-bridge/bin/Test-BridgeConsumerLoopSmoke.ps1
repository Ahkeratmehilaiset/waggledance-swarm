#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True {
    param(
        [Parameter(Mandatory)] [bool] $Condition,
        [Parameter(Mandatory)] [string] $Message
    )
    if (-not $Condition) { throw $Message }
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$auditRoot = Join-Path $repoRoot '.codex-audit'
$tmpRoot = Join-Path $auditRoot ("bridge-consumer-smoke-{0}" -f ([guid]::NewGuid().ToString('N')))
$runtimeRoot = Join-Path $tmpRoot '.agent-bridge'
$worktree = Join-Path $tmpRoot 'codex-tools-1-worktree'
$wakePath = Join-Path $runtimeRoot 'wake_codex-tools-1'

try {
    [void](New-Item -ItemType Directory -Path $runtimeRoot -Force -ErrorAction Stop)
    [void](New-Item -ItemType Directory -Path $worktree -Force -ErrorAction Stop)
    [System.IO.File]::WriteAllText($wakePath, 'wake')

    $script = Join-Path $PSScriptRoot 'Start-AgentBridgeConsumerLoop.ps1'
    $result = @(& $script `
        -Agent codex-tools-1 `
        -AgentUuid '7a8af68d-20bc-4598-9953-23c5dd98b102' `
        -Role tools-tests `
        -Capabilities 'tools,tests,bridge_loop' `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -Model 'test-model' `
        -Sandbox 'danger-full-access' `
        -ApprovalPolicy 'never' `
        -MaxIterations 1 `
        -DurationMinutes 0 `
        -PollSeconds 0 `
        -DryRun)

    Assert-True (@($result).Count -eq 1) 'expected exactly one dry-run iteration'
    $tick = $result[0]
    Assert-True ([bool]$tick.wake_consumed) 'expected wake file to be consumed'
    Assert-True (-not (Test-Path -LiteralPath $wakePath)) 'wake file still exists after consume'
    Assert-True ([bool]$tick.would_run_codex) 'expected default loop to run codex even in dry-run'
    Assert-True (-not [bool]$tick.ran_codex) 'dry-run must not invoke codex'

    $args = @($tick.codex_args)
    Assert-True ($args[0] -eq '--ask-for-approval') 'approval flag must be its own argument'
    Assert-True ($args[1] -eq 'never') 'approval policy must be its own argument'
    Assert-True ($args[2] -eq 'exec') 'codex exec subcommand missing'
    Assert-True ($args[3] -eq '-C') 'worktree flag missing'
    Assert-True ($args[4] -eq $worktree) 'worktree path must be a single argument'
    Assert-True (($args -contains '--sandbox') -and ($args -contains 'danger-full-access')) 'sandbox value must stay intact'
    Assert-True (($args -contains '--model') -and ($args -contains 'test-model')) 'model argument missing'

    $wakeOnly = @(& $script `
        -Agent codex-tools-1 `
        -RuntimeRoot $runtimeRoot `
        -Worktree $worktree `
        -MaxIterations 1 `
        -DurationMinutes 0 `
        -PollSeconds 0 `
        -WakeOnly `
        -DryRun)
    Assert-True (@($wakeOnly).Count -eq 1) 'expected one wake-only dry-run iteration'
    Assert-True (-not [bool]$wakeOnly[0].wake_consumed) 'unexpected wake consumed in no-wake check'
    Assert-True (-not [bool]$wakeOnly[0].would_run_codex) 'WakeOnly must skip codex when no wake exists'
    Assert-True (-not (@($wakeOnly[0].codex_args) -contains '--model')) 'default loop must use Codex config model unless a model is explicit'

    'Bridge consumer loop smoke passed.'
} finally {
    $tmpFull = [System.IO.Path]::GetFullPath($tmpRoot)
    $auditFull = [System.IO.Path]::GetFullPath($auditRoot)
    if ($tmpFull.StartsWith($auditFull, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $tmpFull)) {
        Remove-Item -LiteralPath $tmpFull -Recurse -Force -ErrorAction SilentlyContinue
    }
}

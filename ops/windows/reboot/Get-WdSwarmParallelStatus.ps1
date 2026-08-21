#requires -Version 5.1
<#
.SYNOPSIS
    Reports compact-state, wake, and write-scope parallelism for the WD fleet.

.DESCRIPTION
    Read-only. It does not acknowledge bridge events, consume wake sentinels,
    change Git state, or start processes. Missing or stale compact state is
    reported, never repaired implicitly.
#>
[CmdletBinding()]
param(
    [string] $ManifestPath = '',
    [ValidateRange(60, 86400)]
    [int] $StaleAfterSeconds = 1800,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-WdStatusManifest {
    param([string] $Requested)

    if ($Requested) { return [IO.Path]::GetFullPath($Requested) }
    $pointerPath = 'C:\Python\WD_REBOOT_STATE_CURRENT.json'
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        throw "current reboot pointer is missing: $pointerPath"
    }
    $pointer = Get-Content -LiteralPath $pointerPath -Raw |
        ConvertFrom-Json -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace([string]$pointer.fleet_manifest)) {
        throw 'current reboot pointer has no fleet_manifest'
    }
    return [IO.Path]::GetFullPath([string]$pointer.fleet_manifest)
}

function Get-WdStatusGitText {
    param(
        [Parameter(Mandatory)] [string] $Git,
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $Arguments
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Git --no-replace-objects -C $Worktree @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($exitCode -ne 0) { return '' }
    return (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

$manifestFull = Resolve-WdStatusManifest -Requested $ManifestPath
if (-not (Test-Path -LiteralPath $manifestFull -PathType Leaf)) {
    throw "fleet manifest is missing: $manifestFull"
}
$manifest = Get-Content -LiteralPath $manifestFull -Raw |
    ConvertFrom-Json -ErrorAction Stop
if ([int]$manifest.schema_version -ne 2) {
    throw "unsupported fleet manifest schema: $($manifest.schema_version)"
}
$git = [string]$manifest.git_executable
if (-not (Test-Path -LiteralPath $git -PathType Leaf)) {
    throw "fleet Git executable is missing: $git"
}

$definitions = [Collections.Generic.List[object]]::new()
foreach ($lane in @($manifest.lanes)) {
    $definitions.Add([pscustomobject]@{
        agent = [string]$lane.agent
        worktree = [IO.Path]::GetFullPath([string]$lane.worktree)
    })
}
$definitions.Add([pscustomobject]@{
    agent = [string]$manifest.tools_supervisor.agent
    worktree = [IO.Path]::GetFullPath(
        [string]$manifest.tools_supervisor.worktree
    )
})
if (@($definitions).Count -ne 5) {
    throw 'parallel status requires exactly five unique lane definitions'
}
if (@($definitions.agent | Select-Object -Unique).Count -ne 5) {
    throw 'parallel status lane identities are not unique'
}

$runtimeRoot = [IO.Path]::GetFullPath([string]$manifest.runtime_root)
$now = [DateTimeOffset]::UtcNow
$lanes = [Collections.Generic.List[object]]::new()
$scopeOwners = @{}
foreach ($definition in @($definitions)) {
    $agent = [string]$definition.agent
    $worktree = [string]$definition.worktree
    $statePath = Join-Path $worktree '.codex-audit\wd-current-state.json'
    $stateHealth = 'missing'
    $state = $null
    $ageSeconds = $null
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            $bytes = [IO.File]::ReadAllBytes($statePath)
            if ($bytes.Length -gt 32768) { throw 'state exceeds 32 KiB' }
            $state = [Text.Encoding]::UTF8.GetString($bytes) |
                ConvertFrom-Json -ErrorAction Stop
            $updated = [DateTimeOffset]::Parse(
                [string]$state.updated_at_utc,
                [Globalization.CultureInfo]::InvariantCulture
            ).ToUniversalTime()
            $ageSeconds = [Math]::Max(
                0,
                [int64][Math]::Floor(($now - $updated).TotalSeconds)
            )
            if (
                [string]$state.schema -cne 'wd.lane-current.v1' -or
                [string]$state.agent -cne $agent -or
                -not ([string]$state.worktree).Equals(
                    $worktree,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) {
                throw 'state identity mismatch'
            }
            $stateHealth = if ($ageSeconds -gt $StaleAfterSeconds) {
                'stale'
            } else { 'current' }
        }
        catch {
            $stateHealth = 'invalid'
            $state = $null
        }
    }

    $branch = Get-WdStatusGitText -Git $git -Worktree $worktree -Arguments @(
        'branch', '--show-current'
    )
    $head = Get-WdStatusGitText -Git $git -Worktree $worktree -Arguments @(
        'rev-parse', 'HEAD'
    )
    $scope = if ($null -eq $state) { @() } else {
        @($state.write_scope | ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ })
    }
    foreach ($path in $scope) {
        $key = $path.Replace('/', '\').ToLowerInvariant()
        if (-not $scopeOwners.ContainsKey($key)) {
            $scopeOwners[$key] = [Collections.Generic.List[string]]::new()
        }
        $scopeOwners[$key].Add($agent)
    }
    $status = if ($null -eq $state) { '' } else { [string]$state.status }
    $nextAction = if ($null -eq $state) { '' } else {
        [string]$state.next_action
    }
    $lanes.Add([pscustomobject]@{
        agent = $agent
        state_health = $stateHealth
        age_seconds = $ageSeconds
        task_id = if ($null -eq $state) { '' } else { [string]$state.task_id }
        status = $status
        branch = $branch
        head = $head
        checkpoint_head = if ($null -eq $state) { '' } else {
            [string]$state.head
        }
        head_matches = (
            $null -ne $state -and $head -and
            [string]$state.head -ceq $head
        )
        write_scope = @($scope)
        next_action = $nextAction
        runnable = (
            $null -ne $state -and
            $status -cne 'blocked' -and
            -not [string]::IsNullOrWhiteSpace($nextAction)
        )
        wake_pending = Test-Path -LiteralPath (
            Join-Path $runtimeRoot ("wake_{0}" -f $agent)
        ) -PathType Leaf
    })
}

$collisions = @(
    foreach ($key in @($scopeOwners.Keys | Sort-Object)) {
        $owners = @($scopeOwners[$key] | Select-Object -Unique)
        if ($owners.Count -gt 1) {
            [pscustomobject]@{ write_scope = $key; agents = $owners }
        }
    }
)
$report = [pscustomobject]@{
    schema = 'wd.swarm-parallel-status.v1'
    observed_at_utc = $now.ToString('o')
    manifest = $manifestFull
    stale_after_seconds = $StaleAfterSeconds
    lanes = @($lanes)
    summary = [pscustomobject]@{
        total_lanes = @($lanes).Count
        current_checkpoints = @($lanes | Where-Object {
                $_.state_health -ceq 'current'
            }).Count
        runnable_lanes = @($lanes | Where-Object { $_.runnable }).Count
        blocked_lanes = @($lanes | Where-Object {
                $_.status -ceq 'blocked'
            }).Count
        pending_wakes = @($lanes | Where-Object { $_.wake_pending }).Count
        scope_collisions = $collisions.Count
    }
    scope_collisions = @($collisions)
}

if ($Json) {
    $report | ConvertTo-Json -Depth 8
} else {
    $report.lanes | Format-Table `
        agent, state_health, age_seconds, status, task_id, head_matches,
        runnable, wake_pending -AutoSize
    $report.summary | Format-List
    if ($collisions.Count -gt 0) {
        $collisions | Format-Table write_scope, agents -AutoSize
    }
}

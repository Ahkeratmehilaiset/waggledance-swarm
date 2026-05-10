#requires -Version 5.1
<#
.SYNOPSIS
    R23.1 gate: require process-isolated role-review evidence for risky changes.

.DESCRIPTION
    Classifies changed paths, then checks the bridge event stream for an
    Invoke-RoleReview synthesis event for the supplied target. This turns
    BRIDGE_PROTOCOL rule 7 from "remember to do it" into a scriptable
    pre-merge gate.

    Exit codes:
      0 = gate passed, or review not required for the supplied paths
      2 = role review required but missing/incomplete
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Target,
    [string[]] $ChangedPath = @(),
    [int] $Tail = 50000,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {
    [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $bridgeRoot -Force -ErrorAction Stop)
}
$eventsPath = Join-Path (Join-Path $bridgeRoot 'shared') 'events.jsonl'

function Normalize-PathForGate {
    param([string] $Path)
    $normalized = (($Path -replace '\\','/').Trim()).ToLowerInvariant()
    if ($normalized.StartsWith('./')) {
        $normalized = $normalized.Substring(2)
    }
    return $normalized.TrimStart('/')
}

function Test-RequiresRoleReview {
    param([string[]] $Paths)
    if (@($Paths).Count -eq 0) { return $true }

    $riskyPrefixes = @(
        '.agent-bridge/',
        'waggledance/core/',
        'orchestrator/',
        'schemas/',
        'tools/',
        '.github/workflows/'
    )
    $riskyFiles = @(
        'pyproject.toml',
        'requirements.txt',
        'requirements-dev.txt'
    )

    foreach ($path0 in @($Paths)) {
        $path = Normalize-PathForGate $path0
        if (-not $path) { continue }
        if ($riskyFiles -contains $path) { return $true }
        foreach ($prefix in $riskyPrefixes) {
            if ($path.StartsWith($prefix)) { return $true }
        }
    }
    return $false
}

function Read-BridgeEventObjects {
    param([string] $Path, [int] $MaxLines)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $items }
    $lines = if ($MaxLines -le 0) {
        @(Get-Content -Path $Path -Encoding UTF8)
    } else {
        @(Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8)
    }
    foreach ($line in $lines) {
        if (-not $line) { continue }
        try { [void]$items.Add(($line | ConvertFrom-Json -ErrorAction Stop)) } catch {}
    }
    return $items
}

$requires = Test-RequiresRoleReview -Paths $ChangedPath
$events = @(Read-BridgeEventObjects -Path $eventsPath -MaxLines $Tail)

$escapedTarget = [regex]::Escape($Target)
$synthesis = @(
    $events |
        Where-Object {
            [string]$_.task_id -match "^${escapedTarget}-role-review-\d{4}-\d{2}-\d{2}-synthesis$" -and
            [string]$_.type -eq 'message' -and
            [string]$_.status -eq 'answered'
        } |
        Sort-Object ts_utc |
        Select-Object -Last 1
)

$rolesPresent = @{}
foreach ($role in @('architect','security','reliability')) {
    $rolesPresent[$role] = $false
}

if ($synthesis.Count -gt 0) {
    $msg = [string]$synthesis[-1].message
    foreach ($role in @('architect','security','reliability')) {
        if ($msg -match ([regex]::Escape("-$role"))) {
            $rolesPresent[$role] = $true
        }
    }
}

$missingRoles = @($rolesPresent.Keys | Where-Object { -not $rolesPresent[$_] } | Sort-Object)
$reviewComplete = ($synthesis.Count -gt 0 -and $missingRoles.Count -eq 0)
$passed = (-not $requires) -or $reviewComplete

$result = [pscustomobject]@{
    target = $Target
    changed_paths = @($ChangedPath)
    review_required = [bool]$requires
    passed = [bool]$passed
    synthesis_task_id = if ($synthesis.Count -gt 0) { [string]$synthesis[-1].task_id } else { '' }
    missing_roles = @($missingRoles)
    message = if (-not $requires) {
        'role review not required for supplied paths'
    } elseif ($reviewComplete) {
        'role review synthesis found with architect/security/reliability refs'
    } else {
        'role review required but missing complete synthesis'
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
} else {
    $marker = if ($passed) { 'PASS' } else { 'FAIL' }
    Write-Host ("[{0}] {1}" -f $marker, $result.message)
    $result | Format-List
}

if ($passed) { exit 0 } else { exit 2 }

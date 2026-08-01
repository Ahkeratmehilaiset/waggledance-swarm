#requires -Version 5.1
<#
.SYNOPSIS
    Choose the next safe bridge action for one agent.

.DESCRIPTION
    This compatibility entry point delegates the read-only decision to the
    authoritative Python implementation so PowerShell and Python callers use
    the same parsing, lease, owner-generation, and write-scope rules.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $Agent,

    [int] $Tail = 5000,

    [double] $OpenRequestMaxAgeHours = 12.0,

    [string] $Now = '',

    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$sessionIdentity = Join-Path $PSScriptRoot 'AgentBridgeSessionIdentity.ps1'
. $sessionIdentity
Assert-AgentBridgeSessionIdentity -RequestedAgent $Agent

if (
    [double]::IsNaN($OpenRequestMaxAgeHours) -or
    [double]::IsInfinity($OpenRequestMaxAgeHours) -or
    $OpenRequestMaxAgeHours -le 0
) {
    throw 'OpenRequestMaxAgeHours must be positive'
}

$bridgeRoot = Resolve-AgentBridgeRoot `
    -DefaultRoot (Split-Path -Parent $PSScriptRoot)

$repoRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..')
)
$pythonScript = Join-Path $repoRoot 'tools\bridge_next_action.py'
if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
    throw "authoritative bridge helper is missing: $pythonScript"
}

$pythonCandidates = New-Object System.Collections.Generic.List[string]
if ($env:PYTHON) {
    [void]$pythonCandidates.Add([string]$env:PYTHON)
}
$repoPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $repoPython -PathType Leaf) {
    [void]$pythonCandidates.Add($repoPython)
}
[void]$pythonCandidates.Add('python.exe')
[void]$pythonCandidates.Add('python')

$pythonCommand = $null
foreach ($candidate in $pythonCandidates) {
    $resolved = Get-Command `
        -Name $candidate `
        -CommandType Application `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $resolved) {
        $pythonCommand = [string]$resolved.Source
        break
    }
}
if (-not $pythonCommand) {
    throw 'python executable was not found'
}

$eventsPath = Join-Path (Join-Path $bridgeRoot 'shared') 'events.jsonl'
$pythonArguments = @(
    $pythonScript,
    '--agent',
    $Agent,
    '--bridge-root',
    $bridgeRoot,
    '--events',
    $eventsPath,
    '--tail',
    [string]$Tail,
    '--open-request-max-age-hours',
    $OpenRequestMaxAgeHours.ToString(
        'R',
        [System.Globalization.CultureInfo]::InvariantCulture
    )
)
if ($Now) {
    $pythonArguments += @('--now', $Now)
}
if ($Json) {
    $pythonArguments += '--json'
}

# Keep native non-zero exits available through LASTEXITCODE in PowerShell 7,
# while remaining harmless on Windows PowerShell 5.1.
$PSNativeCommandUseErrorActionPreference = $false
& $pythonCommand $pythonArguments
$pythonExitCode = $LASTEXITCODE
if ($null -eq $pythonExitCode) {
    $pythonExitCode = 1
}
exit $pythonExitCode

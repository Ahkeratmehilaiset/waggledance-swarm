#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (ARCH-011): launch the Operator Cockpit
    HTML in the default browser. Passive UI; no automation, no
    HTTP server.

    Usage:
      orchestrator\Open-WaggleCockpit.ps1
      orchestrator\Open-WaggleCockpit.ps1 -RepoRoot 'C:\Python\project2-master'
#>
[CmdletBinding()]
param(
    [string] $RepoRoot = ''
)

$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}
$cockpitFile = Join-Path $RepoRoot 'review_cockpit.html'
if (-not (Test-Path -LiteralPath $cockpitFile)) {
    throw "review_cockpit.html not found at $cockpitFile"
}
$dataFile = Join-Path $RepoRoot 'state/cockpit_data.json'
if (-not (Test-Path -LiteralPath $dataFile)) {
    Write-Warning ("state/cockpit_data.json not found at $dataFile. The cockpit will display 'could not load' until you run Build-WaggleCockpitData.ps1.")
}
Start-Process $cockpitFile
Write-Host ('Cockpit opened: ' + $cockpitFile)
exit 0

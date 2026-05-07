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
# Phase 2B-R2 (ARCH-005): cockpit HTML now lives under
# orchestrator/cockpit/. Resolve the new path first; fall back to
# the legacy repo-root location only if the new file is absent so
# in-flight worktrees that haven't pulled this commit still work.
$cockpitFile = Join-Path $RepoRoot 'orchestrator/cockpit/review_cockpit.html'
if (-not (Test-Path -LiteralPath $cockpitFile)) {
    $legacyCockpitFile = Join-Path $RepoRoot 'review_cockpit.html'
    if (Test-Path -LiteralPath $legacyCockpitFile) {
        $cockpitFile = $legacyCockpitFile
    } else {
        throw "review_cockpit.html not found at $cockpitFile or $legacyCockpitFile"
    }
}
$dataFile = Join-Path $RepoRoot 'state/cockpit_data.json'
if (-not (Test-Path -LiteralPath $dataFile)) {
    Write-Warning ("state/cockpit_data.json not found at $dataFile. The cockpit will display 'could not load' until you run Build-WaggleCockpitData.ps1.")
}
Start-Process $cockpitFile
Write-Host ('Cockpit opened: ' + $cockpitFile)
exit 0

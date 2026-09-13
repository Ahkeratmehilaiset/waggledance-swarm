#requires -Version 5.1
<# Lead-only on-demand advisory helper. Default is read-only status, not a model call. #>
[CmdletBinding()]
param([string] $PromptPath = '', [string] $TaskId = '', [switch] $Status)
$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $PSScriptRoot 'deployment-manifest.json'
if (-not $env:WD_REBOOT_EXPECTED_MANIFEST_HASH -or
    (Get-FileHash -LiteralPath $manifestPath).Hash -cne $env:WD_REBOOT_EXPECTED_MANIFEST_HASH) {
    throw 'Grok requires the externally anchored installed bundle'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$wrapper = Join-Path $PSScriptRoot 'Invoke-WdBridgePython.ps1'
if ((Get-FileHash -LiteralPath $wrapper).Hash -cne $manifest.files.'Invoke-WdBridgePython.ps1') {
    throw 'Bridge Python wrapper hash mismatch'
}
$arguments = @('--status')
if ($PromptPath -and -not $Status) {
    if (-not $TaskId) { throw 'Lead must supply -TaskId for a consultation' }
    $arguments = @('--prompt-file', ([IO.Path]::GetFullPath($PromptPath)), '--task-id', $TaskId)
}
$previousGeneration = $env:WD_BRIDGE_GENERATION
try {
    $env:WD_BRIDGE_GENERATION = [string]$manifest.source_commit
    & $wrapper -Tool tools/wd_grok_helper.py -VerifyPackage @arguments
} finally {
    $env:WD_BRIDGE_GENERATION = $previousGeneration
}


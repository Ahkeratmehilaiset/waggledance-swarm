#requires -Version 5.1
<#
.SYNOPSIS
    Runs one packaged bridge Python tool from the pinned reboot bundle.

.DESCRIPTION
    This wrapper lives at the root of a commit-addressed reboot bundle. It
    verifies the deployment manifest against its external anchor when the
    launcher exported WD_REBOOT_EXPECTED_MANIFEST_HASH, verifies the tool file
    and the package definition hashes, resolves the trusted per-user
    interpreter, and runs the tool with Python isolation (-S -B, PYTHONPATH =
    pinned code root + pinned site, PYTHONSAFEPATH, PYTHONNOUSERSITE,
    PYTHONDONTWRITEBYTECODE) applied to this process only for the duration of
    the call and restored afterwards. The caller's working directory is
    preserved, so relative paths and the task Git cwd keep their meaning.

.EXAMPLE
    & $env:WD_BRIDGE_PYTHON_WRAPPER tools/bridge_next_action.py --agent fable-5 --json

.NOTES
    The tool's output stays on the pipeline and its exit code is published as
    $LASTEXITCODE. The wrapper deliberately does not call `exit`: a script-level
    exit abandons the caller's assignment and would discard the tool's output.
    From a separate process, call it through -Command and forward the code:
    pwsh -NoProfile -Command "& '<wrapper>' <tool> <args>; exit $LASTEXITCODE".
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidatePattern('^[A-Za-z0-9_./\\-]+\.py$')]
    [string] $Tool,
    [switch] $VerifyPackage,
    [Parameter(ValueFromRemainingArguments)]
    [string[]] $ToolArguments = @()
)

$ErrorActionPreference = 'Stop'

$contextScript = Join-Path $PSScriptRoot 'BridgeCodeContext.ps1'
$manifestPath = Join-Path $PSScriptRoot 'deployment-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "pinned bridge invocation requires a deployed bundle manifest: $manifestPath"
}
$manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $manifestHash = [BitConverter]::ToString($sha.ComputeHash($manifestBytes)).Replace('-', '')
}
finally {
    $sha.Dispose()
}
$expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
if (
    -not [string]::IsNullOrWhiteSpace($expectedManifestHash) -and
    $manifestHash -cne $expectedManifestHash.ToUpperInvariant()
) {
    throw 'pinned bridge invocation found a deployment manifest that differs from its external anchor'
}
$manifestText = [Text.Encoding]::UTF8.GetString($manifestBytes)
if ($manifestText.Length -gt 0 -and $manifestText[0] -eq [char]0xFEFF) {
    $manifestText = $manifestText.Substring(1)
}
$manifest = $manifestText | ConvertFrom-Json -ErrorAction Stop
$contextProperty = $manifest.files.PSObject.Properties['BridgeCodeContext.ps1']
if (
    $null -eq $contextProperty -or
    -not (Test-Path -LiteralPath $contextScript -PathType Leaf) -or
    (Get-FileHash -LiteralPath $contextScript -Algorithm SHA256).Hash -cne
        ([string]$contextProperty.Value).ToUpperInvariant()
) {
    throw 'pinned bridge invocation refuses an unanchored BridgeCodeContext.ps1'
}
. $contextScript

# The packaged tool's own stdout/stderr flow straight through to the
# caller; only its exit code is carried out of band.
Invoke-WdBridgePythonTool `
    -BundleRoot $PSScriptRoot `
    -Tool $Tool `
    -ToolArguments @($ToolArguments) `
    -VerifyPackage:$VerifyPackage
$global:LASTEXITCODE = Get-WdBridgeCodeLastExitCode

#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('codex-lead-1', 'codex-tools-1')]
    [string] $Agent,
    [Parameter(Mandatory)]
    [string] $DeliveryId,
    [Parameter(Mandatory)]
    [string] $BundleRoot,
    [string] $ExpectedManifestHash = $env:WD_REBOOT_EXPECTED_MANIFEST_HASH
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Agent -cnotin @('codex-lead-1', 'codex-tools-1')) {
    throw 'Native wake agent must match its canonical lowercase identity exactly'
}
if ($DeliveryId -cnotmatch '^[0-9a-f]{32}$') {
    throw 'Native wake delivery_id must be 32 lowercase hexadecimal characters'
}
if ($ExpectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$') {
    throw 'Native wake procedure requires a trusted manifest SHA-256 anchor'
}
if (-not [IO.Path]::IsPathRooted($BundleRoot)) {
    throw 'Native wake bundle root must be absolute'
}

$root = [IO.Path]::GetFullPath($BundleRoot).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
if (-not [string]::Equals($root, $BundleRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Native wake bundle root must be fully qualified without implicit drive resolution'
}
if (-not [IO.Directory]::Exists($root)) {
    throw 'Native wake bundle root is missing'
}
$component = $root
while ($component) {
    $componentItem = Get-Item -LiteralPath $component -Force -ErrorAction Stop
    if (($componentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Native wake bundle path contains a reparse point'
    }
    $parent = [IO.Path]::GetDirectoryName($component.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar))
    if (-not $parent -or [string]::Equals($parent, $component, [StringComparison]::OrdinalIgnoreCase)) {
        break
    }
    $component = $parent
}

function Assert-DirectBundleFile {
    param([string] $Root, [string] $Name)
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($Root, $Name))
    if (-not [string]::Equals([IO.Path]::GetDirectoryName($path), $Root, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Native wake bundle file escapes its root'
    }
    if (-not [IO.File]::Exists($path)) {
        throw ('Native wake bundle file is missing: ' + $Name)
    }
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw ('Native wake bundle file is a reparse point: ' + $Name)
    }
    return $path
}

# Avoid a PowerShell 7 parent's PSModulePath breaking Get-FileHash discovery
# in Windows PowerShell 5.1. Match the existing continuity publisher contract.
function Get-NativeWakeBytesHash {
    param([byte[]] $Bytes)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($Bytes)
        return ([BitConverter]::ToString($hash)).Replace('-', '')
    } finally { $sha.Dispose() }
}

$manifestPath = Assert-DirectBundleFile -Root $root -Name 'deployment-manifest.json'
if ((Get-Item -LiteralPath $manifestPath -Force).Length -gt 1048576) {
    throw 'Native wake deployment manifest is oversized'
}
$manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
if ($manifestBytes.Length -gt 1048576) { throw 'Native wake deployment manifest is oversized' }
$manifestHash = Get-NativeWakeBytesHash -Bytes $manifestBytes
if (-not [string]::Equals($manifestHash, $ExpectedManifestHash, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Native wake deployment manifest differs from its trusted anchor'
}
$manifest = [Text.Encoding]::UTF8.GetString($manifestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop
if ($null -eq $manifest -or $manifest.schema_version -ne 1 -or $null -eq $manifest.files) {
    throw 'Native wake deployment manifest has an invalid schema'
}

$procedureName = if ($Agent -ceq 'codex-lead-1') { 'WAKE_PROCEDURE_LEAD.md' } else { 'WAKE_PROCEDURE_TOOLS.md' }
$entries = @($manifest.files.PSObject.Properties | Where-Object { $_.Name -ceq $procedureName })
if ($entries.Count -ne 1 -or [string]$entries[0].Value -cnotmatch '^[0-9A-Fa-f]{64}$') {
    throw ('Native wake procedure is not hash-pinned in the manifest: ' + $procedureName)
}
$procedurePath = Assert-DirectBundleFile -Root $root -Name $procedureName
if ((Get-Item -LiteralPath $procedurePath -Force).Length -gt 65536) {
    throw 'Native wake procedure is oversized'
}
$procedureBytes = [IO.File]::ReadAllBytes($procedurePath)
if ($procedureBytes.Length -gt 65536) { throw 'Native wake procedure is oversized' }
$procedureHash = Get-NativeWakeBytesHash -Bytes $procedureBytes
if (-not [string]::Equals($procedureHash, [string]$entries[0].Value, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Native wake procedure differs from its deployment manifest pin'
}

'Automatic bridge wake for ' + $Agent + '; delivery_id=' + $DeliveryId + '. Read and follow the verified procedure at ' + $procedurePath + ' (SHA256 ' + $procedureHash + ').'

#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ManifestPath,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$ManifestSha256
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Get-ObserverHash {
    param([Parameter(Mandatory)][string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $sha.Dispose(); $stream.Dispose() }
}
if ((Get-ObserverHash $ManifestPath) -ine $ManifestSha256) { throw 'Observer manifest changed' }
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -cne 'wd.capacity-observer-install.v1' -or $manifest.execution_mode -cne 'metadata_only') {
    throw 'Unsupported observer configuration'
}
$root = Split-Path ([IO.Path]::GetFullPath($ManifestPath)) -Parent
foreach ($property in $manifest.files.PSObject.Properties) {
    $path = [IO.Path]::GetFullPath((Join-Path $root $property.Name))
    if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or
        (Get-ObserverHash $path) -ine $property.Value) { throw 'Observer code changed or escaped release' }
}
foreach ($kind in @('python','codex')) {
    if ((Get-ObserverHash $manifest.$kind) -ine $manifest.($kind + '_sha256')) {
        throw 'Observer executable changed; reinstall after verified CLI update'
    }
}
$collector = Join-Path $root 'tools\bridge_capacity_collector.py'
& $manifest.python -E -s -S -B $collector --provider codex --codex-executable $manifest.codex --store $manifest.store --scheduled
exit $LASTEXITCODE


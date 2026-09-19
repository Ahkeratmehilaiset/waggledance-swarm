#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ManifestPath,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$ManifestSha256,
    [ValidateSet('Scheduled','ClaudeHook','ClaudeStatusline')][string]$Mode='Scheduled'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Keep this bootstrap guard local: no unverified helper executes before pin checks.
function Assert-CapacityPath([string]$Path,[string]$Root) {
    $full=[IO.Path]::GetFullPath($Path)
    $boundary=[IO.Path]::GetFullPath($Root).TrimEnd('\','/')
    if(-not $full.Equals($boundary,[StringComparison]::OrdinalIgnoreCase) -and
       -not $full.StartsWith($boundary+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escaped observer root'}
    $walk=$full
    while($walk -and $walk.Length -ge $boundary.Length){
        try{$attributes=[IO.File]::GetAttributes($walk)}
        catch [IO.FileNotFoundException]{$attributes=0}
        catch [IO.DirectoryNotFoundException]{$attributes=0}
        if($attributes -band [IO.FileAttributes]::ReparsePoint){
            throw 'Observer path contains a reparse point'
        }
        $walk=Split-Path $walk -Parent
    }
    return $full
}
function Get-ObserverHash {
    param([Parameter(Mandatory)][string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $sha.Dispose(); $stream.Dispose() }
}
try {
$ManifestPath=Assert-CapacityPath $ManifestPath ([IO.Path]::GetPathRoot($ManifestPath))
if ((Get-ObserverHash $ManifestPath) -ine $ManifestSha256) { throw 'Observer manifest changed' }
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -cne 'wd.capacity-observer-install.v1' -or $manifest.execution_mode -cne 'metadata_only') {
    throw 'Unsupported observer configuration'
}
$root = Split-Path ([IO.Path]::GetFullPath($ManifestPath)) -Parent
foreach ($property in $manifest.files.PSObject.Properties) {
    $path = Assert-CapacityPath (Join-Path $root $property.Name) $root
    if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or
        (Get-ObserverHash $path) -ine $property.Value) { throw 'Observer code changed or escaped release' }
}
foreach ($kind in $(if($Mode -ceq 'Scheduled'){@('python','codex')}else{@('python')})) {
    [void](Assert-CapacityPath $manifest.$kind ([IO.Path]::GetPathRoot($manifest.$kind)))
    if ((Get-ObserverHash $manifest.$kind) -ine $manifest.($kind + '_sha256')) {
        throw 'Observer executable changed; reinstall after verified CLI update'
    }
}
$collector = Join-Path $root 'tools\bridge_capacity_collector.py'
[void](Assert-CapacityPath $manifest.store ([IO.Path]::GetPathRoot($manifest.store)))
if($Mode -cne 'Scheduled'){
    $buffer=New-Object char[] (2097152+1)
    $length=[Console]::In.ReadBlock($buffer,0,$buffer.Length)
    if($length -gt 2097152){throw 'Native metadata input exceeds bound'}
    $payload=New-Object string($buffer,0,$length)
    $OutputEncoding=New-Object Text.UTF8Encoding($false)
    $arguments=@('-E','-s','-S','-B',$collector,'--store',[string]$manifest.store)
    if($Mode -ceq 'ClaudeHook'){$arguments+='--claude-hook'}else{$arguments+=@('--provider','claude','--statusline')}
    $payload | & $manifest.python @arguments
    # Telemetry cannot block a Stop hook or ask the model to continue.
    exit 0
}
& $manifest.python -E -s -S -B $collector --provider codex --codex-executable $manifest.codex --store $manifest.store --scheduled
exit $LASTEXITCODE
} catch {
    if($Mode -cne 'Scheduled'){
        [Console]::Error.WriteLine('WD capacity native metadata unavailable: '+$_.Exception.Message)
        if($Mode -ceq 'ClaudeStatusline'){Write-Output 'WD capacity | observation unavailable'}
        exit 0
    }
    throw
}


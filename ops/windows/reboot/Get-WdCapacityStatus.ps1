#requires -Version 5.1
<# Read-only installed capacity locator. Never collects, repairs or starts agents. #>
[CmdletBinding()]
param([string]$InstallRoot='C:\Python\wd-capacity-observer')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Get-CapacityReadHash([string]$Path) {
    $stream=[IO.File]::OpenRead($Path);$sha=[Security.Cryptography.SHA256]::Create()
    try{return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}
    finally{$sha.Dispose();$stream.Dispose()}
}
function Assert-CapacityReadPath([string]$Path,[string]$Root) {
    $full=[IO.Path]::GetFullPath($Path)
    if(-not $full.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escaped observer root'}
    $walk=$full
    while($walk -and $walk.Length -ge $Root.Length){
        if(Test-Path -LiteralPath $walk){
            if((Get-Item -LiteralPath $walk -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Observer path contains a reparse point'}
        }
        $walk=Split-Path $walk -Parent
    }
    return $full
}
$reason='unverified_capacity_locator'
try {
    $root=[IO.Path]::GetFullPath($InstallRoot).TrimEnd('\','/')
    $pointer=Assert-CapacityReadPath (Join-Path $root 'current.json') $root
    $json=@{ErrorAction='Stop'}
    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$json.DateKind='String'}
    $current=Get-Content -LiteralPath $pointer -Raw|ConvertFrom-Json @json
    if($current.mode -cne 'metadata_only' -or $current.source_commit -cnotmatch '^[a-f0-9]{40}$' -or $current.manifest_sha256 -cnotmatch '^[A-Fa-f0-9]{64}$'){throw 'Invalid observer pointer'}
    $manifestPath=Assert-CapacityReadPath $current.manifest $root
    if((Get-CapacityReadHash $manifestPath) -ine $current.manifest_sha256){throw 'Observer manifest changed'}
    $m=Get-Content -LiteralPath $manifestPath -Raw|ConvertFrom-Json @json
    if($m.schema -cne 'wd.capacity-observer-install.v1' -or $m.execution_mode -cne 'metadata_only' -or $m.source_commit -cne $current.source_commit){throw 'Observer source mismatch'}
    $release=Split-Path $manifestPath -Parent
    foreach($leaf in @('tools\bridge_capacity_collector.py','tools\bridge_capacity_advisor.py','ops\windows\reboot\Get-WdCapacityStatus.ps1')){
        $field=$m.files.PSObject.Properties[$leaf]
        if($null -eq $field){throw 'Required reader file is not pinned'}
    }
    foreach($property in $m.files.PSObject.Properties){
        $path=Assert-CapacityReadPath (Join-Path $release $property.Name) $release
        if((Get-CapacityReadHash $path) -ine $property.Value){throw 'Observer source changed'}
    }
    if((Get-CapacityReadHash $PSCommandPath) -ine $m.files.'ops\windows\reboot\Get-WdCapacityStatus.ps1'){throw 'Reader is not the installed version'}
    if(-not [IO.Path]::IsPathRooted($m.python) -or [IO.Path]::GetExtension($m.python) -ine '.exe' -or
        (Get-CapacityReadHash $m.python) -ine $m.python_sha256){throw 'Reader Python changed'}
    $store=Assert-CapacityReadPath $m.store $root
    if($store -ine (Join-Path $root 'observations.sqlite')){throw 'Unrecognized observation store'}
    $reason='status_unavailable'
    $text=& $m.python -E -s -S -B (Join-Path $release 'tools\bridge_capacity_collector.py') --store $store --status
    $code=$LASTEXITCODE
    $result=$text|ConvertFrom-Json @json
    if($result.schema -cne 'wd.capacity-status.v1' -or $result.execution_allowed -ne $false){throw 'Invalid read-only status result'}
    $result|Add-Member -NotePropertyName installation -NotePropertyValue ([ordered]@{
        source_commit=$current.source_commit;manifest=$manifestPath;manifest_sha256=$current.manifest_sha256;
        status_command=$PSCommandPath;store=$store;source_verified=$true;agent_quota_binding='unverified'})
    $result|ConvertTo-Json -Depth 32
    exit $code
} catch {
    [ordered]@{schema='wd.capacity-status.v1';state='unknown';reason=$reason;observed_at=[datetime]::UtcNow.ToString('o');
        execution_allowed=$false;observations=@();source_verified=$false}|ConvertTo-Json -Depth 4
    exit 2
}

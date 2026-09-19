#requires -Version 5.1
<#
.SYNOPSIS
Install a hash-pinned, Limited, metadata-only capacity observer.
.DESCRIPTION
Default is a plan. -Apply registers a five-minute task and logon trigger.
This does not change the bridge supervisor, models, terminals or Grok budget.
The source tree must be clean; required CI/review gates are the caller's duty.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonExecutable,
    [Parameter(Mandatory)][string]$CodexExecutable,
    [string]$InstallRoot = 'C:\Python\wd-capacity-observer',
    [switch]$Apply
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
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
foreach ($path in @($PythonExecutable, $CodexExecutable)) {
    if (-not [IO.Path]::IsPathRooted($path) -or [IO.Path]::GetExtension($path) -ine '.exe' -or
        -not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Absolute native executable required' }
}
$root = [IO.Path]::GetFullPath($InstallRoot)
if (-not $root.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Persistent C: install required' }
$head = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or (& git -C $repo status --porcelain --untracked-files=no)) {
    throw 'Observer requires a clean committed source tree'
}
$release = Join-Path $root $head
$files = @('tools\bridge_capacity_advisor.py', 'tools\bridge_capacity_collector.py',
           'tools\bridge_capacity_recovery.py', 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1')
$hashes = [ordered]@{}
foreach ($file in $files) { $hashes[$file] = (Get-ObserverHash (Join-Path $repo $file)) }
$manifest = [ordered]@{schema='wd.capacity-observer-install.v1';source_commit=$head;files=$hashes;
    python=$PythonExecutable;python_sha256=(Get-ObserverHash $PythonExecutable);
    codex=$CodexExecutable;codex_sha256=(Get-ObserverHash $CodexExecutable);
    store=(Join-Path $root 'observations.sqlite');execution_mode='metadata_only'}
if (-not $Apply) { $manifest | ConvertTo-Json -Depth 8; return }
if (Test-Path -LiteralPath $release) { throw 'Release directory already exists; verify existing installation instead of overwriting' }
[void](New-Item -ItemType Directory -Path $release -Force)
foreach ($file in $files) {
    $target = Join-Path $release $file
    [void](New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force)
    Copy-Item -LiteralPath (Join-Path $repo $file) -Destination $target
}
$manifestPath = Join-Path $release 'manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$anchor = (Get-ObserverHash $manifestPath)
$runner = Join-Path $release 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1'
$hostPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
    $runner + '" -ManifestPath "' + $manifestPath + '" -ManifestSha256 ' + $anchor
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $hostPath -Argument $arguments -WorkingDirectory $release
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $identity),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$old = Get-ScheduledTask -TaskName 'WD-CapacityObserver' -ErrorAction SilentlyContinue
if ($old) { throw 'WD-CapacityObserver already exists; replacement requires explicit installation reconciliation' }
Register-ScheduledTask -TaskName 'WD-CapacityObserver' -Action $action -Trigger $triggers -Settings $settings -Principal $principal | Out-Null
[pscustomobject]@{source_commit=$head;manifest=$manifestPath;manifest_sha256=$anchor;task='WD-CapacityObserver';mode='metadata_only'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'current.json') -Encoding UTF8
Start-ScheduledTask -TaskName 'WD-CapacityObserver'
Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw


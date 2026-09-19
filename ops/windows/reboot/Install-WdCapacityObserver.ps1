#requires -Version 5.1
<#
.SYNOPSIS
Install a hash-pinned, Limited, metadata-only capacity observer.
.DESCRIPTION
Default is a plan. -Apply registers a minute task and logon trigger;
the collector admits at most one provider request per five minutes.
This does not change the bridge supervisor, models, terminals or Grok budget.
The source tree must be clean; required CI/review gates are the caller's duty.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonExecutable,
    [Parameter(Mandatory)][string]$CodexExecutable,
    [string]$InstallRoot = 'C:\Python\wd-capacity-observer',
    [switch]$Apply,
    [switch]$Update
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
$pythonHash = Get-ObserverHash $PythonExecutable
$codexHash = Get-ObserverHash $CodexExecutable
$releaseId = $head + '-' + $pythonHash.Substring(0,12) + '-' + $codexHash.Substring(0,12)
$release = Join-Path $root $releaseId
$files = @('tools\bridge_capacity_advisor.py', 'tools\bridge_capacity_collector.py',
           'tools\bridge_capacity_recovery.py', 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1')
$hashes = [ordered]@{}
foreach ($file in $files) { $hashes[$file] = (Get-ObserverHash (Join-Path $repo $file)) }
$manifest = [ordered]@{schema='wd.capacity-observer-install.v1';source_commit=$head;files=$hashes;
    python=$PythonExecutable;python_sha256=$pythonHash;
    codex=$CodexExecutable;codex_sha256=$codexHash;
    store=(Join-Path $root 'observations.sqlite');execution_mode='metadata_only'}
if (-not $Apply) { $manifest | ConvertTo-Json -Depth 8; return }
$manifestPath = Join-Path $release 'manifest.json'
$manifestJson = $manifest | ConvertTo-Json -Depth 8
if (Test-Path -LiteralPath $release) {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        (Get-Content -LiteralPath $manifestPath -Raw).Trim() -cne $manifestJson.Trim()) {
        throw 'Existing observer release differs or is incomplete; refusing overwrite'
    }
    foreach ($file in $files) {
        if ((Get-ObserverHash (Join-Path $release $file)) -cne $hashes[$file]) { throw 'Existing observer release changed' }
    }
} else {
    [void](New-Item -ItemType Directory -Path $release -Force)
    foreach ($file in $files) {
        $target = Join-Path $release $file
        [void](New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force)
        Copy-Item -LiteralPath (Join-Path $repo $file) -Destination $target
    }
    $manifestJson | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}
$anchor = (Get-ObserverHash $manifestPath)
$runner = Join-Path $release 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1'
$hostPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
    $runner + '" -ManifestPath "' + $manifestPath + '" -ManifestSha256 ' + $anchor
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $hostPath -Argument $arguments -WorkingDirectory $release
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $identity),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$old = Get-ScheduledTask -TaskName 'WD-CapacityObserver' -ErrorAction SilentlyContinue
if ($old) {
    # Task Scheduler can return DOMAIN\user, user, or a SID for the same owner.
    # Resolve the actual security identity; never authorize by a display name.
    $ownerSid = $null
    try {
        $owner = [string]$old.Principal.UserId
        if ($owner -match '^S-\d-') {
            $ownerSid = ([Security.Principal.SecurityIdentifier]::new($owner)).Value
        } else {
            $ownerSid = ([Security.Principal.NTAccount]::new($owner)).Translate(
                [Security.Principal.SecurityIdentifier]).Value
        }
    } catch { $ownerSid = $null }
    if (@($old.Actions).Count -ne 1 -or $old.Actions[0].Execute -ine $hostPath -or
        $ownerSid -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -or
        [string]$old.Principal.RunLevel -cne 'Limited') {
        throw 'Existing task is not this exact Limited observer; refusing replacement'
    }
    if ($old.Actions[0].Arguments -cne $arguments -or $old.Actions[0].WorkingDirectory -ine $release) {
        if (-not $Update) { throw 'A verified observer update requires -Apply -Update' }
        $current = Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw | ConvertFrom-Json
        $priorManifest = [IO.Path]::GetFullPath([string]$current.manifest)
        if (-not $priorManifest.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or
            (Get-ObserverHash $priorManifest) -cne $current.manifest_sha256) {
            throw 'Previous observer manifest is not verified inside this install root'
        }
        $priorRelease = Split-Path $priorManifest -Parent
        $priorRunner = Join-Path $priorRelease 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1'
        $priorArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
            $priorRunner + '" -ManifestPath "' + $priorManifest + '" -ManifestSha256 ' + $current.manifest_sha256
        if ($old.Actions[0].Arguments -cne $priorArguments -or $old.Actions[0].WorkingDirectory -ine $priorRelease -or
            [string]$old.State -ceq 'Running') { throw 'Existing observer differs or is still running; retry after its bounded invocation ends' }
        $backup = Join-Path $root ('task-before-update-' + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '.xml')
        Export-ScheduledTask -TaskName 'WD-CapacityObserver' | Set-Content -LiteralPath $backup -Encoding UTF8
        Register-ScheduledTask -TaskName 'WD-CapacityObserver' -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
    }
} else {
    Register-ScheduledTask -TaskName 'WD-CapacityObserver' -Action $action -Trigger $triggers -Settings $settings -Principal $principal | Out-Null
}
[pscustomobject]@{source_commit=$head;release_id=$releaseId;manifest=$manifestPath;manifest_sha256=$anchor;task='WD-CapacityObserver';mode='metadata_only'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'current.json') -Encoding UTF8
Start-ScheduledTask -TaskName 'WD-CapacityObserver'
Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw


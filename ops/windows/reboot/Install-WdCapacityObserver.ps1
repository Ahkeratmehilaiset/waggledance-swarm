#requires -Version 5.1
<#
.SYNOPSIS
Install a hash-pinned, Limited, metadata-only capacity observer.
.DESCRIPTION
Default is a plan. -Apply registers a minute task and logon trigger;
the collector admits at most one provider request per five minutes.
The task starts through the hash-pinned GUI-subsystem wd_silent_launch.exe
when it is present, because a console host started by an Interactive task
opens a window before -WindowStyle Hidden can take effect: once a minute.
This does not change the bridge supervisor, models, terminals or Grok budget.
The source tree must be clean; required CI/review gates are the caller's duty.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonExecutable,
    [Parameter(Mandatory)][string]$CodexExecutable,
    [string]$InstallRoot = 'C:\Python\wd-capacity-observer',
    [switch]$Apply,
    [switch]$Update,
    [string]$SilentLauncher = 'C:\Python\wd_silent_launch.exe',
    [string]$SilentLauncherSha256 = '4CD4FBED01E3EAD1C999493212F7499137C0937F597BDD5172C0EFEEDA3F509F'
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
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
[void](Assert-CapacityPath $repo ([IO.Path]::GetPathRoot($repo)))
foreach ($path in @($PythonExecutable, $CodexExecutable)) {
    [void](Assert-CapacityPath $path ([IO.Path]::GetPathRoot($path)))
    if (-not [IO.Path]::IsPathRooted($path) -or [IO.Path]::GetExtension($path) -ine '.exe' -or
        -not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Absolute native executable required' }
}
# The launcher may be absent, but its path is held to the same shape rules.
if (-not [IO.Path]::IsPathRooted($SilentLauncher) -or [IO.Path]::GetExtension($SilentLauncher) -ine '.exe') {
    throw 'Absolute silent launcher .exe path required'
}
[void](Assert-CapacityPath $SilentLauncher ([IO.Path]::GetPathRoot($SilentLauncher)))
$root = [IO.Path]::GetFullPath($InstallRoot)
if (-not $root.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Persistent C: install required' }
[void](Assert-CapacityPath $root ([IO.Path]::GetPathRoot($root)))
[void](Assert-CapacityPath (Join-Path $root 'observations.sqlite') $root)
[void](Assert-CapacityPath (Join-Path $root 'current.json') $root)
$head = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or (& git -C $repo status --porcelain --untracked-files=no)) {
    throw 'Observer requires a clean committed source tree'
}
$pythonHash = Get-ObserverHash $PythonExecutable
$codexHash = Get-ObserverHash $CodexExecutable
$releaseId = $head + '-' + $pythonHash.Substring(0,12) + '-' + $codexHash.Substring(0,12)
$release = Join-Path $root $releaseId
[void](Assert-CapacityPath $release $root)
# bridge_capacity_attribution.py is imported by the collector on the opt-in
# --attribution path, so it must ship and be hashed or that path is inert.
# bridge_model_qualification.py ships hashed for provenance only; nothing in
# the observer runtime imports it and no runtime path is wired to it.
$files = @('tools\bridge_capacity_advisor.py', 'tools\bridge_capacity_collector.py',
           'tools\bridge_capacity_attribution.py', 'tools\bridge_model_qualification.py',
           'tools\bridge_capacity_recovery.py', 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1',
           'ops\windows\reboot\Get-WdCapacityStatus.ps1')
$hashes = [ordered]@{}
foreach ($file in $files) { $hashes[$file] = (Get-ObserverHash (Assert-CapacityPath (Join-Path $repo $file) $repo)) }
$manifest = [ordered]@{schema='wd.capacity-observer-install.v1';source_commit=$head;files=$hashes;
    python=$PythonExecutable;python_sha256=$pythonHash;
    codex=$CodexExecutable;codex_sha256=$codexHash;
    store=(Join-Path $root 'observations.sqlite');execution_mode='metadata_only'}
if (-not $Apply) { $manifest | ConvertTo-Json -Depth 8; return }
$statusCommand=Join-Path (Split-Path $root -Parent) 'Get-WdCapacityStatus.ps1'
[void](Assert-CapacityPath $statusCommand ([IO.Path]::GetPathRoot($statusCommand)))
if((Test-Path -LiteralPath $statusCommand) -and
   (Get-ObserverHash $statusCommand) -ine $hashes['ops\windows\reboot\Get-WdCapacityStatus.ps1']){
    $priorPointer=Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw|ConvertFrom-Json
    $priorPath=Assert-CapacityPath ([string]$priorPointer.manifest) $root
    if(-not $priorPath.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or
       (Get-ObserverHash $priorPath) -ine $priorPointer.manifest_sha256){throw 'Cannot verify prior status command owner'}
    $prior=Get-Content -LiteralPath $priorPath -Raw|ConvertFrom-Json
    $priorField=$prior.files.PSObject.Properties['ops\windows\reboot\Get-WdCapacityStatus.ps1']
    if($null -eq $priorField -or (Get-ObserverHash $statusCommand) -ine $priorField.Value){throw 'Existing status command differs; preserve it'}
}
$manifestPath = Join-Path $release 'manifest.json'
[void](Assert-CapacityPath $manifestPath $release)
$manifestJson = $manifest | ConvertTo-Json -Depth 8
if (Test-Path -LiteralPath $release) {
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        (Get-Content -LiteralPath $manifestPath -Raw).Trim() -cne $manifestJson.Trim()) {
        throw 'Existing observer release differs or is incomplete; refusing overwrite'
    }
    foreach ($file in $files) {
        if ((Get-ObserverHash (Assert-CapacityPath (Join-Path $release $file) $release)) -cne $hashes[$file]) { throw 'Existing observer release changed' }
    }
} else {
    [void](New-Item -ItemType Directory -Path $release -Force)
    foreach ($file in $files) {
        $target = Assert-CapacityPath (Join-Path $release $file) $release
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
# The launcher waits for the host and returns its exit code (measured), so
# IgnoreNew still prevents overlapping runs. The task's two-minute limit does
# NOT bound the observer through the launcher: on expiry Task Scheduler ends
# the launcher and the host keeps running (measured 2026-09-26). The real
# bound is the collector's own 45-second whole-call timeout. The launcher's
# hash is verified here at install time only, like the runner. A present
# launcher with the wrong hash is refused; an absent one keeps the direct host
# for a new or direct task and refuses to downgrade a silent one.
$silentPrefix = '"' + $hostPath + '" '
$taskExecute = $hostPath
$taskArguments = $arguments
if (Test-Path -LiteralPath $SilentLauncher -PathType Leaf) {
    if ((Get-ObserverHash $SilentLauncher) -ine $SilentLauncherSha256) {
        throw 'Silent task launcher integrity mismatch; refusing registration'
    }
    $taskExecute = $SilentLauncher
    $taskArguments = $silentPrefix + $arguments
} else {
    Write-Warning 'wd_silent_launch.exe is absent; the observer task will flash a console window each minute'
}
# Both registered shapes wrap the same observer invocation. Return that inner
# invocation, or $null for any action this installer did not register.
function Get-ObserverInvocation($TaskAction) {
    if ($TaskAction.Execute -ieq $hostPath) { return [string]$TaskAction.Arguments }
    if ($TaskAction.Execute -ieq $SilentLauncher -and
        ([string]$TaskAction.Arguments).StartsWith($silentPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        return ([string]$TaskAction.Arguments).Substring($silentPrefix.Length)
    }
    return $null
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $taskExecute -Argument $taskArguments -WorkingDirectory $release
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
    $oldInvocation = if (@($old.Actions).Count -eq 1) { Get-ObserverInvocation $old.Actions[0] } else { $null }
    if ($null -eq $oldInvocation -or
        $ownerSid -cne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -or
        [string]$old.Principal.RunLevel -cne 'Limited') {
        throw 'Existing task is not this exact Limited observer; refusing replacement'
    }
    if ($old.Actions[0].Execute -ieq $SilentLauncher -and $taskExecute -ieq $hostPath) {
        # Match Set-WdTaskConsoleContainment.ps1: a missing launcher must not
        # quietly turn a silent task back into a once-a-minute console flash.
        throw 'Silent task launcher is missing; refusing to downgrade the observer task'
    }
    if ($oldInvocation -ceq $arguments -and $old.Actions[0].WorkingDirectory -ieq $release -and
        $old.Actions[0].Execute -ine $taskExecute) {
        # Same release, only the launch shape differs: no code changes, so this
        # needs no -Update, but keep the prior registration as a backup.
        if ([string]$old.State -ceq 'Running') { throw 'Existing observer is still running; retry after its bounded invocation ends' }
        $backup = Join-Path $root ('task-before-launch-shape-' + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '.xml')
        Export-ScheduledTask -TaskName 'WD-CapacityObserver' | Set-Content -LiteralPath $backup -Encoding UTF8
        Register-ScheduledTask -TaskName 'WD-CapacityObserver' -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
    } elseif ($oldInvocation -cne $arguments -or $old.Actions[0].WorkingDirectory -ine $release) {
        if (-not $Update) { throw 'A verified observer update requires -Apply -Update' }
        $current = Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw | ConvertFrom-Json
        $priorManifest = Assert-CapacityPath ([string]$current.manifest) $root
        if (-not $priorManifest.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or
            (Get-ObserverHash $priorManifest) -cne $current.manifest_sha256) {
            throw 'Previous observer manifest is not verified inside this install root'
        }
        $priorRelease = Split-Path $priorManifest -Parent
        $priorRunner = Join-Path $priorRelease 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1'
        $priorArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
            $priorRunner + '" -ManifestPath "' + $priorManifest + '" -ManifestSha256 ' + $current.manifest_sha256
        if ($oldInvocation -cne $priorArguments -or $old.Actions[0].WorkingDirectory -ine $priorRelease -or
            [string]$old.State -ceq 'Running') { throw 'Existing observer differs or is still running; retry after its bounded invocation ends' }
        $backup = Join-Path $root ('task-before-update-' + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '.xml')
        Export-ScheduledTask -TaskName 'WD-CapacityObserver' | Set-Content -LiteralPath $backup -Encoding UTF8
        Register-ScheduledTask -TaskName 'WD-CapacityObserver' -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
    }
} else {
    Register-ScheduledTask -TaskName 'WD-CapacityObserver' -Action $action -Trigger $triggers -Settings $settings -Principal $principal | Out-Null
}
Copy-Item -LiteralPath (Join-Path $release 'ops\windows\reboot\Get-WdCapacityStatus.ps1') -Destination $statusCommand -Force
[pscustomobject]@{source_commit=$head;release_id=$releaseId;manifest=$manifestPath;manifest_sha256=$anchor;task='WD-CapacityObserver';mode='metadata_only'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'current.json') -Encoding UTF8
Start-ScheduledTask -TaskName 'WD-CapacityObserver'
Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw


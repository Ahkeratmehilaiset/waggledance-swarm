#requires -Version 5.1
<# Install explicit lane-local metadata hooks, preserving other settings.
The caller must inspect the current settings and supply their exact SHA-256.
Never run this against global settings or unrelated conversations. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Worktree,
    [Parameter(Mandatory)][string]$ManifestPath,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$ManifestSha256,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{64}$|^missing$')][string]$ExpectedSettingsSha256,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Get-CapacityFileHash([string]$Path){
    $stream=[IO.File]::OpenRead($Path);$sha=[Security.Cryptography.SHA256]::Create()
    try{[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}
    finally{$sha.Dispose();$stream.Dispose()}
}
$root=[IO.Path]::GetFullPath($Worktree)
if(-not (Test-Path -LiteralPath (Join-Path $root '.git'))){throw 'Explicit Git worktree required'}
$directory=Join-Path $root '.claude'
$settingsPath=Join-Path $directory 'settings.local.json'
$ownershipPath=Join-Path $directory 'wd-capacity-integration.json'
foreach($path in @($root,$directory,$settingsPath,$ownershipPath)){
    if((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Reparse settings path refused'}
}
$before=if(Test-Path -LiteralPath $settingsPath){Get-CapacityFileHash $settingsPath}else{'missing'}
if($before -ine $ExpectedSettingsSha256){throw 'Settings changed since inspection'}
if((Get-CapacityFileHash $ManifestPath) -ine $ManifestSha256){throw 'Observer manifest changed'}
$manifest=Get-Content -LiteralPath $ManifestPath -Raw|ConvertFrom-Json
if($manifest.schema -cne 'wd.capacity-observer-install.v1' -or $manifest.execution_mode -cne 'metadata_only'){throw 'Unsupported observer'}
$release=Split-Path ([IO.Path]::GetFullPath($ManifestPath)) -Parent
foreach($file in $manifest.files.PSObject.Properties){
    $path=[IO.Path]::GetFullPath((Join-Path $release $file.Name))
    if(-not $path.StartsWith($release+'\',[StringComparison]::OrdinalIgnoreCase) -or (Get-CapacityFileHash $path) -ine $file.Value){throw 'Observer code changed'}
}
$runner=Join-Path $release 'ops\windows\reboot\Invoke-WdCapacityObserver.ps1'
if($null -eq $manifest.files.PSObject.Properties['ops\windows\reboot\Invoke-WdCapacityObserver.ps1']){throw 'Runner not pinned'}
$hostPath=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
# Claude executes command hooks through its shell: use forward slashes, quote all paths.
foreach($path in @($runner,$ManifestPath,$hostPath)){if($path -match '["`$\r\n]'){throw 'Unsafe command path'}}
$command='"'+$hostPath.Replace('\','/')+'" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner.Replace('\','/')+'" -ManifestPath "'+$ManifestPath.Replace('\','/')+'" -ManifestSha256 '+$ManifestSha256
$hookCommand=$command+' -Mode ClaudeHook'
$statusCommand=$command+' -Mode ClaudeStatusline'
$settings=if($before -eq 'missing'){[pscustomobject]@{}}else{Get-Content -LiteralPath $settingsPath -Raw|ConvertFrom-Json}
$owned=if(Test-Path -LiteralPath $ownershipPath){Get-Content -LiteralPath $ownershipPath -Raw|ConvertFrom-Json}else{$null}
if($settings.PSObject.Properties['statusLine']){
    $oldCommand=[string]$settings.statusLine.command
    # First adoption requires the exact settings hash already inspected by caller.
    # Once owned, refuse replacing a statusline changed by another user/tool.
    if($null -ne $owned -and $oldCommand -cne $owned.status_command -and $oldCommand -cne $statusCommand){throw 'Owned statusline changed; preserve it'}
}
if(-not $settings.PSObject.Properties['hooks']){$settings|Add-Member NoteProperty hooks ([pscustomobject]@{})}
foreach($event in @('UserPromptSubmit','Stop','StopFailure')){
    $field=$settings.hooks.PSObject.Properties[$event]
    $groups=@(if($null -ne $field){$field.Value})
    $preserved=@(foreach($group in $groups){
        $kept=@($group.hooks|Where-Object {
            -not ($_.type -ceq 'command' -and ($_.command -ceq $hookCommand -or
                ($null -ne $owned -and $_.command -ceq $owned.hook_command)))
        })
        if($kept.Count){$group.hooks=$kept;$group}
    })
    $preserved+=@([pscustomobject]@{hooks=@([pscustomobject]@{type='command';command=$hookCommand;timeout=15})})
    $settings.hooks|Add-Member NoteProperty $event $preserved -Force
}
$settings|Add-Member NoteProperty statusLine ([pscustomobject]@{type='command';command=$statusCommand}) -Force
if(-not $Apply){[pscustomobject]@{settings=$settingsPath;previous_sha256=$before;hook_command=$hookCommand;status_command=$statusCommand}|ConvertTo-Json;return}
[void][IO.Directory]::CreateDirectory($directory)
$check=if(Test-Path -LiteralPath $settingsPath){Get-CapacityFileHash $settingsPath}else{'missing'}
if($check -ine $before){throw 'Settings changed during plan'}
if($before -ne 'missing'){
    $backup=Join-Path $root ('.codex-audit\capacity-settings-'+[guid]::NewGuid().ToString('N')+'.json')
    [void][IO.Directory]::CreateDirectory((Split-Path $backup -Parent))
    Copy-Item -LiteralPath $settingsPath -Destination $backup
}
$temp=$settingsPath+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
[IO.File]::WriteAllText($temp,($settings|ConvertTo-Json -Depth 64),(New-Object Text.UTF8Encoding($false)))
if(Test-Path -LiteralPath $settingsPath){[IO.File]::Replace($temp,$settingsPath,[NullString]::Value)}else{[IO.File]::Move($temp,$settingsPath)}
[pscustomobject]@{schema='wd.claude-capacity-integration.v1';hook_command=$hookCommand;status_command=$statusCommand;manifest=$ManifestPath;manifest_sha256=$ManifestSha256}|
    ConvertTo-Json|Set-Content -LiteralPath $ownershipPath -Encoding UTF8
[pscustomobject]@{settings=$settingsPath;sha256=(Get-CapacityFileHash $settingsPath);mode='metadata_only'}|ConvertTo-Json

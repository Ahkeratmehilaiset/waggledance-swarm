#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ManifestPath,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$ManifestSha256,
    [ValidateSet('Scheduled','ClaudeHook','ClaudeHookAlert','ClaudeStatusline')][string]$Mode='Scheduled',
    [switch]$PauseNativeCronOnLimit,
    [string]$GuardWorktree='',
    [ValidateSet('','claude-rco-1','claude-rco-2','fable-5')][string]$GuardAgent=''
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
function Get-WdCapacityHookIdentity {
    param($Observation)
    if($Observation.native_thread_id -cnotmatch '^[a-fA-F0-9-]{36}$'){throw 'Invalid native capacity identity'}
    $bin=[IO.Path]::GetFullPath([string]$env:WD_BRIDGE_BIN)
    $bundle=[IO.Path]::GetFullPath((Join-Path $bin '..\..\..'))
    $bridgeManifest=Assert-CapacityPath (Join-Path $bundle 'deployment-manifest.json') $bundle
    if(-not $env:WD_REBOOT_EXPECTED_MANIFEST_HASH -or
       (Get-ObserverHash $bridgeManifest) -ine $env:WD_REBOOT_EXPECTED_MANIFEST_HASH){throw 'Capacity alert has no inherited bridge anchor'}
    $bridge=Get-Content -LiteralPath $bridgeManifest -Raw|ConvertFrom-Json
    foreach($entry in $bridge.files.PSObject.Properties){
        if($entry.Name -notlike 'tools-bootstrap/*'){continue}
        $path=Assert-CapacityPath (Join-Path $bundle $entry.Name) $bundle
        if((Get-ObserverHash $path) -ine $entry.Value){throw 'Capacity alert bridge helper pin changed'}
    }
    if($bin -ine (Join-Path $bundle 'tools-bootstrap\.agent-bridge\bin')){throw 'Invalid bridge helper directory'}
    $evidence=& (Join-Path $bin 'Get-BridgeExecutionEvidence.ps1')|ConvertFrom-Json
    if($evidence.pin_status -cne 'manifest_and_launcher_verified' -or
       $evidence.native_conversation_id -cne $Observation.native_thread_id -or
       $evidence.observed_agent -cnotin @('claude-rco-1','claude-rco-2','fable-5')){throw 'Capacity alert native identity is not verified'}
    [pscustomobject]@{agent=[string]$evidence.observed_agent;bin=$bin}
}
function Update-WdNativeCronGuard {
    param($Observation,[string]$Worktree,[string]$Agent)
    if($null -eq $Observation -or $Observation.hook_event_name -cnotin @('Stop','StopFailure')){return}
    $pause=($Observation.hook_event_name -ceq 'StopFailure' -and $Observation.availability_state -ceq 'rate_limited')
    if(-not $pause -and $Observation.hook_event_name -cne 'Stop'){return}
    $identity=Get-WdCapacityHookIdentity $Observation
    if(-not $Agent -or $identity.agent -cne $Agent){throw 'Cron guard agent mismatch'}
    $root=Assert-CapacityPath $Worktree ([IO.Path]::GetPathRoot($Worktree))
    $path=Assert-CapacityPath (Join-Path $root '.claude\settings.local.json') $root
    $statePath=Assert-CapacityPath (Join-Path $root '.claude\wd-capacity-cron-guard.json') $root
    $mutex=[Threading.Mutex]::new($false,('Local\WD-CapacityCron-'+$Agent))
    $held=$false
    try{
        try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
        if(-not $held){throw 'Cron guard update already running'}
        $before=Get-ObserverHash $path
        $settings=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
        $state=if(Test-Path -LiteralPath $statePath){Get-Content -LiteralPath $statePath -Raw|ConvertFrom-Json}else{$null}
        if($state -and ($state.agent -cne $Agent -or $state.native_thread_id -cne $Observation.native_thread_id)){throw 'Cron pause belongs to another native session; reconcile explicitly'}
        $envField=$settings.PSObject.Properties['env']
        $setting=if($envField){$settings.env.PSObject.Properties['CLAUDE_CODE_DISABLE_CRON']}else{$null}
        if($pause){
            if($state){
                if($setting -and $setting.Value -ceq '1' -and
                   ($state.state -ceq 'paused' -or $before -ceq $state.paused_settings_sha256)){return}
                throw 'Interrupted cron pause needs reconciliation; no automatic overwrite'
            }
            # Never take ownership of an existing user setting, even "0".
            if($setting){throw 'Existing cron override preserved; pause ownership unavailable'}
            if(-not $envField){$settings|Add-Member NoteProperty env ([pscustomobject]@{})}
            $settings.env|Add-Member NoteProperty CLAUDE_CODE_DISABLE_CRON '1'
            $afterBytes=[Text.Encoding]::UTF8.GetBytes(($settings|ConvertTo-Json -Depth 64))
            $sha=[Security.Cryptography.SHA256]::Create()
            try{$afterHash=[BitConverter]::ToString($sha.ComputeHash($afterBytes)).Replace('-','')}finally{$sha.Dispose()}
            $state=[ordered]@{schema='wd.native-cron-guard.v1';agent=$Agent;native_thread_id=$Observation.native_thread_id;
                alert_id=$Observation.alert_id;paused_at=$Observation.observed_at;state='pause_intent';settings_sha256=$before;paused_settings_sha256=$afterHash}
            $bytes=[Text.Encoding]::UTF8.GetBytes(($state|ConvertTo-Json -Compress))
            $stream=[IO.File]::Open($statePath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
        }else{
            if(-not $state){return}
            if($state.state -cne 'paused' -and $before -cne $state.paused_settings_sha256){throw 'Unconfirmed cron ownership; reconcile explicitly'}
            if(-not $setting -or $setting.Value -cne '1'){throw 'Owned cron override changed; preserve settings and reconcile'}
            $settings.env.PSObject.Properties.Remove('CLAUDE_CODE_DISABLE_CRON')
            if(@($settings.env.PSObject.Properties).Count -eq 0){$settings.PSObject.Properties.Remove('env')}
        }
        $temp=$path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
        try{
            [IO.File]::WriteAllText($temp,($settings|ConvertTo-Json -Depth 64),(New-Object Text.UTF8Encoding($false)))
            if((Get-ObserverHash $path) -cne $before){throw 'Cron settings changed concurrently'}
            [IO.File]::Replace($temp,$path,[NullString]::Value)
        }finally{if(Test-Path -LiteralPath $temp){Remove-Item -LiteralPath $temp}}
        if(-not $pause){Remove-Item -LiteralPath $statePath}
        else{$state.state='paused';$state|ConvertTo-Json|Set-Content -LiteralPath $statePath -Encoding UTF8}
    }finally{if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
}
function Publish-WdCapacityAlert {
    param($Observation,[string]$Store)
    if($null -eq $Observation){return}
    if($Observation.hook_event_name -cne 'StopFailure' -or $Observation.alert_id -cnotmatch '^[a-f0-9]{32}$'){throw 'Invalid native capacity alert'}
    $identity=Get-WdCapacityHookIdentity $Observation
    $agent=$identity.agent;$bin=$identity.bin
    $stateRoot=Assert-CapacityPath (Join-Path (Split-Path $Store -Parent) 'bridge-alerts') (Split-Path $Store -Parent)
    [void][IO.Directory]::CreateDirectory($stateRoot)
    $path=Assert-CapacityPath (Join-Path $stateRoot ($Observation.alert_id+'.json')) $stateRoot
    $mutex=[Threading.Mutex]::new($false,('Local\WD-CapacityAlert-'+$Observation.alert_id))
    $held=$false
    try {
        try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
        if(-not $held){return}
        $requestId='capacity-'+$Observation.alert_id
        if(Test-Path -LiteralPath $path){
            $prior=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
            if($prior.request_id -cne $requestId -or $prior.agent -cne $agent -or $prior.native_thread_id -cne $Observation.native_thread_id){throw 'Capacity alert intent identity changed'}
            if($prior.state -ceq 'canonical'){return}
            # A previous send may have appended before its receipt was persisted.
            # Reconcile exact identity; never blindly replay an uncertain send.
            $raw=& (Join-Path $bin 'Read-AgentBridge.ps1') -Raw -NoAckReceived -NoContinuity -Tail 1200 6>$null
            $rows=$raw|ConvertFrom-Json
            $found=@($rows|Where-Object {
                $null -ne $_.PSObject.Properties['request_id'] -and $_.request_id -ceq $requestId -and
                $_.agent -ceq $agent -and $null -ne $_.payload -and
                $null -ne $_.payload.PSObject.Properties['alert_id'] -and $_.payload.alert_id -ceq $Observation.alert_id
            })
            if($found.Count -eq 1){$prior.state='canonical';$prior|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $path -Encoding UTF8}
            else{[Console]::Error.WriteLine('WD capacity alert requires reconciliation: '+$requestId)}
            return
        }
        $record=[ordered]@{schema='wd.capacity-alert-delivery.v1';request_id=$requestId;agent=$agent;
            native_thread_id=$Observation.native_thread_id;state='send_pending';observed_at=$Observation.observed_at}
        $bytes=[Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Compress))
        $stream=[IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
        $payload=[ordered]@{schema='wd.capacity-blocked-notice.v1';alert_id=$Observation.alert_id;
            affected_agent=$agent;native_thread_id=$Observation.native_thread_id;observation=$Observation;
            origin='automatic_native_hook';model_authored=$false;authority_effect='none';requires_reply=$true;
            result_contract=@{schema='wd.task-result-contract.v1';required=@('disposition','next_action');
                types=@{disposition='string';next_action='string'};additional_properties=$false}}
        $message='Automatic native capacity failure observation, not a model-authored task reply. Inspect the exact request and shared capacity status. Preserve pending work; do not treat this as completion or as a failure bound to a particular task. Avoid assigning more work to the blocked lane. Coordinate a suitable existing worker under current operator authority, or report the blocker. No account change, paid credits, model switch, claim release, blind retry or reviewer-authority transfer is authorized by this notice.'
        $receipt=& (Join-Path $bin 'Write-AgentEvent.ps1') -Agent $agent -Type blocked -Status blocked -To codex-lead-1 -TaskId ('capacity/native/'+$Observation.alert_id) -RequestId $requestId -Message $message -PayloadJson ($payload|ConvertTo-Json -Depth 16 -Compress) -ReceiptJson
        $event=$receipt|ConvertFrom-Json
        if($event.request_id -cne $requestId -or $event._bridge_delivery.canonical_durable -ne $true){throw 'Capacity notice canonical receipt unavailable; reconcile intent'}
        $record.state='canonical'
        $record|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $path -Encoding UTF8
    }finally{if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
}
try {
if($PauseNativeCronOnLimit -and ($Mode -cne 'ClaudeHookAlert' -or -not $GuardWorktree -or -not $GuardAgent)){throw 'Explicit native hook guard configuration required'}
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
    if($Mode -cin @('ClaudeHook','ClaudeHookAlert')){$arguments+='--claude-hook'}else{$arguments+=@('--provider','claude','--statusline')}
    if($Mode -ceq 'ClaudeHookAlert'){
        $arguments+=$(if($PauseNativeCronOnLimit){'--emit-lifecycle'}else{'--emit-alert'})
        $nativeOutput=$payload | & $manifest.python @arguments
        if($LASTEXITCODE -ne 0){throw 'Native failure observation failed'}
        if($nativeOutput){
            $observation=$nativeOutput|ConvertFrom-Json
            if($PauseNativeCronOnLimit){
                try{Update-WdNativeCronGuard $observation $GuardWorktree $GuardAgent}
                catch{[Console]::Error.WriteLine('WD native cron guard requires reconciliation: '+$_.Exception.Message)}
            }
            if($observation -and $observation.hook_event_name -ceq 'StopFailure'){Publish-WdCapacityAlert -Observation $observation -Store $manifest.store}
        }
    }else{$payload | & $manifest.python @arguments}
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


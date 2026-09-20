#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ManifestPath,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{64}$')][string]$ManifestSha256,
    [ValidateSet('Scheduled','ClaudeHook','ClaudeHookAlert','ClaudeStatusline')][string]$Mode='Scheduled'
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
function Publish-WdCapacityAlert {
    param($Observation,[string]$Store)
    if($null -eq $Observation){return}
    if($Observation.hook_event_name -cne 'StopFailure' -or
       $Observation.alert_id -cnotmatch '^[a-f0-9]{32}$' -or
       $Observation.native_thread_id -cnotmatch '^[a-fA-F0-9-]{36}$'){throw 'Invalid native capacity alert'}
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
    $agent=[string]$evidence.observed_agent
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
        $arguments+='--emit-alert'
        $nativeOutput=$payload | & $manifest.python @arguments
        if($LASTEXITCODE -ne 0){throw 'Native failure observation failed'}
        if($nativeOutput){Publish-WdCapacityAlert -Observation ($nativeOutput|ConvertFrom-Json) -Store $manifest.store}
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


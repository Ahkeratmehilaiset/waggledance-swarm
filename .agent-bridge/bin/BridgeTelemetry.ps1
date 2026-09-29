#requires -Version 5.1
# Local observation sidecars only. Never an ACK, task result, or authorization.
# F1 (default OFF: no live caller passes the new parameters yet; F7/F30 wire them later):
# optional reason/watermark/latency metadata and an explicit turn outcome. Metadata is a
# separate object that never changes the correlation or authority fields, and no
# observation grants processing: an enqueue or a seen event is never "handled".
# Read-only report: tools/bridge_wake_telemetry.py.
function ConvertTo-BridgeObservationMetadata {
    # Only supplied fields are recorded. An invalid value throws before anything is
    # written, so a future caller fails at wiring time instead of recording a guess.
    param([string]$Reason='', [Nullable[long]]$Watermark=$null,
          [Nullable[double]]$LatencyMs=$null, [string]$LatencyBasis='')
    $metadata=[ordered]@{}
    if($Reason){
        if($Reason -cnotmatch '^[a-z][a-z0-9_]{0,63}$'){throw 'Observation reason must be a lowercase token'}
        $metadata['reason']=$Reason
    }
    if($null -ne $Watermark){
        if($Watermark -lt 0){throw 'Observation watermark must be a non-negative byte offset'}
        $metadata['watermark']=[long]$Watermark
    }
    if($null -ne $LatencyMs -or $LatencyBasis){
        if($null -eq $LatencyMs -or -not $LatencyBasis){throw 'Observation latency needs both LatencyMs and LatencyBasis'}
        $ms=[double]$LatencyMs
        if([double]::IsNaN($ms) -or [double]::IsInfinity($ms) -or $ms -lt 0 -or $ms -gt 86400000){
            throw 'Observation latency must be finite and within 0..86400000 ms'
        }
        if($LatencyBasis -cnotmatch '^[a-z][a-z0-9_]{0,63}$'){throw 'Observation latency basis must be a lowercase token'}
        $metadata['latency_ms']=$ms
        $metadata['latency_basis']=$LatencyBasis
    }
    if($metadata.get_Count() -eq 0){return $null}
    return $metadata
}
function ConvertTo-BridgeObservationTime {
    param($Value)
    if($Value -is [datetime] -or $Value -is [datetimeoffset]){return $Value.ToUniversalTime().ToString('o')}
    return [string]$Value
}
function Get-BridgeStageBinding {
    param($Event)
    if($null -eq $Event){return $null}
    if($Event.PSObject.Properties['request_id'] -and $Event.request_id){
        return [pscustomobject]@{request_id=$Event.request_id;
            agent=$(if($Event.PSObject.Properties['agent']){$Event.agent}else{$null});
            session_id=$(if($Event.PSObject.Properties['session_id']){$Event.session_id}else{$null});
            reply_ts_utc=$(if($Event.PSObject.Properties['reply_ts_utc']){ConvertTo-BridgeObservationTime $Event.reply_ts_utc}else{''})}
    }
    if($Event.PSObject.Properties['in_reply_to_request_id'] -and $Event.in_reply_to_request_id -and
       $Event.PSObject.Properties['in_reply_to_requester']){
        $requester=$Event.in_reply_to_requester
        if($null -ne $requester -and $requester.PSObject.Properties['agent'] -and $requester.PSObject.Properties['session_id']){
            return [pscustomobject]@{request_id=$Event.in_reply_to_request_id;agent=$requester.agent;
                session_id=$requester.session_id;reply_ts_utc=$(if($Event.PSObject.Properties['ts_utc']){ConvertTo-BridgeObservationTime $Event.ts_utc}else{''})}
        }
    }
    return $null
}

function Write-BridgeWakeObservation {
    param([string]$Path,[object[]]$Events,
          [string]$Reason='',[Nullable[long]]$Watermark=$null,[Nullable[double]]$LatencyMs=$null,[string]$LatencyBasis='')
    # Validated before any read or write; $null when no metadata was supplied.
    $metadata=ConvertTo-BridgeObservationMetadata -Reason $Reason -Watermark $Watermark -LatencyMs $LatencyMs -LatencyBasis $LatencyBasis
    $bindings=[Collections.Generic.List[object]]::new()
    $complete=$true
    if([IO.File]::Exists($Path)){
        try{
            if((Get-Item -LiteralPath $Path).Length -gt 131072){throw 'Oversized previous wake'}
            $jsonArgs=@{ErrorAction='Stop'}
            if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$jsonArgs.DateKind='String'}
            $previous=Get-Content -LiteralPath $Path -Raw|ConvertFrom-Json @jsonArgs
            if($previous.schema -cne 'wd.bridge-wake-observation.v1'){throw 'Legacy wake'}
            foreach($item in @($previous.requests)){$bindings.Add($item)}
            $complete=[bool]$previous.correlation_complete
        }catch{$complete=$false}
    }
    foreach($event in $Events){
        $bound=Get-BridgeStageBinding $event
        if($null -eq $bound){$complete=$false;continue}
        $bindings.Add([pscustomobject]@{request_id=[string]$bound.request_id;agent=[string]$bound.agent;
            session_id=[string]$bound.session_id;reply_ts_utc=$(if($bound.PSObject.Properties['reply_ts_utc']){[string]$bound.reply_ts_utc}else{''})})
    }
    if($bindings.Count -gt 256){$complete=$false}
    $value=[ordered]@{schema='wd.bridge-wake-observation.v1';observed_at_utc=[datetime]::UtcNow.ToString('o');
        requests=@($bindings|Select-Object -Last 256);correlation_complete=$complete;authority_effect='none'}
    # Describes this wake only: never carried forward from the previous snapshot.
    if($null -ne $metadata){$value['metadata']=$metadata}
    $temp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    try{
        [IO.File]::WriteAllText($temp,($value|ConvertTo-Json -Depth 8 -Compress),(New-Object Text.UTF8Encoding($false)))
        if([IO.File]::Exists($Path)){
            try{[IO.File]::Replace($temp,$Path,[NullString]::Value)}
            catch [IO.FileNotFoundException]{[IO.File]::Move($temp,$Path)}
        }else{[IO.File]::Move($temp,$Path)}
    }finally{if([IO.File]::Exists($temp)){[IO.File]::Delete($temp)}}
}

function Write-BridgeStageObservation {
    param([string]$BridgeRoot, [string]$Stage, $Request, [string]$Target,
          [string]$DeliveryId='', [string]$QueueId='', [string]$ReplyTimestamp='', [string]$ReportReference='',
          [string]$Reason='', [Nullable[long]]$Watermark=$null, [Nullable[double]]$LatencyMs=$null,
          [string]$LatencyBasis='', [string]$ActionOutcome='')
    if ($Stage -cnotin @('request_durable','watcher_seen','relay_enqueued','model_turn_started','answer_durable','lead_processed','user_reported','turn_completed')) {
        throw 'Unknown bridge observation stage'
    }
    # turn_completed is the ONLY source of a no-op ratio: the agent states explicitly
    # whether its turn acted. Nothing else (a pending or missing stage, a queue
    # acceptance) may be read as a no-op.
    $binding = if ($null -ne $Request) { Get-BridgeStageBinding $Request } else { $null }
    if ($Stage -ceq 'turn_completed') {
        if ($ActionOutcome -cnotin @('acted','noop')) { throw 'turn_completed needs ActionOutcome acted or noop' }
        # One outcome per TURN, checked AFTER binding exactly as the reader keys it: a per-turn
        # DeliveryId (the relay's per-wake id, or one minted once at model_turn_started), or a
        # bound Request with its requester session AND the reply this turn wrote. A request
        # alone names the request, not the turn (RCO1 SF1/SF2). Nothing is dropped silently:
        # a Request that does not bind throws even when a DeliveryId is present.
        if ($null -ne $Request -and $null -eq $binding) { throw 'turn_completed Request does not bind a request' }
        if (-not $DeliveryId) {
            $reply = if ($ReplyTimestamp) { $ReplyTimestamp } elseif ($null -ne $binding) { [string]$binding.reply_ts_utc } else { '' }
            if ($null -eq $binding -or -not [string]$binding.session_id -or -not $reply) {
                throw 'turn_completed needs a turn identity: DeliveryId, or a bound Request with its session and reply'
            }
        }
    } elseif ($ActionOutcome) {
        throw 'ActionOutcome is recorded only with stage turn_completed'
    }
    $metadata = ConvertTo-BridgeObservationMetadata -Reason $Reason -Watermark $Watermark -LatencyMs $LatencyMs -LatencyBasis $LatencyBasis
    $observation = [ordered]@{
        schema='wd.bridge-stage.v1'; stage=$Stage; observed_at_utc=[datetime]::UtcNow.ToString('o');
        target=$Target; request_id=$null; requester=$null; requester_session_id=$null;
        delivery_id=$DeliveryId; queue_id=$QueueId; observer_pid=$PID; authority_effect='none'
        observation_source=$(if ($Stage -cin @('model_turn_started','lead_processed','user_reported','turn_completed')) {'agent_reported'} else {'runtime_observed'})
        reply_ts_utc=$ReplyTimestamp; report_reference=$ReportReference
    }
    # Optional fields are appended only when present, so existing records keep their shape.
    if ($Stage -ceq 'turn_completed') { $observation['action_outcome'] = $ActionOutcome }
    if ($null -ne $metadata) { $observation['metadata'] = $metadata }
    if ($null -ne $Request) {
        # Flow stages stay best-effort: an unbindable request records nothing (turn_completed threw above).
        if($null -eq $binding){return}
        foreach ($pair in @(@('request_id','request_id'),@('requester','agent'),@('requester_session_id','session_id'))) {
            $property = $binding.PSObject.Properties[$pair[1]]
            if ($null -ne $property) { $observation[$pair[0]] = $property.Value }
        }
        if (-not $observation.request_id) { return }
        if(-not $ReplyTimestamp -and $binding.PSObject.Properties['reply_ts_utc'] -and $binding.reply_ts_utc){$observation.reply_ts_utc=[string]$binding.reply_ts_utc}
    }
    $directory = Join-Path $BridgeRoot 'shared\telemetry'
    [void][IO.Directory]::CreateDirectory($directory)
    $path = Join-Path $directory ('stage-' + [guid]::NewGuid().ToString('N') + '.json')
    # Atomic: a reader sees either no stage-*.json or the complete record. The temporary
    # name ends in .tmp, which the stage-*.json readers never match.
    $temp = $path + '.tmp'
    try {
        [IO.File]::WriteAllText($temp, ($observation | ConvertTo-Json -Compress -Depth 4), (New-Object Text.UTF8Encoding($false)))
        [IO.File]::Move($temp, $path)
    } finally {
        if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
    }
}

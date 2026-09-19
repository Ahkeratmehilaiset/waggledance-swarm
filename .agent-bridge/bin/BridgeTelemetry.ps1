#requires -Version 5.1
# Local observation sidecars only. Never an ACK, task result, or authorization.
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
    param([string]$Path,[object[]]$Events)
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
          [string]$DeliveryId='', [string]$QueueId='', [string]$ReplyTimestamp='', [string]$ReportReference='')
    if ($Stage -cnotin @('request_durable','watcher_seen','relay_enqueued','model_turn_started','answer_durable','lead_processed','user_reported')) {
        throw 'Unknown bridge observation stage'
    }
    $observation = [ordered]@{
        schema='wd.bridge-stage.v1'; stage=$Stage; observed_at_utc=[datetime]::UtcNow.ToString('o');
        target=$Target; request_id=$null; requester=$null; requester_session_id=$null;
        delivery_id=$DeliveryId; queue_id=$QueueId; observer_pid=$PID; authority_effect='none'
        observation_source=$(if ($Stage -cin @('model_turn_started','lead_processed','user_reported')) {'agent_reported'} else {'runtime_observed'})
        reply_ts_utc=$ReplyTimestamp; report_reference=$ReportReference
    }
    if ($null -ne $Request) {
        $Request=Get-BridgeStageBinding $Request
        if($null -eq $Request){return}
        foreach ($pair in @(@('request_id','request_id'),@('requester','agent'),@('requester_session_id','session_id'))) {
            $property = $Request.PSObject.Properties[$pair[1]]
            if ($null -ne $property) { $observation[$pair[0]] = $property.Value }
        }
        if (-not $observation.request_id) { return }
        if(-not $ReplyTimestamp -and $Request.PSObject.Properties['reply_ts_utc'] -and $Request.reply_ts_utc){$observation.reply_ts_utc=[string]$Request.reply_ts_utc}
    }
    $directory = Join-Path $BridgeRoot 'shared\telemetry'
    [void][IO.Directory]::CreateDirectory($directory)
    $path = Join-Path $directory ('stage-' + [guid]::NewGuid().ToString('N') + '.json')
    [IO.File]::WriteAllText($path, ($observation | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
}

#requires -Version 5.1
# Local observation sidecars only. Never an ACK, task result, or authorization.
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
        foreach ($pair in @(@('request_id','request_id'),@('requester','agent'),@('requester_session_id','session_id'))) {
            $property = $Request.PSObject.Properties[$pair[1]]
            if ($null -ne $property) { $observation[$pair[0]] = $property.Value }
        }
        if (-not $observation.request_id) { return }
    }
    $directory = Join-Path $BridgeRoot 'shared\telemetry'
    [void][IO.Directory]::CreateDirectory($directory)
    $path = Join-Path $directory ('stage-' + [guid]::NewGuid().ToString('N') + '.json')
    [IO.File]::WriteAllText($path, ($observation | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
}

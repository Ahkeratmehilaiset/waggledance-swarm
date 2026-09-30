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
        # \z, not $: .NET $ also matches before a final newline, which the reader's fullmatch refuses.
        if($Reason -cnotmatch '^[a-z][a-z0-9_]{0,63}\z'){throw 'Observation reason must be a lowercase token'}
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
        if($LatencyBasis -cnotmatch '^[a-z][a-z0-9_]{0,63}\z'){throw 'Observation latency basis must be a lowercase token'}
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

function Test-BridgeObservationPrintable {
    # Python str.isprintable(): no Unicode 'Other' (Cc Cf Cs Co Cn) or 'Separator' (Zl Zp Zs)
    # code point except the ASCII space. Surrogate pairs are one code point.
    param([string]$Value)
    $index = 0
    while ($index -lt $Value.Length) {
        $category = [string][Globalization.CharUnicodeInfo]::GetUnicodeCategory($Value, $index)
        if ($category -in @('Control','Format','Surrogate','PrivateUse','OtherNotAssigned','LineSeparator','ParagraphSeparator')) { return $false }
        if ($category -ceq 'SpaceSeparator' -and $Value[$index] -ne ' ') { return $false }
        if ([char]::IsSurrogatePair($Value, $index)) { $index += 2 } else { $index += 1 }
    }
    return $true
}
function Get-BridgeObservationScalarCount {
    # Python len(): Unicode scalar count, so a surrogate PAIR counts once (a lone surrogate once too).
    param([string]$Value)
    $count = 0
    $index = 0
    while ($index -lt $Value.Length) {
        if ([char]::IsSurrogatePair($Value, $index)) { $index += 2 } else { $index += 1 }
        $count += 1
    }
    return $count
}
function Test-BridgeObservationId {
    # The reader's _bounded_id: a string of at most 256 printable code points, non-empty unless
    # allowed. An EMPTY string is accepted with AllowEmpty exactly as the reader does: Python's
    # ''.isprintable() is True ("or the string is empty"), so _bounded_id('', allow_empty=True)
    # accepts it. $null (absent) is decided by the caller, never here.
    param($Value, [switch]$AllowEmpty)
    return ($Value -is [string] -and ($AllowEmpty -or $Value -ne '') -and
            (Get-BridgeObservationScalarCount $Value) -le 256 -and (Test-BridgeObservationPrintable $Value))
}
function Test-BridgeObservationTime {
    # The reader's parse_utc: at most 40 characters, YYYY-MM-DDTHH:MM:SS[.1-9 digits] then Z or
    # +HH:MM/-HH:MM, a real calendar time, representable in UTC. ASCII digits only.
    param([string]$Value)
    if ($Value.Length -gt 40) { return $false }
    $match = [regex]::Match($Value, '^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})\z')
    if (-not $match.Success) { return $false }
    $local = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($match.Groups[1].Value, 'yyyy-MM-ddTHH:mm:ss',
            [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$local)) { return $false }
    $zone = $match.Groups[3].Value
    if ($zone -ceq 'Z') { return $true }
    $hours = [int]$zone.Substring(1, 2); $minutes = [int]$zone.Substring(4, 2)
    if ($hours -gt 23 -or $minutes -gt 59) { return $false }
    $offsetTicks = ([timespan]::new($hours, $minutes, 0)).Ticks
    if ($zone[0] -eq '-') { $offsetTicks = -$offsetTicks }
    $utcTicks = $local.Ticks - $offsetTicks
    return ($utcTicks -ge [datetime]::MinValue.Ticks -and $utcTicks -le [datetime]::MaxValue.Ticks)
}
function Get-BridgeStageObservationProblem {
    # The first reason tools/bridge_wake_telemetry.validate_stage would discard this record, or ''.
    param([Parameter(Mandatory)] $Observation)
    $target = $Observation['target']
    if (-not ($target -is [string]) -or $target -cnotmatch '^[a-z][a-z0-9-]{0,63}\z') { return 'target must be a lowercase agent id' }
    foreach ($name in @('delivery_id', 'queue_id', 'report_reference')) {
        $value = $Observation[$name]
        if (-not ($value -is [string]) -or (Get-BridgeObservationScalarCount $value) -gt 1024) { return ($name + ' must be a string of at most 1024 characters') }
    }
    if ($null -eq $Observation['request_id']) {
        if ($null -ne $Observation['requester'] -or $null -ne $Observation['requester_session_id']) { return 'a requester needs a request_id' }
    } else {
        if (-not (Test-BridgeObservationId $Observation['request_id'])) { return 'request_id must be a non-empty printable string of at most 256 characters' }
        foreach ($name in @('requester', 'requester_session_id')) {
            $value = $Observation[$name]
            if ($null -ne $value -and -not (Test-BridgeObservationId $value -AllowEmpty)) { return ($name + ' must be a printable string of at most 256 characters') }
        }
    }
    $reply = $Observation['reply_ts_utc']
    if (-not ($reply -is [string]) -or ($reply -and -not (Test-BridgeObservationTime $reply))) {
        return 'reply_ts_utc must be empty or ISO-8601 with a zone (at most 40 characters)'
    }
    return ''
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
    # F1-WRITER-READER (Tools 381ee2ca, 2e4262ab): refuse the scalar/time values the reader's
    # validate_stage would discard, BEFORE any directory or file exists. turn_completed throws
    # visibly; for the live flow stages an invalid final scalar/time value records nothing (the
    # reader would drop it anyway). That skip is ONLY for these values: an unknown stage, an
    # invalid metadata/ActionOutcome argument (validated above) or an I/O failure still throws.
    # Parity is by source reading, not a differential proof (Unicode-database versions differ).
    $problem = Get-BridgeStageObservationProblem -Observation $observation
    if ($problem) {
        if ($Stage -ceq 'turn_completed') { throw ('turn_completed observation refused: ' + $problem) }
        return
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

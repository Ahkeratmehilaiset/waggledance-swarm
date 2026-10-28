#requires -Version 5.1
# Correlation-only contract. Callers also require a substantive terminal event.
function Get-BridgeContractField {
    param($Event, [string]$Name)
    $direct = $Event.PSObject.Properties[$Name]
    $payload = $Event.PSObject.Properties['payload']
    $nested = if ($null -ne $payload -and $null -ne $payload.Value) { $payload.Value.PSObject.Properties[$Name] } else { $null }
    if ($null -ne $direct -and $null -ne $direct.Value -and $null -ne $nested -and $null -ne $nested.Value -and
        (ConvertTo-BridgeContractJson $direct.Value) -cne (ConvertTo-BridgeContractJson $nested.Value)) {
        return [pscustomobject]@{ invalid_binding = $true }
    }
    if ($null -ne $direct -and $null -ne $direct.Value) { return ,$direct.Value }
    if ($null -ne $nested) { return ,$nested.Value }
    return $null
}

function Test-BridgeContractFieldConflict {
    param($Event, [string]$Name)
    $direct = $Event.PSObject.Properties[$Name]
    $payload = $Event.PSObject.Properties['payload']
    $nested = if ($null -ne $payload -and $null -ne $payload.Value) { $payload.Value.PSObject.Properties[$Name] } else { $null }
    return ($null -ne $direct -and $null -ne $direct.Value -and $null -ne $nested -and $null -ne $nested.Value -and
        (ConvertTo-BridgeContractJson $direct.Value) -cne (ConvertTo-BridgeContractJson $nested.Value))
}

function ConvertTo-BridgeContractJson {
    param($Value)
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [string] -or $Value -is [ValueType]) { return ConvertTo-Json -InputObject $Value -Compress }
    if ($Value -is [System.Collections.IDictionary]) {
        $pairs = @($Value.Keys | Sort-Object -CaseSensitive | ForEach-Object {
            (ConvertTo-Json -InputObject ([string]$_) -Compress) + ':' + (ConvertTo-BridgeContractJson $Value[$_])
        })
        return '{' + ($pairs -join ',') + '}'
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        return '[' + (@($Value | ForEach-Object { ConvertTo-BridgeContractJson $_ }) -join ',') + ']'
    }
    $pairs = @($Value.PSObject.Properties | Sort-Object Name -CaseSensitive | ForEach-Object {
        (ConvertTo-Json -InputObject $_.Name -Compress) + ':' + (ConvertTo-BridgeContractJson $_.Value)
    })
    return '{' + ($pairs -join ',') + '}'
}

function Get-BridgeRequestContent {
    param($Request)
    $content = @{}
    foreach ($key in @('request_id','agent','agent_uuid','session_id','run_id','task_id','to','type','status','message','payload','expected_responders')) {
        $property = $Request.PSObject.Properties[$key]
        $content[$key] = if ($null -ne $property) { $property.Value } else { $null }
    }
    return ConvertTo-BridgeContractJson $content
}

function Get-BridgeRequestViewKey {
    param($Request, [string]$Target='')
    $rid = Get-BridgeContractField $Request 'request_id'
    if ($rid) { return "$Target|id|$($Request.agent)|$rid" }
    return "$Target|legacy|$($Request.agent)|$($Request.task_id)"
}

function Set-BridgeRequestViewEntry {
    param($Map, [string]$Key, $Request)
    if ((Get-BridgeContractField $Request 'request_id') -and $Map.ContainsKey($Key)) {
        if ((Get-BridgeRequestContent $Map[$Key]) -cne (Get-BridgeRequestContent $Request) -or
            (Get-BridgeContractField $Map[$Key] 'request_digest') -cne (Get-BridgeContractField $Request 'request_digest')) {
            $Map[$Key] | Add-Member -Force NoteProperty request_binding_conflict $true
        }
    } else { $Map[$Key] = $Request }
}

function New-BridgeRequestIndex {
    param([object[]]$Events)
    $byTask = [System.Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $versions = [System.Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $positions = [System.Collections.Generic.Dictionary[object,int]]::new()
    $position = 0
    foreach ($event in $Events) {
        $positions[$event] = $position
        $position++
        $task = [string]$event.task_id
        if (-not $byTask.ContainsKey($task)) { $byTask[$task] = New-Object System.Collections.Generic.List[object] }
        [void]$byTask[$task].Add($event)
        if (Test-BridgeRequestLikeEvent $event) {
            $key = [string]$event.agent + '|' + $task
            if (-not $versions.ContainsKey($key)) { $versions[$key] = New-Object 'System.Collections.Generic.HashSet[string]' }
            $time = ConvertTo-BridgeContractTime $event.ts_utc
            if ($null -ne $time) { [void]$versions[$key].Add($time.ToString('o')) }
        }
    }
    return [pscustomobject]@{by_task=$byTask;versions=$versions;positions=$positions}
}

function Test-BridgeAmbiguousLegacy {
    param($Index, $Request)
    $key = [string]$Request.agent + '|' + [string]$Request.task_id
    return $Index.versions.ContainsKey($key) -and $Index.versions[$key].Count -gt 1
}

function ConvertTo-BridgeContractTime {
    param($Value)
    if ($Value -is [datetime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) { return $null }
        return $Value.ToUniversalTime()
    }
    if ($Value -is [datetimeoffset]) { return $Value.UtcDateTime }
    if ([string]$Value -notmatch '(Z|[+-]\d\d:\d\d)$') { return $null }
    try { return [datetimeoffset]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime }
    catch { return $null }
}

function Test-BridgeBoundRequest {
    param($Request)
    foreach ($key in @('request_id','nonce','token','task_revision','expected_responders')) {
        if ($null -ne (Get-BridgeContractField $Request $key)) { return $true }
    }
    return $false
}

function Test-BridgeReplyBinding {
    param($Request, $Reply, [string]$Target, [bool]$RequesterClosure=$false, [bool]$AmbiguousLegacy=$false,
        [bool]$RequireExplicitCorrelation=$false, [int]$RequestPosition=-1, [int]$ReplyPosition=-1)
    if ($RequestPosition -ge 0 -or $ReplyPosition -ge 0) {
        if ($RequestPosition -lt 0 -or $ReplyPosition -le $RequestPosition) { return $false }
    }
    # Enforce at the shared boundary, including reader/receipt callers: a later
    # same-task terminal event is not evidence that a control was processed.
    $controlType = ([string](Get-BridgeContractField $Request 'type')).Trim().ToLowerInvariant()
    $controlStatus = ([string](Get-BridgeContractField $Request 'status')).Trim().ToLowerInvariant()
    if ($controlType -in @('decision', 'finding')) {
        foreach ($status in @('changes_requested', 'rco_fail', 'review_failed', 'blocked')) {
            if ($controlStatus -eq $status -or $controlStatus.StartsWith($status + '_')) {
                $RequireExplicitCorrelation = $true
                break
            }
        }
    }
    if (Get-BridgeContractField $Request 'request_binding_conflict') { return $false }
    $requester = [string](Get-BridgeContractField $Request 'agent')
    $author = if ($RequesterClosure) { $requester } else { $Target }
    if ([string](Get-BridgeContractField $Reply 'agent') -cne $author) { return $false }
    if ([string](Get-BridgeContractField $Reply 'task_id') -cne [string](Get-BridgeContractField $Request 'task_id')) { return $false }
    $sent = ConvertTo-BridgeContractTime (Get-BridgeContractField $Request 'ts_utc')
    $answered = ConvertTo-BridgeContractTime (Get-BridgeContractField $Reply 'ts_utc')
    if ($null -eq $sent -or $null -eq $answered -or $answered -le $sent) { return $false }
    $recipients = @(([string](Get-BridgeContractField $Reply 'to') -split ',') | ForEach-Object {$_.Trim()} | Where-Object {$_})
    $recipient = if ($RequesterClosure) {$Target} else {$requester}
    if ($recipients.Count -and $recipients -cnotcontains $recipient) { return $false }
    $rid = Get-BridgeContractField $Request 'request_id'
    if ($null -ne $rid) {
        if ($rid -isnot [string] -or -not $rid -or (Get-BridgeContractField $Reply 'in_reply_to_request_id') -cne $rid) { return $false }
        if ($recipients -cnotcontains $recipient) { return $false }
        $digest = Get-BridgeContractField $Request 'request_digest'
        if ($null -ne $digest -and (Get-BridgeContractField $Reply 'in_reply_to_request_digest') -cne $digest) { return $false }
        $context = Get-BridgeContractField $Reply 'in_reply_to_requester'
        if ($null -eq $context) { return $false }
        foreach ($key in @('agent','agent_uuid','session_id','run_id')) {
            $expected = Get-BridgeContractField $Request $key
            if ($expected -and (Get-BridgeContractField $context $key) -cne $expected) { return $false }
        }
    } elseif ($null -ne (Get-BridgeContractField $Reply 'in_reply_to_request_id')) { return $false }
    $reference = Get-BridgeContractField $Reply 'request_ts_utc'
    if ($null -ne $reference -and (ConvertTo-BridgeContractTime $reference) -ne $sent) { return $false }
    $correlated = $null -ne $reference
    foreach ($key in @('nonce','token','task_revision')) {
        if (Test-BridgeContractFieldConflict $Request $key) { return $false }
        $expected = Get-BridgeContractField $Request $key
        $actual = Get-BridgeContractField $Reply $key
        if ($null -ne $expected) {
            if ((Test-BridgeContractFieldConflict $Reply $key) -or $null -eq $actual -or
                (ConvertTo-BridgeContractJson $actual) -cne (ConvertTo-BridgeContractJson $expected)) { return $false }
            $correlated = $true
        }
    }
    if ($null -eq $rid -and ($AmbiguousLegacy -or $RequireExplicitCorrelation) -and -not $correlated) { return $false }
    $identity = $null
    if ($RequesterClosure) { $identity = $Request }
    else {
        $expected = Get-BridgeContractField $Request 'expected_responders'
        if ($null -ne $expected) {
            $property = $expected.PSObject.Properties[$Target]
            if ($null -eq $property) { return $false }
            $identity = $property.Value
            if ($null -eq $identity) { return $false }
        }
    }
    if ($null -ne $identity) {
        if ($identity -isnot [System.Collections.IDictionary] -and $identity.GetType() -ne [System.Management.Automation.PSCustomObject]) { return $false }
        foreach ($key in @('agent_uuid','session_id','run_id')) {
            $value = Get-BridgeContractField $identity $key
            if (-not $RequesterClosure -and ($value -isnot [string] -or -not $value)) { return $false }
            if ($value -and (Get-BridgeContractField $Reply $key) -cne $value) { return $false }
        }
    }
    return $true
}

# Explicit opt-in producer metadata only. Never changes legacy digest keys,
# routing, vote authority, HOLD or veto classification. No artifact is opened.
function Get-BridgeP1Property {
    param($Object,[string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [Collections.IDictionary]) { return ,$Object[$Name] }
    $property=$Object.PSObject.Properties[$Name]
    if ($null -ne $property) { return ,$property.Value }
    return $null
}

function Test-BridgeLifecycleP1 {
    param($Event)
    if ($null -eq $Event) { return $false }
    $marker=Get-BridgeContractField ([pscustomobject]$Event) 'request_lifecycle'
    if ($null -eq $marker) { return $false }
    if ($marker -isnot [string] -or $marker -cne 'wd.request-lifecycle.p1') {
        throw 'P1 HOLD: unknown or conflicting request_lifecycle'
    }
    return $true
}

function Test-BridgeP1Revision {
    param($Value)
    return (($Value -is [int] -or $Value -is [long]) -and $Value -ge 1 -and $Value -le 2147483647)
}

function Assert-BridgeP1Envelope {
    param($Event)
    if (-not (Test-BridgeLifecycleP1 $Event)) { return }
    $row=[pscustomobject]$Event
    foreach ($name in @('request_lifecycle','task_revision','previous_request_id','previous_request_digest',
            'previous_requester','previous_targets','restart_reason',
            'superseded_by_request_id','superseded_by_task_revision','action_required','fyi','artifact')) {
        if (Test-BridgeContractFieldConflict $row $name) { throw "P1 HOLD: conflicting $name" }
    }
    if (-not (Test-BridgeP1Revision (Get-BridgeContractField $row 'task_revision'))) {
        throw 'P1 HOLD: task_revision must be a positive bounded integer'
    }
    # Request declaration validation must not depend on the routing classifier,
    # which intentionally ignores malformed requests with no recipients.
    if ($null -ne (Get-BridgeContractField $row 'request_id') -and
        @(([string](Get-BridgeContractField $row 'to')).Split(',') |
            ForEach-Object {$_.Trim()} | Where-Object {$_}).Count -eq 0) {
        throw 'P1 HOLD: request requires at least one target'
    }
    $action=Get-BridgeContractField $row 'action_required'
    $fyi=Get-BridgeContractField $row 'fyi'
    if ($action -isnot [bool] -or $fyi -isnot [bool] -or $action -eq $fyi) {
        throw 'P1 HOLD: action_required/fyi must be opposite JSON booleans'
    }
    foreach ($name in @('previous_request_id','superseded_by_request_id')) {
        $value=Get-BridgeContractField $row $name
        if ($null -ne $value -and ($value -isnot [string] -or $value -cnotmatch '^[A-Za-z0-9._:-]{1,128}$')) {
            throw "P1 HOLD: invalid $name"
        }
    }
    $previousDigest=Get-BridgeContractField $row 'previous_request_digest'
    if ($null -ne $previousDigest -and ($previousDigest -isnot [string] -or $previousDigest -cnotmatch '^[a-fA-F0-9]{64}$')) {
        throw 'P1 HOLD: invalid previous_request_digest'
    }
    $nextRevision=Get-BridgeContractField $row 'superseded_by_task_revision'
    if ($null -ne $nextRevision -and -not (Test-BridgeP1Revision $nextRevision)) {
        throw 'P1 HOLD: invalid superseded_by_task_revision'
    }
    $artifact=Get-BridgeContractField $row 'artifact'
    $restartReason=Get-BridgeContractField $row 'restart_reason'
    $previousRequester=Get-BridgeContractField $row 'previous_requester'
    $previousTargets=Get-BridgeContractField $row 'previous_targets'
    if ($null -ne $restartReason -or $null -ne $previousRequester -or $null -ne $previousTargets) {
        if ($restartReason -isnot [string] -or
            $restartReason -cnotin @('requester_session_replaced','responder_session_replaced') -or
            -not (Test-BridgeRequestLikeEvent $row) -or
            ($previousRequester -isnot [pscustomobject] -and $previousRequester -isnot [Collections.IDictionary]) -or
            $previousTargets -isnot [string] -or [string]::IsNullOrWhiteSpace($previousTargets)) {
            throw 'P1 HOLD: restart successor requires its predecessor tuple, targets and a known reason'
        }
        foreach ($name in @('agent','agent_uuid','session_id','run_id')) {
            $value=Get-BridgeP1Property $previousRequester $name
            if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
                throw 'P1 HOLD: restart predecessor identity is incomplete'
            }
        }
    }
    if ($null -ne $artifact) {
        if ($artifact -isnot [pscustomobject] -and $artifact -isnot [Collections.IDictionary]) {
            throw 'P1 HOLD: artifact must be an object'
        }
        $path=Get-BridgeP1Property $artifact 'path'
        $size=Get-BridgeP1Property $artifact 'bytes'
        $sha=Get-BridgeP1Property $artifact 'sha256'
        $owner=Get-BridgeP1Property $artifact 'owner'
        if ($path -isnot [string] -or [string]::IsNullOrWhiteSpace($path) -or
            $path.Length -gt 8192 -or $path.IndexOf([char]0) -ge 0 -or
            $path -cnotmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/]|/[^/])' -or
            ($size -isnot [int] -and $size -isnot [long]) -or $size -lt 0 -or $size -gt 9007199254740991 -or
            $sha -isnot [string] -or $sha -cnotmatch '^[a-fA-F0-9]{64}$' -or
            $owner -isnot [string] -or $owner -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
            throw 'P1 HOLD: artifact requires absolute path, integer bytes, full sha256 and owner'
        }
    }
}

function Initialize-BridgeP1Envelope {
    param([Collections.IDictionary]$Event,$Request=$null,[bool]$IsRequest=$false)
    $optIn=Test-BridgeLifecycleP1 ([pscustomobject]$Event)
    if (-not $optIn -and -not (Test-BridgeLifecycleP1 $Request)) { return }
    $data=[ordered]@{}
    $payload=Get-BridgeP1Property $Event 'payload'
    if ($null -ne $payload) {
        if ($payload -is [Collections.IDictionary]) {
            foreach ($name in $payload.Keys) { $data[$name]=$payload[$name] }
        } elseif ($payload -is [pscustomobject]) {
            foreach ($property in $payload.PSObject.Properties) { $data[$property.Name]=$property.Value }
        } else { throw 'P1 HOLD: metadata needs an object payload' }
    }
    $data['request_lifecycle']='wd.request-lifecycle.p1'
    if (-not $data.Contains('action_required') -and -not $data.Contains('fyi')) {
        $data['action_required']=$IsRequest
        $data['fyi']=-not $IsRequest
    }
    # Structured task replies retain their result, and expose its descriptor as
    # a shape-checked claim, not a verified receipt and never an approval.
    if (-not $data.Contains('artifact') -and -not $IsRequest) {
        $resultArtifact=Get-BridgeP1Property (Get-BridgeP1Property $payload 'result') 'artifact'
        if ($null -ne $resultArtifact) { $data['artifact']=$resultArtifact }
    }
    $Event['payload']=[pscustomobject]$data
    foreach ($name in @('request_lifecycle','task_revision','previous_request_id','previous_request_digest',
            'previous_requester','previous_targets','restart_reason',
            'superseded_by_request_id','superseded_by_task_revision','action_required','fyi','artifact')) {
        if ($data.Contains($name)) {
            $old=Get-BridgeP1Property $Event $name
            if ($null -ne $old -and (ConvertTo-BridgeContractJson $old) -cne (ConvertTo-BridgeContractJson $data[$name])) {
                throw "P1 HOLD: top-level $name conflicts with payload"
            }
            $Event[$name]=$data[$name]
        }
    }
    Assert-BridgeP1Envelope ([pscustomobject]$Event)
    if ($IsRequest) {
        foreach ($name in @('agent','agent_uuid','session_id','run_id','task_id','request_id','nonce')) {
            $value=Get-BridgeContractField ([pscustomobject]$Event) $name
            if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
                throw "P1 HOLD: bound request requires $name"
            }
        }
        $expected=Get-BridgeContractField ([pscustomobject]$Event) 'expected_responders'
        foreach ($target in @(([string]$Event['to']).Split(',') | ForEach-Object {$_.Trim()} | Where-Object {$_})) {
            $identity=Get-BridgeP1Property $expected $target
            foreach ($name in @('agent_uuid','session_id','run_id')) {
                $value=Get-BridgeP1Property $identity $name
                if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
                    throw 'P1 HOLD: expected responder identity is incomplete'
                }
            }
        }
    }
}

function Get-BridgeP1TaskKey {
    param($Event)
    # Logical continuity survives a requester restart or recipient-set change.
    # This key grants no identity authority: exact reply/closure binding stays unchanged.
    return ConvertTo-BridgeContractJson ([ordered]@{agent=[string]$Event.agent;
        task_id=[string]$Event.task_id})
}

function Test-BridgeP1RestartSuccessor {
    # Continuity only. The public writer separately verifies the new actor's
    # current inherited frame. This is never reply/closure or voting authority.
    param($Request,$Successor)
    if (-not (Test-BridgeRequestLikeEvent $Request) -or
        -not (Test-BridgeRequestLikeEvent $Successor) -or
        (Get-BridgeP1TaskKey $Request) -cne (Get-BridgeP1TaskKey $Successor) -or
        -not (Test-BridgeLifecycleP1 $Request) -or -not (Test-BridgeLifecycleP1 $Successor) -or
        $null -eq (Get-BridgeContractField $Successor 'restart_reason')) { return $false }
    Assert-BridgeP1Envelope $Request
    Assert-BridgeP1Envelope $Successor
    if ((Get-BridgeContractField $Request 'request_binding_conflict') -or
        (Get-BridgeContractField $Successor 'request_binding_conflict')) { return $false }
    $previous=Get-BridgeContractField $Successor 'previous_requester'
    $changed=$false
    foreach ($name in @('agent','agent_uuid','session_id','run_id')) {
        $old=Get-BridgeContractField $Request $name
        $current=Get-BridgeContractField $Successor $name
        if ($old -isnot [string] -or [string]::IsNullOrWhiteSpace($old) -or
            $current -isnot [string] -or [string]::IsNullOrWhiteSpace($current) -or
            (Get-BridgeP1Property $previous $name) -cne $old) { return $false }
        if ($name -cne 'agent' -and $old -cne $current) { $changed=$true }
    }
    $oldId=Get-BridgeContractField $Request 'request_id'
    $oldDigest=Get-BridgeContractField $Request 'request_digest'
    $oldRevision=Get-BridgeContractField $Request 'task_revision'
    $oldTime=ConvertTo-BridgeContractTime (Get-BridgeContractField $Request 'ts_utc')
    $newTime=ConvertTo-BridgeContractTime (Get-BridgeContractField $Successor 'ts_utc')
    if ($oldId -isnot [string] -or -not $oldId -or
        $oldDigest -isnot [string] -or $oldDigest -cnotmatch '^[a-fA-F0-9]{64}$' -or
        (Get-BridgeContractField $Successor 'request_id') -ceq $oldId -or
        (Get-BridgeContractField $Successor 'previous_request_id') -cne $oldId -or
        (Get-BridgeContractField $Successor 'previous_request_digest') -cne $oldDigest -or
        -not (Test-BridgeP1Revision $oldRevision) -or
        (Get-BridgeContractField $Successor 'task_revision') -ne ([long]$oldRevision+1) -or
        (Get-BridgeContractField $Successor 'previous_targets') -cne (Get-BridgeContractField $Request 'to') -or
        $null -eq $oldTime -or $null -eq $newTime -or $newTime -le $oldTime) { return $false }
    if ((Get-BridgeContractField $Successor 'restart_reason') -ceq 'requester_session_replaced') {
        return $changed
    }
    $oldResponders=Get-BridgeContractField $Request 'expected_responders'
    $newResponders=Get-BridgeContractField $Successor 'expected_responders'
    $newTargets=@(([string]$Successor.to).Split(',') | ForEach-Object {$_.Trim()} | Where-Object {$_})
    foreach ($target in @(([string]$Request.to).Split(',') | ForEach-Object {$_.Trim()} | Where-Object {$_})) {
        if ($newTargets -cnotcontains $target) { continue }
        $oldIdentity=Get-BridgeP1Property $oldResponders $target
        $newIdentity=Get-BridgeP1Property $newResponders $target
        $complete=$true; $replaced=$false
        foreach ($name in @('agent_uuid','session_id','run_id')) {
            $old=Get-BridgeP1Property $oldIdentity $name
            $current=Get-BridgeP1Property $newIdentity $name
            if ($old -isnot [string] -or [string]::IsNullOrWhiteSpace($old) -or
                $current -isnot [string] -or [string]::IsNullOrWhiteSpace($current)) { $complete=$false }
            elseif ($old -cne $current) { $replaced=$true }
        }
        if ($complete -and $replaced) { return $true }
    }
    return $false
}

function Assert-BridgeP1Transition {
    # Rows must come from a complete frozen canonical prefix, refreshed under
    # publication + append fences with an empty accepted queue. Never an overlay.
    param([object[]]$Rows,$Event,$ClosedRequest=$null)
    $newRequest=Test-BridgeRequestLikeEvent $Event
    $subject=if ($newRequest) { $Event } else { $ClosedRequest }
    if ($null -eq $subject) { throw 'P1 HOLD: closure lacks its full request' }
    $key=Get-BridgeP1TaskKey $subject
    $byId=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $entries=[Collections.Generic.List[object]]::new()
    for ($position=0; $position -lt $Rows.Count; $position++) {
        $row=$Rows[$position]
        if ((Get-BridgeP1TaskKey $row) -cne $key) { continue }
        if (Test-BridgeLifecycleP1 $row) { Assert-BridgeP1Envelope $row }
        if (-not (Test-BridgeRequestLikeEvent $row)) { continue }
        $id=Get-BridgeContractField $row 'request_id'
        if ($id -isnot [string] -or -not $id) { throw 'P1 HOLD: same-key request has unknown binding' }
        if ($byId.ContainsKey($id)) {
            $first=$byId[$id].request
            if ((Get-BridgeRequestContent $first) -cne (Get-BridgeRequestContent $row) -or
                (Get-BridgeContractField $first 'request_digest') -cne (Get-BridgeContractField $row 'request_digest')) {
                throw 'P1 HOLD: conflicting immutable request ID'
            }
            continue
        }
        $entry=[pscustomobject]@{request=$row;position=$position;all_terminal=$true;any_answer=$false;closure=$null}
        foreach ($target in @(([string]$row.to).Split(',') | ForEach-Object {$_.Trim()} | Where-Object {$_})) {
            $terminal=$false
            for ($replyPosition=$position+1; $replyPosition -lt $Rows.Count; $replyPosition++) {
                $reply=$Rows[$replyPosition]
                if ((Get-BridgeContractField $reply 'in_reply_to_request_id') -cne $id) { continue }
                if ((Test-BridgeAnswerEvent $reply) -and
                    (Test-BridgeReplyBinding $row $reply $target -RequestPosition $position -ReplyPosition $replyPosition)) {
                    $entry.any_answer=$true
                    $terminal=$true
                }
                if ((Test-BridgeRequesterClosureEvent $reply) -and
                    (Test-BridgeReplyBinding $row $reply $target -RequesterClosure $true -RequestPosition $position -ReplyPosition $replyPosition)) {
                    $entry.closure=$reply
                    $terminal=$true
                }
            }
            if (-not $terminal) { $entry.all_terminal=$false }
        }
        $byId.Add($id,$entry)
        $entries.Add($entry)
    }
    # Re-derive historical supersession too: otherwise rev2 can publish while
    # the old request still blocks rev3 forever. No answer or closure is made.
    foreach ($entry in $entries) {
        $superseders=@($entries | Where-Object {
            $_.position -gt $entry.position -and
            (Test-BridgeP1RestartSuccessor $entry.request $_.request)
        })
        if ($superseders.Count -gt 1) { throw 'P1 HOLD: conflicting restart successors' }
        if ($superseders.Count -eq 1) { $entry.all_terminal=$true }
    }
    if (-not $newRequest) {
        $id=Get-BridgeContractField $ClosedRequest 'request_id'
        if (-not $byId.ContainsKey($id)) { throw 'P1 HOLD: exact closure request is absent' }
        $entry=$byId[$id]
        if ((Get-BridgeRequestContent $entry.request) -cne (Get-BridgeRequestContent $ClosedRequest) -or
            (Get-BridgeContractField $entry.request 'request_digest') -cne (Get-BridgeContractField $ClosedRequest 'request_digest')) {
            throw 'P1 HOLD: closure request copy differs from canonical'
        }
        # Some targets answering is not terminal for targets still pending.
        if ($null -ne $entry.closure -or $entry.all_terminal) {
            throw 'P1 HOLD: answered or closed revision cannot be superseded again'
        }
        if ($Event.status -ceq 'superseded') {
            $nextId=Get-BridgeContractField $Event 'superseded_by_request_id'
            $nextRevision=Get-BridgeContractField $Event 'superseded_by_task_revision'
            if ($null -eq $nextId -or $nextId -ceq $id -or
                $nextRevision -ne ([long](Get-BridgeContractField $ClosedRequest 'task_revision')+1)) {
                throw 'P1 HOLD: superseded closure requires a distinct linked successor revision'
            }
        }
        return
    }
    $prior=@($entries | Where-Object {Test-BridgeLifecycleP1 $_.request} | Select-Object -Last 1)
    $pending=@($entries | Where-Object {-not $_.all_terminal})
    $restartReason=Get-BridgeContractField $Event 'restart_reason'
    if ($null -ne $restartReason) {
        if (-not $prior.Count -or $prior[0].all_terminal -or
            -not (Test-BridgeP1RestartSuccessor $prior[0].request $Event)) {
            throw 'P1 HOLD: restart successor does not match its nonterminal predecessor'
        }
        $pending=@($pending | Where-Object {$_ -ne $prior[0]})
    }
    if ($pending.Count) {
        throw 'P1 HOLD: one bound request is already active for this logical task'
    }
    $id=Get-BridgeContractField $Event 'request_id'
    if ($byId.ContainsKey($id)) { throw 'P1 HOLD: request ID already published; reconcile, never append a retry' }
    $previousId=Get-BridgeContractField $Event 'previous_request_id'
    $previousDigest=Get-BridgeContractField $Event 'previous_request_digest'
    $revision=Get-BridgeContractField $Event 'task_revision'
    if (-not $prior.Count) {
        if ($revision -ne 1 -or $null -ne $previousId -or $null -ne $previousDigest) {
            throw 'P1 HOLD: first opt-in revision must be 1 without a predecessor'
        }
        return
    }
    $old=$prior[0]
    if ($previousId -cne (Get-BridgeContractField $old.request 'request_id') -or
        $previousDigest -cne (Get-BridgeContractField $old.request 'request_digest') -or
        $revision -ne ([long](Get-BridgeContractField $old.request 'task_revision')+1)) {
        throw 'P1 HOLD: predecessor ID, digest or revision does not match'
    }
    if ($null -ne $old.closure -and $old.closure.status -ceq 'superseded' -and
        ((Get-BridgeContractField $old.closure 'superseded_by_request_id') -cne $id -or
         (Get-BridgeContractField $old.closure 'superseded_by_task_revision') -ne $revision)) {
        throw 'P1 HOLD: successor does not match the explicit closure'
    }
    # Separate closure and request writes are NOT atomic. A stale prepared event
    # is refused rather than silently retimed or published ahead of its predecessor.
    $previousTime=if ($null -ne $old.closure) {$old.closure.ts_utc} else {$old.request.ts_utc}
    if ([DateTimeOffset]::Parse($Event.ts_utc) -le [DateTimeOffset]::Parse($previousTime)) {
        throw 'P1 HOLD: successor timestamp precedes its canonical predecessor'
    }
}

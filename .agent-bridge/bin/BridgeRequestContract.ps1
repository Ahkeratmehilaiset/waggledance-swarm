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
    if ($null -ne $direct -and $null -ne $direct.Value) { return $direct.Value }
    if ($null -ne $nested) { return $nested.Value }
    return $null
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

function Test-BridgeContractValuesDiffer {
    # RCO2 64140a03 F2: PowerShell -cne compares strings culture-sensitively, so a soft hyphen, zero-width
    # character or a decomposed accent can make two different ids, labels or digests equal. Two strings are
    # compared ORDINALLY. RCO2 51de3d6b: any non-string side (array, number, bool, object) is DIFFERENT; -cne on an
    # array filters instead of comparing, so [rid] or [rid, null] used to read as equal. Two $null values (a
    # documented absence on both sides) are the only non-string pair that is not different.
    param($Left, $Right)
    if ($Left -is [string] -and $Right -is [string]) {
        return -not [string]::Equals([string]$Left, [string]$Right, [System.StringComparison]::Ordinal)
    }
    return -not ($null -eq $Left -and $null -eq $Right)
}

function Get-BridgeBindingRawField {
    # Get-BridgeContractField for the binding boundary WITHOUT pipeline unrolling: a one-element array stays an
    # array (return ,$value), so the exact-type checks can refuse it. Same top-level/payload rules and conflict object.
    param($Event, [string]$Name)
    if ($null -eq $Event -or ($Event -isnot [System.Management.Automation.PSCustomObject] -and $Event -isnot [System.Collections.IDictionary])) {
        return $null
    }
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

function Test-BridgeContractListContains {
    # Ordinal membership for the recipient list (replaces the culture-sensitive -cnotcontains).
    param([AllowEmptyCollection()] [object[]] $List, $Value)
    if ($Value -isnot [string]) { return $false }
    foreach ($item in $List) {
        if ($item -isnot [string]) { return $false }
        if ([string]::Equals($item, $Value, [System.StringComparison]::Ordinal)) { return $true }
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
    $requester = Get-BridgeBindingRawField $Request 'agent'
    if ($requester -isnot [string]) { return $false }
    $author = if ($RequesterClosure) { $requester } else { $Target }
    if (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $Reply 'agent') $author) { return $false }
    if (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $Reply 'task_id') (Get-BridgeBindingRawField $Request 'task_id')) { return $false }
    $sent = ConvertTo-BridgeContractTime (Get-BridgeContractField $Request 'ts_utc')
    $answered = ConvertTo-BridgeContractTime (Get-BridgeContractField $Reply 'ts_utc')
    if ($null -eq $sent -or $null -eq $answered -or $answered -le $sent) { return $false }
    $to = Get-BridgeBindingRawField $Reply 'to'
    if ($null -ne $to -and $to -isnot [string]) { return $false }
    $recipients = @(([string]$to -split ',') | ForEach-Object {$_.Trim()} | Where-Object {$_})
    $recipient = if ($RequesterClosure) {$Target} else {$requester}
    if ($recipients.Count -and -not (Test-BridgeContractListContains $recipients $recipient)) { return $false }
    $rid = Get-BridgeBindingRawField $Request 'request_id'
    if ($null -ne $rid) {
        if ($rid -isnot [string] -or -not $rid -or (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $Reply 'in_reply_to_request_id') $rid)) { return $false }
        if (-not (Test-BridgeContractListContains $recipients $recipient)) { return $false }
        $digest = Get-BridgeBindingRawField $Request 'request_digest'
        if ($null -ne $digest -and (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $Reply 'in_reply_to_request_digest') $digest)) { return $false }
        $context = Get-BridgeBindingRawField $Reply 'in_reply_to_requester'
        if ($context -isnot [System.Management.Automation.PSCustomObject] -and $context -isnot [System.Collections.IDictionary]) { return $false }
        foreach ($key in @('agent','agent_uuid','session_id','run_id')) {
            $expected = Get-BridgeBindingRawField $Request $key
            if ($null -ne $expected -and (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $context $key) $expected)) { return $false }
        }
    } elseif ($null -ne (Get-BridgeContractField $Reply 'in_reply_to_request_id')) { return $false }
    $reference = Get-BridgeContractField $Reply 'request_ts_utc'
    if ($null -ne $reference -and (ConvertTo-BridgeContractTime $reference) -ne $sent) { return $false }
    $correlated = $null -ne $reference
    foreach ($key in @('nonce','token','task_revision')) {
        $expected = Get-BridgeBindingRawField $Request $key
        $actual = Get-BridgeBindingRawField $Reply $key
        if ($null -ne $expected) {
            if (($null -eq $rid -or $null -ne $actual) -and (Test-BridgeContractValuesDiffer $actual $expected)) { return $false }
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
            $value = Get-BridgeBindingRawField $identity $key
            if (-not $RequesterClosure -and ($value -isnot [string] -or -not $value)) { return $false }
            if ($null -ne $value -and (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $Reply $key) $value)) { return $false }
        }
    }
    return $true
}

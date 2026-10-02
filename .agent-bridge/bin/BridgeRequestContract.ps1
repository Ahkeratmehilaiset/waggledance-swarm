#requires -Version 5.1
# Correlation-only contract. Callers also require a substantive terminal event.
function New-BridgeBindingConflict {
    # fable-5 60b30839: the top-level and payload copies of a field disagree. The marker keeps its old surface
    # (@{invalid_binding=True}) for consumers, but is recognised by a type name ConvertFrom-Json never produces,
    # so a genuine request value shaped {"invalid_binding": ...} is ordinary data, not a conflict.
    $marker = [pscustomobject]@{ invalid_binding = $true }
    $marker.PSObject.TypeNames.Insert(0, 'WaggleDance.BridgeBindingConflict')
    return $marker
}

function Test-BridgeBindingConflict {
    param($Value)
    return ($Value -is [System.Management.Automation.PSCustomObject] -and
        $Value.PSObject.TypeNames[0] -ceq 'WaggleDance.BridgeBindingConflict')
}

function Get-BridgeContractField {
    param($Event, [string]$Name)
    $direct = $Event.PSObject.Properties[$Name]
    $payload = $Event.PSObject.Properties['payload']
    $nested = if ($null -ne $payload -and $null -ne $payload.Value) { $payload.Value.PSObject.Properties[$Name] } else { $null }
    # Ordinal: culture-sensitive -cne ignores U+00AD (and U+200B in pwsh 7), so such copies used to read as equal.
    if ($null -ne $direct -and $null -ne $direct.Value -and $null -ne $nested -and $null -ne $nested.Value -and
        -not [string]::Equals((ConvertTo-BridgeContractJson $direct.Value), (ConvertTo-BridgeContractJson $nested.Value), [System.StringComparison]::Ordinal)) {
        return (New-BridgeBindingConflict)
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
    # Raw read (no one-element array unrolling) so ["req-1"] can never alias the valid id "req-1".
    $rid = Get-BridgeBindingRawField $Request 'request_id'
    if ($rid -is [string] -and $rid.Length -gt 0) { return "$Target|id|$($Request.agent)|$rid" }
    if ($null -ne $rid -and -not ($rid -is [string])) {
        # Conflicting (typed conflict marker) or non-string request_id: never a valid id and never an alias of
        # another request; identity is its own canonical content (exact repeats still coalesce).
        return "$Target|invalid-id|$($Request.agent)|" + (Get-BridgeRequestContent $Request)
    }
    return "$Target|legacy|$($Request.agent)|$($Request.task_id)"
}

function Test-BridgeRequestEntryDiffers {
    # Same-key duplicates: ORDINAL content and digest comparison (culture-sensitive -cne ignored U+00AD, and U+200B in
    # pwsh 7, so a different request reusing an id read as an exact retry and its conflict was never shown).
    param($Left, $Right)
    return (-not [string]::Equals((Get-BridgeRequestContent $Left), (Get-BridgeRequestContent $Right), [System.StringComparison]::Ordinal) -or
        -not [string]::Equals((ConvertTo-BridgeContractJson (Get-BridgeContractField $Left 'request_digest')),
            (ConvertTo-BridgeContractJson (Get-BridgeContractField $Right 'request_digest')), [System.StringComparison]::Ordinal))
}

function Set-BridgeRequestViewEntry {
    param($Map, [string]$Key, $Request)
    if ((Get-BridgeContractField $Request 'request_id') -and $Map.ContainsKey($Key)) {
        if (Test-BridgeRequestEntryDiffers $Map[$Key] $Request) {
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

function Test-BridgeContractBlankLabel {
    # An exact empty-string label: Write-AgentEvent copies a requester label into in_reply_to_requester only
    # if ($value), so a blank request label is OMITTED from the reply and must stay a skipped (optional) check.
    param($Value)
    return ($Value -is [string] -and $Value.Length -eq 0)
}

function Test-BridgeContractCorrelationDiffers {
    # nonce/token/task_revision only (never ids, digests or identity labels): Write-BridgeTaskReply echoes these
    # with their original JSON type, so an equal value of the SAME exact built-in scalar type matches (bool, the
    # integer types, a finite double or decimal). Strings compare ordinally; both $null is not different; any
    # other pair (arrays, objects, a type change such as true vs "true" or true vs 1) is DIFFERENT.
    param($Left, $Right)
    if ($Left -is [string] -or $Right -is [string]) { return Test-BridgeContractValuesDiffer $Left $Right }
    if ($null -eq $Left -or $null -eq $Right) { return -not ($null -eq $Left -and $null -eq $Right) }
    $type = $Left.GetType()
    if ($type -ne $Right.GetType()) { return $true }
    if ($type -notin @([bool], [int], [long], [decimal], [double])) { return $true }
    # fable-5 ec85: PowerShell 5.1 ConvertFrom-Json reads integers beyond Int64/Decimal (31 digits, 2**96) as [double],
    # so distinct texts can be the same double. A double that is non-finite or has magnitude >= 2**53 (either side)
    # is not an exact value and never matches; identical modest finite doubles (1.5, -0.0 vs 0.0) still do.
    if ($type -eq [double] -and (
        [double]::IsNaN($Left) -or [double]::IsInfinity($Left) -or [double]::IsNaN($Right) -or [double]::IsInfinity($Right) -or
        [math]::Abs($Left) -ge 9007199254740992.0 -or [math]::Abs($Right) -ge 9007199254740992.0)) { return $true }
    return -not ($Left -eq $Right)
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
        -not [string]::Equals((ConvertTo-BridgeContractJson $direct.Value), (ConvertTo-BridgeContractJson $nested.Value), [System.StringComparison]::Ordinal)) {
        return (New-BridgeBindingConflict)
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
            if ($null -ne $expected -and -not (Test-BridgeContractBlankLabel $expected) -and
                (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $context $key) $expected)) { return $false }
        }
    } elseif ($null -ne (Get-BridgeContractField $Reply 'in_reply_to_request_id')) { return $false }
    $reference = Get-BridgeContractField $Reply 'request_ts_utc'
    if ($null -ne $reference -and (ConvertTo-BridgeContractTime $reference) -ne $sent) { return $false }
    $correlated = $null -ne $reference
    foreach ($key in @('nonce','token','task_revision')) {
        $expected = Get-BridgeBindingRawField $Request $key
        $actual = Get-BridgeBindingRawField $Reply $key
        # RCO2 a41e1a25: the request's own value disagrees between top level and payload: no single correlation.
        if (Test-BridgeBindingConflict $expected) { return $false }
        if ($null -ne $expected) {
            if (($null -eq $rid -or $null -ne $actual) -and (Test-BridgeContractCorrelationDiffers $actual $expected)) { return $false }
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
            if ($null -ne $value -and -not (Test-BridgeContractBlankLabel $value) -and
                (Test-BridgeContractValuesDiffer (Get-BridgeBindingRawField $Reply $key) $value)) { return $false }
        }
    }
    return $true
}

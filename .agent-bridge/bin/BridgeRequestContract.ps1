#requires -Version 5.1
# Correlation-only contract. Callers also require a substantive terminal event.
function Get-BridgeContractField {
    param($Event, [string]$Name)
    $direct = $Event.PSObject.Properties[$Name]
    $payload = $Event.PSObject.Properties['payload']
    $nested = if ($null -ne $payload -and $null -ne $payload.Value) { $payload.Value.PSObject.Properties[$Name] } else { $null }
    if ($null -ne $direct -and $null -ne $nested -and $direct.Value -cne $nested.Value) {
        return [pscustomobject]@{ invalid_binding = $true }
    }
    if ($null -ne $direct) { return $direct.Value }
    if ($null -ne $nested) { return $nested.Value }
    return $null
}

function ConvertTo-BridgeContractTime {
    param($Value)
    if ($Value -is [datetime] -or $Value -is [datetimeoffset]) { return $Value.ToUniversalTime() }
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
    param($Request, $Reply, [string]$Target, [bool]$RequesterClosure=$false, [bool]$AmbiguousLegacy=$false)
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
        $expected = Get-BridgeContractField $Request $key
        $actual = Get-BridgeContractField $Reply $key
        if ($null -ne $expected) {
            if (($null -eq $rid -or $null -ne $actual) -and $actual -cne $expected) { return $false }
            $correlated = $true
        }
    }
    if ($null -eq $rid -and $AmbiguousLegacy -and -not $correlated) { return $false }
    $identity = $null
    if ($RequesterClosure) { $identity = $Request }
    else {
        $expected = Get-BridgeContractField $Request 'expected_responders'
        if ($null -ne $expected) {
            $property = $expected.PSObject.Properties[$Target]
            if ($null -eq $property) { return $false }
            $identity = $property.Value
        }
    }
    if ($null -ne $identity) {
        foreach ($key in @('agent_uuid','session_id','run_id')) {
            $value = Get-BridgeContractField $identity $key
            if ($value -and (Get-BridgeContractField $Reply $key) -cne $value) { return $false }
        }
    }
    return $true
}

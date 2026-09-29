#requires -Version 5.1
<# Read-only, bounded discovery over the complete canonical request log. This
   never decides answer state: resolve an ID with Get-BridgeReplySnapshot.ps1.
   Pages are newest first and bound to a snapshot length; if the log changes,
   restart discovery instead of silently continuing on a different snapshot. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[a-z][a-z0-9_-]{1,32}$')] [string] $Agent,
    [ValidatePattern('^[A-Za-z0-9._:-]{1,128}$')] [string] $SessionId = '',
    [ValidatePattern('^[A-Za-z0-9._:-]{1,128}$')] [string] $RequestId = '',
    [string] $TaskId = '',
    [string] $TsUtc = '',
    [ValidateRange(1,50)] [int] $PageSize = 50,
    [ValidatePattern('^[0-9]+:[0-9]+:[A-F0-9]{16}$')] [string] $Cursor = '',
    [switch] $IncludeRequest,
    [switch] $NoCache
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')
. (Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1')
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')
. (Join-Path $PSScriptRoot 'BridgeReplyIndex.ps1')
if ($IncludeRequest -and -not ($RequestId -or ($TaskId -and $TsUtc))) {
    throw '-IncludeRequest requires -RequestId or both -TaskId and -TsUtc'
}
$root=if ($env:AGENT_BRIDGE_RUNTIME_ROOT) { $env:AGENT_BRIDGE_RUNTIME_ROOT } else { Split-Path $PSScriptRoot -Parent }
$started=[DateTimeOffset]::UtcNow.ToString('o')
# Read failures, partial rows, rotation and prefix changes throw in the index.
$snapshot=Read-BridgeReplyIndex -Path (Join-Path $root 'shared/events.jsonl') `
    -CachePath (Join-Path $root 'shared/cache/reply-index.json') -NoCache:$NoCache
# A cursor is valid only for the same snapshot AND the same exact query. A
# changed filter could otherwise silently skip older matching requests.
$cursorSeed=[ordered]@{
    snapshot=$snapshot.candidate_cursor
    agent=$Agent; session_id=$SessionId; request_id=$RequestId
    task_id=$TaskId; ts_utc=$TsUtc
    order='first_indexed_position_desc'; page_size=$PageSize
    include_request=[bool]$IncludeRequest
} | ConvertTo-Json -Depth 8 -Compress
$cursorHasher=[Security.Cryptography.SHA256]::Create()
try {
    $cursorHash=([BitConverter]::ToString($cursorHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($cursorSeed))) -replace '-','').Substring(0,16)
} finally { $cursorHasher.Dispose() }
$rows=@($snapshot.rows)
$byId=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
$order=[Collections.Generic.List[string]]::new()
$foreignIds=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
for ($position=0; $position -lt $rows.Count; $position++) {
    $event=$rows[$position]
    $id=Get-BridgeContractField $event 'request_id'
    if ($null -eq $id -or -not (Test-BridgeRequestLikeEvent $event)) { continue }
    $author=Get-BridgeContractField $event 'agent'
    if ($author -isnot [string]) {
        if ([string]$event.agent -ceq $Agent) { throw "Malformed request author binding at indexed position $position" }
        continue
    }
    if ($author -cne $Agent) {
        if ($id -is [string]) { [void]$foreignIds.Add($id) }
        continue
    }
    if ($id -isnot [string] -or $id -cnotmatch '^[A-Za-z0-9._:-]{1,128}$') {
        throw "Malformed request_id at indexed position $position; inventory is incomplete"
    }
    if ($SessionId -and (Get-BridgeContractField $event 'session_id') -cne $SessionId) { continue }
    if (-not $byId.ContainsKey($id)) {
        $byId[$id]=[pscustomobject]@{first_position=$position;occurrences=1;event=$event}
        $order.Add($id)
        continue
    }
    $first=$byId[$id]
    if ((Get-BridgeRequestContent $event) -cne (Get-BridgeRequestContent $first.event) -or
        (Get-BridgeContractField $event 'request_digest') -cne (Get-BridgeContractField $first.event 'request_digest')) {
        throw "Conflicting content for immutable request ID $id"
    }
    $first.occurrences++
}
$folded=@{}
foreach ($id in $order) {
    $key=$id.ToLowerInvariant()
    if ($folded.ContainsKey($key)) { $folded[$key]++ } else { $folded[$key]=1 }
}
$cursorPosition=$null
if ($Cursor) {
    $parts=$Cursor.Split(':')
    [long]$length=0; [int]$position=0
    if (-not [long]::TryParse($parts[0],[ref]$length) -or
        -not [int]::TryParse($parts[1],[ref]$position) -or
        $length -ne [long]$snapshot.snapshot_length -or $position -lt 0 -or
        $parts[2] -cne $cursorHash) {
        throw 'Inventory cursor does not match this complete snapshot'
    }
    $cursorPosition=$position
}
$matched=[Collections.Generic.List[object]]::new()
for ($i=$order.Count-1; $i -ge 0; $i--) {
    $id=$order[$i]; $entry=$byId[$id]; $event=$entry.event
    if ($RequestId -and $id -cne $RequestId) { continue }
    if ($TaskId -and [string]$event.task_id -cne $TaskId) { continue }
    if ($TsUtc -and [string]$event.ts_utc -cne $TsUtc) { continue }
    $matched.Add([pscustomobject]@{request_id=$id;entry=$entry})
}
if ($IncludeRequest -and $matched.Count -ne 1) {
    throw '-IncludeRequest requires exactly one matched request; narrow the exact filters'
}
$eligible=@($matched | Where-Object {
    $null -eq $cursorPosition -or $_.entry.first_position -lt $cursorPosition
})
$selected=@($eligible | Select-Object -First $PageSize)
$requests=@(foreach ($item in $selected) {
    $id=$item.request_id; $entry=$item.entry; $event=$entry.event
    $record=[ordered]@{
        request_id=$id; task_id=$event.task_id; ts_utc=$event.ts_utc
        targets=@(([string]$event.to -split ',') | ForEach-Object {$_.Trim()} | Where-Object {$_})
        first_indexed_position=$entry.first_position; occurrences=$entry.occurrences
        id_also_used_by_other_requester=$foreignIds.Contains($id)
        case_variant_id=($folded[$id.ToLowerInvariant()] -gt 1)
        answer_state='not_evaluated'
    }
    if ($IncludeRequest) { $record['request']=$event }
    [pscustomobject]$record
})
$truncated=$eligible.Count -gt $requests.Count
$nextCursor=$null
if ($truncated) { $nextCursor='{0}:{1}:{2}' -f $snapshot.snapshot_length,$requests[-1].first_indexed_position,$cursorHash }
$output=[pscustomobject]@{
    schema='wd.request-inventory.v2'; requester=$Agent
    session_id_filter=$(if ($SessionId) {$SessionId} else {$null})
    request_id_filter=$(if ($RequestId) {$RequestId} else {$null})
    task_id_filter=$(if ($TaskId) {$TaskId} else {$null})
    ts_utc_filter=$(if ($TsUtc) {$TsUtc} else {$null})
    read_started_utc=$started;read_completed_utc=[DateTimeOffset]::UtcNow.ToString('o')
    snapshot_cursor=$snapshot.candidate_cursor;snapshot_bytes=$snapshot.snapshot_length
    parsed_rows=$snapshot.parsed_rows;cache_status=$snapshot.cache_status;cache_path=$snapshot.cache_path
    request_count=$order.Count;matched_count=$matched.Count;returned_count=$requests.Count
    page_size=$PageSize;truncated=$truncated;next_cursor=$nextCursor;requests=$requests
    case_variant_request_ids=@($requests | Where-Object {$_.case_variant_id} | ForEach-Object {$_.request_id})
    answer_authority='Get-BridgeReplySnapshot.ps1 -RequestId <id> -Requester <requester>'
    note='Discovery only; answer_state never evaluated. HOLD/cancel/finding controls are separate. Re-read after append.'
    authority_effect='none'
}
$json=$output | ConvertTo-Json -Depth 64 -Compress
# A pathological single field must fail visibly rather than emit a truncated
# JSON stream that looks complete to a caller or floods a native model turn.
if ($json.Length -gt 50000) { throw 'Inventory page exceeds 50000 characters; use a narrower exact filter or smaller PageSize' }
$json

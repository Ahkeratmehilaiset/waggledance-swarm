#requires -Version 5.1
<# Read-only inventory of every request ID an exact requester has sent, over the
   WHOLE canonical log (no age or tail cutoff). Discovery only: it never decides
   whether a request is answered. Resolve each ID with
   Get-BridgeReplySnapshot.ps1 -RequestId <id> -Requester <agent>, which remains
   the only answer authority. Only the derived parse cache is written (-NoCache
   writes nothing). #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[a-z][a-z0-9_-]{1,32}$')] [string] $Agent,
    [ValidatePattern('^[A-Za-z0-9._:-]{1,128}$')] [string] $SessionId = '',
    [switch] $NoCache
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')
. (Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1')
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')
. (Join-Path $PSScriptRoot 'BridgeReplyIndex.ps1')
$root=if ($env:AGENT_BRIDGE_RUNTIME_ROOT) { $env:AGENT_BRIDGE_RUNTIME_ROOT } else { Split-Path $PSScriptRoot -Parent }
$started=[DateTimeOffset]::UtcNow.ToString('o')
# Read failures, partial rows, rotation and prefix changes throw inside the
# shared index: an incomplete snapshot is never reported as an empty inventory.
$snapshot=Read-BridgeReplyIndex -Path (Join-Path $root 'shared/events.jsonl') `
    -CachePath (Join-Path $root 'shared/cache/reply-index.json') -NoCache:$NoCache
$rows=@($snapshot.rows)
# The exact selection Get-BridgeReplySnapshot uses for a known ID, minus the ID.
$byId=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
$order=[Collections.Generic.List[string]]::new()
$foreignIds=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
for ($position=0; $position -lt $rows.Count; $position++) {
    $event=$rows[$position]
    $requestId=Get-BridgeContractField $event 'request_id'
    if ($null -eq $requestId -or -not (Test-BridgeRequestLikeEvent $event)) { continue }
    $author=Get-BridgeContractField $event 'agent'
    if ($author -isnot [string]) {
        # Another requester's malformed row must not blind this inventory; our own must.
        if ([string]$event.agent -ceq $Agent) { throw "Malformed request author binding at indexed position $position" }
        continue
    }
    if ($author -cne $Agent) {
        if ($requestId -is [string]) { [void]$foreignIds.Add($requestId) }
        continue
    }
    # A non-string or top-level/payload conflicting ID cannot be resolved exactly.
    if ($requestId -isnot [string] -or $requestId -cnotmatch '^[A-Za-z0-9._:-]{1,128}$') {
        throw "Malformed request_id at indexed position $position; inventory is incomplete"
    }
    if ($SessionId -and (Get-BridgeContractField $event 'session_id') -cne $SessionId) { continue }
    if (-not $byId.ContainsKey($requestId)) {
        $byId[$requestId]=[pscustomobject]@{first_position=$position;occurrences=1;event=$event}
        $order.Add($requestId)
        continue
    }
    $first=$byId[$requestId]
    if ((Get-BridgeRequestContent $event) -cne (Get-BridgeRequestContent $first.event) -or
        (Get-BridgeContractField $event 'request_digest') -cne (Get-BridgeContractField $first.event 'request_digest')) {
        throw "Conflicting content for immutable request ID $requestId"
    }
    $first.occurrences++
}
$folded=@{}
$requests=@(foreach ($requestId in $order) {
    $entry=$byId[$requestId]
    $key=$requestId.ToLowerInvariant()
    if ($folded.ContainsKey($key)) { $folded[$key]++ } else { $folded[$key]=1 }
    [pscustomobject]@{request_id=$requestId;task_id=$entry.event.task_id;ts_utc=$entry.event.ts_utc;
        targets=@(([string]$entry.event.to -split ',') | ForEach-Object {$_.Trim()} | Where-Object {$_});
        first_indexed_position=$entry.first_position;occurrences=$entry.occurrences;
        id_also_used_by_other_requester=$foreignIds.Contains($requestId);
        answer_state='not_evaluated';request=$entry.event}
})
$caseVariants=@($requests | Where-Object { $folded[$_.request_id.ToLowerInvariant()] -gt 1 } | ForEach-Object { $_.request_id })
[pscustomobject]@{schema='wd.request-inventory.v1';requester=$Agent;
    session_id_filter=$(if ($SessionId) {$SessionId} else {$null});
    read_started_utc=$started;read_completed_utc=[DateTimeOffset]::UtcNow.ToString('o');
    snapshot_cursor=$snapshot.candidate_cursor;snapshot_bytes=$snapshot.snapshot_length;
    parsed_rows=$snapshot.parsed_rows;cache_status=$snapshot.cache_status;cache_path=$snapshot.cache_path;
    request_count=$requests.Count;case_variant_request_ids=$caseVariants;requests=$requests;
    answer_authority='Get-BridgeReplySnapshot.ps1 -RequestId <id> -Requester <requester>';
    note='Discovery only; answer_state is never evaluated here. Controls (HOLD, cancel, finding) are not enumerated; read them separately. A later append requires a new inventory.';
    authority_effect='none'} | ConvertTo-Json -Depth 64

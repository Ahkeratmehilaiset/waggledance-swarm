#requires -Version 5.1
<# Canonical snapshot for an exact outgoing request; only a derived parse cache is written. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[A-Za-z0-9._:-]{1,128}$')] [string] $RequestId,
    [ValidatePattern('^[a-z][a-z0-9_-]{1,32}$')] [string] $Requester = 'codex-lead-1',
    [switch] $NoCache
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')
. (Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1')
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')
. (Join-Path $PSScriptRoot 'BridgeReplyIndex.ps1')
. (Join-Path $PSScriptRoot 'BridgeTaskResult.ps1')
$root=if ($env:AGENT_BRIDGE_RUNTIME_ROOT) { $env:AGENT_BRIDGE_RUNTIME_ROOT } else { Split-Path $PSScriptRoot -Parent }
$started=[DateTimeOffset]::UtcNow.ToString('o')
$snapshot=Read-BridgeReplyIndex -Path (Join-Path $root 'shared/events.jsonl') `
    -CachePath (Join-Path $root 'shared/cache/reply-index.json') -NoCache:$NoCache
$requests=@($snapshot.rows | Where-Object {
    (Get-BridgeContractField $_ 'request_id') -ceq $RequestId -and
    (Get-BridgeContractField $_ 'agent') -ceq $Requester -and (Test-BridgeRequestLikeEvent $_)
})
if (-not $requests.Count) { throw 'Exact request is absent; this is unknown, not pending' }
$request=$requests[0]
foreach ($duplicate in $requests) {
    if ((Get-BridgeRequestContent $duplicate) -cne (Get-BridgeRequestContent $request) -or
        (Get-BridgeContractField $duplicate 'request_digest') -cne (Get-BridgeContractField $request 'request_digest')) {
        throw 'Conflicting content for immutable request ID'
    }
}
$targets=@(([string]$request.to -split ',') | ForEach-Object {$_.Trim()} | Where-Object {$_})
if (-not $targets.Count) { throw 'Request has no explicit target' }
$results=@(foreach ($target in $targets) {
    $answers=@($snapshot.rows | Where-Object {
        (Get-BridgeContractField $_ 'in_reply_to_request_id') -ceq $RequestId -and
        (Test-BridgeAnswerEvent $_) -and (Test-BridgeReplyBinding $request $_ $target)
    })
    [pscustomobject]@{target=$target;state=$(if ($answers.Count) {'answered'} else {'pending_at_snapshot'});
        answers=$answers;answer_validation=@(foreach ($answer in $answers) {
            $validation=Get-BridgeTaskResultValidation -Request $request -Payload $answer.payload
            [pscustomobject]@{reply_ts_utc=$answer.ts_utc;correlation_valid=$true;
                schema_valid=$validation.schema_valid;content_valid=$validation.content_valid;reported=$null;
                errors=$validation.errors;content_scope=$validation.content_scope}
        })}
})
[pscustomobject]@{schema='wd.reply-snapshot.v1';request_id=$RequestId;request=$request;
    read_started_utc=$started;observed_at_utc=$started;read_completed_utc=[DateTimeOffset]::UtcNow.ToString('o');
    snapshot_cursor=$snapshot.candidate_cursor;snapshot_bytes=$snapshot.snapshot_length;results=$results;
    cache_status=$snapshot.cache_status;cache_path=$snapshot.cache_path;parsed_rows=$snapshot.parsed_rows;
    note='A later append requires a new snapshot. Read full answers before making a substantive conclusion.';
    authority_effect='none'} | ConvertTo-Json -Depth 64

#requires -Version 5.1
<# Records an agent-reported observation after reading/using an exact-bound answer.
   This is telemetry, not proof that the operator saw a final answer. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Agent,
    [Parameter(Mandatory)][string]$RequestEventJson,
    [Parameter(Mandatory)][string]$ReplyEventJson,
    [Parameter(Mandatory)][ValidateSet('lead_processed','user_reported')][string]$Stage,
    [string]$ReportReference=''
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')
. (Join-Path $PSScriptRoot 'BridgeEventClassifier.ps1')
. (Join-Path $PSScriptRoot 'BridgeTelemetry.ps1')
$parameters=@{}
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $parameters.DateKind='String' }
$request=$RequestEventJson | ConvertFrom-Json @parameters
$reply=$ReplyEventJson | ConvertFrom-Json @parameters
if ($Agent -cne 'codex-lead-1' -or $request.agent -cne $Agent -or
    -not (Get-BridgeContractField $request 'request_id') -or
    -not (Test-BridgeAnswerEvent $reply) -or
    -not (Test-BridgeReplyBinding $request $reply $reply.agent)) {
    throw 'Observation requires the requester and a substantive exactly bound answer'
}
if ($Stage -ceq 'user_reported' -and [string]::IsNullOrWhiteSpace($ReportReference)) {
    throw 'Reported observation needs an actual published report reference'
}
$root=if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {$env:AGENT_BRIDGE_RUNTIME_ROOT} else {Split-Path $PSScriptRoot -Parent}
Write-BridgeStageObservation -BridgeRoot $root -Stage $Stage -Request $request -Target $reply.agent `
    -ReplyTimestamp $reply.ts_utc -ReportReference $ReportReference

#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory)][string]$Agent,
      [Parameter(Mandatory)][string]$RequestEventJson,
      [string]$DeliveryId='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')
. (Join-Path $PSScriptRoot 'BridgeTelemetry.ps1')
$request = $RequestEventJson | ConvertFrom-Json
if (@(([string]$request.to).Split(',') | ForEach-Object {$_.Trim()}) -cnotcontains $Agent) {
    throw 'Request is not addressed to this turn'
}
if (-not (Get-BridgeContractField $request 'request_id')) { throw 'Request has no immutable request_id' }
$root = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) {$env:AGENT_BRIDGE_RUNTIME_ROOT} else {Split-Path $PSScriptRoot -Parent}
Write-BridgeStageObservation -BridgeRoot $root -Stage model_turn_started -Request $request -Target $Agent -DeliveryId $DeliveryId

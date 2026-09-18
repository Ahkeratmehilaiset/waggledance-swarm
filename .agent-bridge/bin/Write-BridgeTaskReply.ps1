#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Agent,
    [Parameter(Mandatory)][string]$RequestEventJson,
    [Parameter(Mandatory)][string]$ResultJson,
    [string]$Message='Task result supplied; see validation scope and evidence.',
    [string]$Status='answered',
    [switch]$ReceiptJson
)
$ErrorActionPreference='Stop'
if ($Status -cin @('received','seen','acknowledged')) {throw 'A task result must not use an ACK status'}
. (Join-Path $PSScriptRoot 'BridgeTaskResult.ps1')
$jsonArgs=@{ErrorAction='Stop'}
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {$jsonArgs.DateKind='String'}
$request=$RequestEventJson|ConvertFrom-Json @jsonArgs
$result=$ResultJson|ConvertFrom-Json @jsonArgs
if (-not (Test-BridgeResultObject $result)) {throw 'ResultJson must be an object; it is placed under payload.result'}
$payload=[ordered]@{result=$result}
foreach ($name in @('nonce','token','task_revision')) {
    $value=Get-BridgeContractField $request $name
    if ($null -ne $value) {$payload[$name]=$value}
}
$validation=Get-BridgeTaskResultValidation -Request $request -Payload ([pscustomobject]$payload)
if ($validation.errors.Count) {throw ('Task result rejected before write: '+($validation.errors -join ', '))}
$evidence=& (Join-Path $PSScriptRoot Get-BridgeExecutionEvidence.ps1)|ConvertFrom-Json @jsonArgs
if ($evidence.pin_status -ceq 'mismatch') {throw ('Execution evidence rejected before write: '+$evidence.pin_error)}
if ($null -ne $evidence.observed_agent -and $evidence.observed_agent -cne $Agent) {throw 'Observed launcher agent does not match reply author'}
$payload['execution_evidence']=$evidence
$payload['result_validation']=$validation
& (Join-Path $PSScriptRoot Write-AgentEvent.ps1) -Agent $Agent -Type message -Status $Status -TaskId $request.task_id `
    -To $request.agent -Message $Message -ReplyToEventJson $RequestEventJson -PayloadJson ($payload|ConvertTo-Json -Depth 64 -Compress) -ReceiptJson:$ReceiptJson

#requires -Version 5.1
<# Lead-only on-demand advisory helper. Default is read-only status, not a model call. #>
[CmdletBinding()]
param([string] $PromptPath = '', [string] $TaskId = '', [switch] $Status,
    [string] $LifecycleBase64 = '')
$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $PSScriptRoot 'deployment-manifest.json'
if (-not $env:WD_REBOOT_EXPECTED_MANIFEST_HASH -or
    (Get-FileHash -LiteralPath $manifestPath).Hash -cne $env:WD_REBOOT_EXPECTED_MANIFEST_HASH) {
    throw 'Grok requires the externally anchored installed bundle'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($LifecycleBase64) {
    if ($LifecycleBase64.Length -gt 32768 -or $PromptPath -or $TaskId -or $Status) { throw 'Invalid lifecycle invocation' }
    $event=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($LifecycleBase64)) | ConvertFrom-Json
    if ($event.stage -cnotin @('started','answered','failed','deferred')) { throw 'Invalid Grok lifecycle stage' }
    $state=$event.state
    if ([string]$state.task_id -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_./-]{0,159}$') { throw 'Invalid consultation task' }
    $contextPath=Join-Path $PSScriptRoot 'BridgeCodeContext.ps1'
    if ((Get-FileHash -LiteralPath $contextPath).Hash -cne $manifest.files.'BridgeCodeContext.ps1') { throw 'Bridge context hash mismatch' }
    . $contextPath
    # Verify the full transitive writer package before publishing metadata.
    $definition=Get-WdBridgeCodePackageDefinition -Path (Join-Path $PSScriptRoot 'bridge-code-files.json')
    if ($definition.Hash -cne $manifest.files.'bridge-code-files.json') { throw 'Bridge definition hash mismatch' }
    $context=Assert-WdBridgeCodePackageIntegrity -BundleRoot $PSScriptRoot -Deployment $manifest -Definition $definition.Definition
    $fleetPath=Join-Path $PSScriptRoot 'wd-fleet.json'
    if ((Get-FileHash -LiteralPath $fleetPath).Hash -cne $manifest.files.'wd-fleet.json') { throw 'Fleet runtime root hash mismatch' }
    $fleet=Get-Content -LiteralPath $fleetPath -Raw | ConvertFrom-Json
    $env:AGENT_BRIDGE_RUNTIME_ROOT=[string]$fleet.runtime_root
    $writer=Join-Path $PSScriptRoot 'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'
    $payload=[ordered]@{schema='wd.grok-consultation-event.v1';consultation_id=[string]$state.request_id;
        stage=[string]$event.stage;authority_effect='none';advisory_only=$true;
        budget_ref='C:\Python\grok-scout-reports\hourly-state.json'}
    foreach ($key in @('status','exit_code','report_path','report_sha256','duration_seconds','finished_at_utc','next_eligible_utc','error_type')) {
        if ($state.PSObject.Properties[$key]) { $payload[$key]=$state.$key }
    }
    $recipient=if ($event.stage -cin @('answered','failed')) { 'codex-lead-1' } else { 'operator' }
    $session='grok-consult-' + [string]$state.request_id
    $message='Grok advisory consultation ' + $event.stage + '; task=' + $state.task_id
    if ($payload.Contains('report_path')) { $message+='; report=' + $payload.report_path }
    & $writer -Agent grok-scout-1 -Type status -Status ('consultation_' + $event.stage) `
        -TaskId $state.task_id -Message $message -To $recipient -Role advisory-helper `
        -AgentUuid '0dbd9b59-0cbf-5dd4-81d9-af359439dc18' -SessionId $session -RunId $session `
        -Capabilities advisory_only -PayloadJson ($payload | ConvertTo-Json -Depth 8 -Compress) -ReceiptJson
    return
}
$wrapper = Join-Path $PSScriptRoot 'Invoke-WdBridgePython.ps1'
if ((Get-FileHash -LiteralPath $wrapper).Hash -cne $manifest.files.'Invoke-WdBridgePython.ps1') {
    throw 'Bridge Python wrapper hash mismatch'
}
$arguments = @('--status')
if ($PromptPath -and -not $Status) {
    if (-not $TaskId) { throw 'Lead must supply -TaskId for a consultation' }
    $arguments = @('--prompt-file', ([IO.Path]::GetFullPath($PromptPath)), '--task-id', $TaskId)
}
$previousGeneration = $env:WD_BRIDGE_GENERATION
try {
    $env:WD_BRIDGE_GENERATION = [string]$manifest.source_commit
    & $wrapper -Tool tools/wd_grok_helper.py -VerifyPackage @arguments
} finally {
    $env:WD_BRIDGE_GENERATION = $previousGeneration
}


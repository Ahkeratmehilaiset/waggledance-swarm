#requires -Version 5.1
<# Lead-only on-demand advisory helper. Default is read-only status, not a model call. #>
[CmdletBinding()]
param([string] $PromptPath = '', [string] $TaskId = '', [switch] $Status,
    [string] $LifecycleBase64 = '', [string] $ExceptionPath = '', [string] $ExceptionSha256 = '',
    [switch] $ReadOnly, [switch] $Inventory, [string] $RepositoryPath = '', [string] $Commit = '',
    [ValidateRange(2, 8)] [int] $MaxRounds = 6, [string] $AcknowledgeInheritedSurface = '')
$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $PSScriptRoot 'deployment-manifest.json'
if (-not $env:WD_REBOOT_EXPECTED_MANIFEST_HASH -or
    (Get-FileHash -LiteralPath $manifestPath).Hash -cne $env:WD_REBOOT_EXPECTED_MANIFEST_HASH) {
    throw 'Grok requires the externally anchored installed bundle'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$sessionOptions = $ReadOnly -or $Inventory -or $RepositoryPath -or $Commit -or
    $AcknowledgeInheritedSurface -or $PSBoundParameters.ContainsKey('MaxRounds')
if ($LifecycleBase64) {
    if ($LifecycleBase64.Length -gt 32768 -or $PromptPath -or $TaskId -or $Status -or $ExceptionPath -or $ExceptionSha256 -or $sessionOptions) { throw 'Invalid lifecycle invocation' }
    $event=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($LifecycleBase64)) | ConvertFrom-Json
    if ($event.stage -cnotin @('started','answered','failed','deferred')) { throw 'Invalid Grok lifecycle stage' }
    $state=$event.state
    # Full-match checks end with \z: .NET $ also matches before a final newline (RCO1 G1).
    if ([string]$state.task_id -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_./-]{0,159}\z') { throw 'Invalid consultation task' }
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
    $requestId=[string]$state.request_id
    $observationId=[string]$state.observation_id
    if ($event.stage -ceq 'deferred') {
        # A deferral reserves nothing: it names its own observation, never a consultation id.
        if ($requestId -or $observationId -cnotmatch '^[0-9a-f]{32}\z') { throw 'Invalid Grok deferral observation' }
        $consultationId=$null
        $session='grok-deferral-' + $observationId
    } else {
        if ($observationId -or $requestId -cnotmatch '^[0-9a-f]{32}\z') { throw 'Invalid Grok consultation id' }
        $consultationId=$requestId
        $session='grok-consult-' + $requestId
    }
    $payload=[ordered]@{schema='wd.grok-consultation-event.v1';consultation_id=$consultationId;
        stage=[string]$event.stage;authority_effect='none';advisory_only=$true;
        budget_ref='C:\Python\grok-scout-reports\hourly-state.json'}
    if ($event.stage -ceq 'deferred') { $payload['observation_id']=$observationId }
    foreach ($key in @('status','exit_code','report_path','report_sha256','duration_seconds','finished_at_utc','next_eligible_utc','local_availability','provider_quota','error_type','budget_exception')) {
        if ($state.PSObject.Properties[$key]) { $payload[$key]=$state.$key }
    }
    $recipient=if ($event.stage -cin @('answered','failed')) { 'codex-lead-1' } else { 'operator' }
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
$tool = 'tools/wd_grok_helper.py'
if ($sessionOptions -and -not ($ReadOnly -or $Inventory)) {
    throw 'Repository session options require -ReadOnly or -Inventory'
}
if ($sessionOptions -and $Status) { throw 'Use -Status separately from read-only session options' }
if ($PromptPath -and -not $Status) {
    if (-not $TaskId) { throw 'Lead must supply -TaskId for a consultation' }
    $arguments = @('--prompt-file', ([IO.Path]::GetFullPath($PromptPath)), '--task-id', $TaskId)
}
if ($Inventory) {
    if ($ReadOnly -or $PromptPath -or $TaskId -or $RepositoryPath -or $Commit -or
        $ExceptionPath -or $ExceptionSha256 -or $AcknowledgeInheritedSurface -or
        $PSBoundParameters.ContainsKey('MaxRounds')) { throw 'Use -Inventory alone; it never calls Grok' }
    $tool = 'tools/wd_grok_readonly_session.py'
    $arguments = @('--inventory')
} elseif ($ReadOnly) {
    if (-not $PromptPath -or -not $TaskId -or -not $RepositoryPath -or $Commit -cnotmatch '^[a-fA-F0-9]{40}\z') {
        throw '-ReadOnly requires -PromptPath, -TaskId, -RepositoryPath and a full 40-character -Commit'
    }
    $fleetPath = Join-Path $PSScriptRoot 'wd-fleet.json'
    if ((Get-FileHash -LiteralPath $fleetPath).Hash -cne $manifest.files.'wd-fleet.json') {
        throw 'Fleet trusted Git configuration hash mismatch'
    }
    $fleet = Get-Content -LiteralPath $fleetPath -Raw | ConvertFrom-Json
    $tool = 'tools/wd_grok_readonly_session.py'
    $arguments += @('--repo', ([IO.Path]::GetFullPath($RepositoryPath)), '--commit', $Commit,
        '--git-executable', [string]$fleet.git_executable, '--max-rounds', [string]$MaxRounds)
    if ($AcknowledgeInheritedSurface) {
        if ($AcknowledgeInheritedSurface -cnotmatch '^[a-fA-F0-9]{64}\z') { throw 'Inherited surface acknowledgement must be a SHA256 digest' }
        $arguments += @('--acknowledge-inherited-surface', $AcknowledgeInheritedSurface)
    }
}
if ($ExceptionPath -or $ExceptionSha256) {
    if (-not $PromptPath -or $Status -or -not $ExceptionPath -or $ExceptionSha256 -cnotmatch '^[a-fA-F0-9]{64}\z') {
        throw 'Task exception requires a consultation, path and SHA256'
    }
    $arguments += @('--exception-path', ([IO.Path]::GetFullPath($ExceptionPath)), '--exception-sha256', $ExceptionSha256)
}
$previousGeneration = $env:WD_BRIDGE_GENERATION
# A wrapper that ends without publishing an exit code is reported as a failure (1), never success.
$global:LASTEXITCODE = 1
try {
    $env:WD_BRIDGE_GENERATION = [string]$manifest.source_commit
    & $wrapper -Tool $tool -VerifyPackage @arguments
    $toolExitCode = $global:LASTEXITCODE
} finally {
    $env:WD_BRIDGE_GENERATION = $previousGeneration
}
# The Python exit code is the truthful result: 0 only for an answered consultation or a
# successful read-only status/inventory, 1 failed, 2 deferred or blocked (the JSON status
# tells them apart). When THIS script is the process's -File target, it exits with that
# code; otherwise PowerShell -File would report 0 after a failed consultation. Every other
# caller (a script using &, -Command, a dot-source) keeps the Invoke-WdBridgePython.ps1
# convention: no exit, which would abandon the caller's output capture; the code stays in
# $LASTEXITCODE.
$global:LASTEXITCODE = $toolExitCode
$processArguments = [Environment]::GetCommandLineArgs()
$isFileTarget = $false
for ($index = 1; $index -lt $processArguments.Count - 1; $index++) {
    if ($processArguments[$index] -match '^[-/](?i:f|fi|fil|file)$') {
        try {
            $isFileTarget = [IO.Path]::GetFullPath($processArguments[$index + 1]) -ieq [IO.Path]::GetFullPath($PSCommandPath)
        } catch {
            $isFileTarget = $false
        }
        break
    }
}
if ($isFileTarget) {
    exit $toolExitCode
}


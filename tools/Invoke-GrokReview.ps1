#requires -Version 5.1
[CmdletBinding(DefaultParameterSetName = 'Prompt')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Prompt')]
    [string] $Prompt,

    [Parameter(Mandatory, ParameterSetName = 'PromptFile')]
    [string] $PromptPath,

    [string] $TaskId = '',
    [string] $Agent = '',
    [string] $Model = '',
    [string] $ConfigPath = '',
    [string] $StatePath = '',
    [switch] $RequireFreshness,
    [string] $RemoteMainSha = '',
    [string] $LocalOriginMainSha = '',
    [string] $WorktreeHead = '',
    [string] $PrHeadSha = '',
    [string] $ReviewedHeadSha = '',
    [string] $TargetHeadSha = '',
    [string] $GitRoot = '',
    [switch] $DryRun,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptRoot 'invoke_grok_review.py'
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }

$argsList = @($pythonScript)
if ($PSCmdlet.ParameterSetName -eq 'PromptFile') {
    $argsList += @('--prompt-file', $PromptPath)
} else {
    $argsList += @('--prompt', $Prompt)
}
if ($TaskId) { $argsList += @('--task-id', $TaskId) }
if ($Agent) { $argsList += @('--agent', $Agent) }
if ($Model) { $argsList += @('--model', $Model) }
if ($ConfigPath) { $argsList += @('--config', $ConfigPath) }
if ($StatePath) { $argsList += @('--state', $StatePath) }
if ($RequireFreshness) { $argsList += '--require-freshness' }
if ($RemoteMainSha) { $argsList += @('--remote-main-sha', $RemoteMainSha) }
if ($LocalOriginMainSha) { $argsList += @('--local-origin-main-sha', $LocalOriginMainSha) }
if ($WorktreeHead) { $argsList += @('--worktree-head', $WorktreeHead) }
if ($PrHeadSha) { $argsList += @('--pr-head-sha', $PrHeadSha) }
if ($ReviewedHeadSha) { $argsList += @('--reviewed-head-sha', $ReviewedHeadSha) }
if ($TargetHeadSha) { $argsList += @('--target-head-sha', $TargetHeadSha) }
if ($GitRoot) { $argsList += @('--git-root', $GitRoot) }
if ($DryRun) { $argsList += '--dry-run' }
if ($Json) { $argsList += '--json' }

& $python @argsList
exit $LASTEXITCODE

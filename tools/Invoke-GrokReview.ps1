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
if ($DryRun) { $argsList += '--dry-run' }
if ($Json) { $argsList += '--json' }

& $python @argsList
exit $LASTEXITCODE

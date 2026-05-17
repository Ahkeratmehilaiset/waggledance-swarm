#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RemainingArgs = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script = Join-Path $PSScriptRoot 'idle_check.py'
$python = if ($env:PYTHON) { [string] $env:PYTHON } else { 'python' }

& $python $script @RemainingArgs
exit $LASTEXITCODE

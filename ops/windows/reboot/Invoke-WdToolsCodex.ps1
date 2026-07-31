#requires -Version 5.1
<#
.SYNOPSIS
    Invoke the real Codex CLI with the Tools consumer's sandbox-safe PATH.

.DESCRIPTION
    PowerShell installed through WindowsApps adds its package directory back
    to PATH whenever a new pwsh process starts. The bridge consumer uses such
    an intermediate process to bound each Codex tick. This shim runs inside
    that process, restores the already validated process-local PATH, forwards
    standard input and all Codex arguments, and returns the real exit code.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $false

$realCommand = [Environment]::GetEnvironmentVariable(
    'WD_TOOLS_CODEX_REAL_COMMAND',
    'Process'
)
$safePath = [Environment]::GetEnvironmentVariable(
    'WD_TOOLS_CODEX_SAFE_PATH',
    'Process'
)
if ([string]::IsNullOrWhiteSpace($realCommand)) {
    throw 'WD_TOOLS_CODEX_REAL_COMMAND is missing'
}
if (-not [IO.Path]::IsPathRooted($realCommand)) {
    throw "real Codex command is not absolute: $realCommand"
}
$realCommand = [IO.Path]::GetFullPath($realCommand)
if (-not [IO.File]::Exists($realCommand)) {
    throw "real Codex command is missing: $realCommand"
}
if ([string]::IsNullOrWhiteSpace($safePath)) {
    throw 'WD_TOOLS_CODEX_SAFE_PATH is missing'
}
foreach ($entry in @($safePath -split [IO.Path]::PathSeparator)) {
    if ([string]::IsNullOrWhiteSpace($entry)) {
        continue
    }
    if (-not [IO.Path]::IsPathRooted($entry)) {
        throw "sandbox-safe PATH contains a relative entry: $entry"
    }
    if ($entry -match '(?i)[\\/]WindowsApps(?:[\\/]|$)') {
        throw "sandbox-safe PATH still contains WindowsApps: $entry"
    }
}

$env:Path = $safePath
$input | & $realCommand @args
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) {
    $exitCode = if ($?) { 0 } else { 1 }
}
$global:LASTEXITCODE = [int]$exitCode

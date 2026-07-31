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
$writableDirectoriesJson = [Environment]::GetEnvironmentVariable(
    'WD_TOOLS_CODEX_ADDITIONAL_WRITABLE_DIRS',
    'Process'
)
$runtimeRoot = [Environment]::GetEnvironmentVariable(
    'WD_TOOLS_CODEX_RUNTIME_ROOT',
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
if ([string]::IsNullOrWhiteSpace($writableDirectoriesJson)) {
    throw 'WD_TOOLS_CODEX_ADDITIONAL_WRITABLE_DIRS is missing'
}
try {
    $parsedWritableDirectories = (
        $writableDirectoriesJson |
            ConvertFrom-Json -ErrorAction Stop
    )
    $writableDirectories = @(
        foreach ($directory in $parsedWritableDirectories) {
            [string]$directory
        }
    )
}
catch {
    throw "Tools Codex additional writable directory JSON is invalid: $($_.Exception.Message)"
}
if ($writableDirectories.Count -eq 0) {
    throw 'Tools Codex additional writable directory list is empty'
}
if ([string]::IsNullOrWhiteSpace($runtimeRoot)) {
    throw 'WD_TOOLS_CODEX_RUNTIME_ROOT is missing'
}
if (-not [IO.Path]::IsPathRooted($runtimeRoot)) {
    throw "Tools Codex runtime root is relative: $runtimeRoot"
}
$runtimeRoot = [IO.Path]::GetFullPath($runtimeRoot).TrimEnd([char]92, [char]47)

function Assert-WritableDirectoryPath {
    param(
        [Parameter(Mandatory)] [string] $Candidate,
        [Parameter(Mandatory)] [string] $Root
    )

    $candidateFull = [IO.Path]::GetFullPath($Candidate).TrimEnd(
        [char]92,
        [char]47
    )
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd([char]92, [char]47)
    if (
        -not $candidateFull.Equals(
            $rootFull,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        -not $candidateFull.StartsWith(
            $rootFull + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Tools Codex writable directory escaped runtime root: $candidateFull"
    }
    if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
        throw "Tools Codex runtime root is missing: $rootFull"
    }
    $rootItem = Get-Item -LiteralPath $rootFull -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Tools Codex runtime root is a reparse point: $rootFull"
    }
    $relative = $candidateFull.Substring($rootFull.Length).TrimStart(
        [char]92,
        [char]47
    )
    $current = $rootFull
    foreach ($segment in @($relative -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($segment)) {
            continue
        }
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current -PathType Container)) {
            throw "Tools Codex writable directory component is missing: $current"
        }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Tools Codex writable directory component is a reparse point: $current"
        }
    }
}

$seenWritableDirectories = @{}
foreach ($directory in $writableDirectories) {
    if ([string]::IsNullOrWhiteSpace($directory)) {
        throw 'Tools Codex additional writable directory is empty'
    }
    if (-not [IO.Path]::IsPathRooted($directory)) {
        throw "Tools Codex additional writable directory is relative: $directory"
    }
    $directory = [IO.Path]::GetFullPath($directory)
    $directoryKey = $directory.ToLowerInvariant()
    if ($seenWritableDirectories.ContainsKey($directoryKey)) {
        throw "Tools Codex additional writable directory is duplicated: $directory"
    }
    $seenWritableDirectories[$directoryKey] = $true
    Assert-WritableDirectoryPath -Candidate $directory -Root $runtimeRoot
}

$env:Path = $safePath
$forwardArguments = New-Object 'System.Collections.Generic.List[string]'
foreach ($argument in $args) {
    $forwardArguments.Add([string]$argument)
}
if (
    $forwardArguments.Count -eq 0 -or
    $forwardArguments[$forwardArguments.Count - 1] -cne '-'
) {
    throw 'Tools Codex shim requires the stdin prompt marker as the final argument'
}
if ($forwardArguments.Contains('--add-dir')) {
    throw 'Tools Codex shim received an unexpected pre-existing --add-dir'
}
$insertAt = $forwardArguments.Count - 1
foreach ($directory in $writableDirectories) {
    $forwardArguments.Insert($insertAt, '--add-dir')
    $insertAt += 1
    $forwardArguments.Insert($insertAt, [IO.Path]::GetFullPath($directory))
    $insertAt += 1
}
$nativeArguments = [string[]]$forwardArguments.ToArray()
$previousErrorActionPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 represents a native process's stderr as
    # NativeCommandError. Codex writes its startup banner there, so keep that
    # stream visible without turning a successful native launch into a
    # terminating PowerShell error. The real process exit code remains
    # authoritative.
    $ErrorActionPreference = 'Continue'
    $input | & $realCommand @nativeArguments
    $exitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($null -eq $exitCode) {
    $exitCode = if ($?) { 0 } else { 1 }
}
$global:LASTEXITCODE = [int]$exitCode

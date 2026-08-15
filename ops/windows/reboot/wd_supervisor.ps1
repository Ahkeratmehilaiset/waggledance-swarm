#requires -Version 5.1
<#
.SYNOPSIS
    Fail-closed watchdog for bridge watchers, the Tools consumer, and drivers.

.DESCRIPTION
    In report-only mode, verifies the exact configured processes and deliberate
    merge-driver HOLD. With -Apply it starts only absent helpers and contains
    driver tasks that are running or enabled without proven non-Apply legacy
    arguments. It never enables a scheduled task and emits no synthetic bridge
    events.
#>
[CmdletBinding()]
param(
    [switch] $Apply,
    [string] $ConfigPath = '',
    [string] $LogPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
}
$selfPid = $PID
$actions = New-Object 'System.Collections.Generic.List[string]'

function Get-RequiredText {
    param(
        [Parameter(Mandatory)] [psobject] $Object,
        [Parameter(Mandatory)] [string] $Name
    )

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "supervisor configuration is missing '$Name'"
    }
    return [string]$property.Value
}

function Read-Utf8SupervisorSnapshot {
    param([Parameter(Mandatory)] [string] $Path)

    $bytes = [IO.File]::ReadAllBytes($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = [BitConverter]::ToString(
            $sha.ComputeHash($bytes)
        ).Replace('-', '')
    }
    finally {
        $sha.Dispose()
    }
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
        $text = $text.Substring(1)
    }
    return [pscustomobject]@{
        Hash = $hash
        Text = $text
    }
}

function Initialize-SupervisorLogParent {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [switch] $Apply
    )

    # Apply preflights its durable log target before any process or task
    # reconciliation. Report-only mode must remain byte-inert.
    if (-not $Apply) { return }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop)
    }
}

function Write-SupervisorLogLine {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Line,
        [switch] $Apply
    )

    if (-not $Apply) { return }
    Add-Content -LiteralPath $Path -Value $Line -Encoding UTF8
}

function Assert-WdSupervisorPathWithoutReparse {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [ValidateSet('Directory', 'Leaf')] [string] $ExpectedType
    )

    $candidate = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $root = [IO.Path]::GetPathRoot($candidate)
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "supervisor trusted path root is missing: $root"
    }
    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "supervisor trusted path root is a reparse point: $root"
    }
    $relative = $candidate.Substring($root.Length).TrimStart('\')
    $current = $root
    foreach ($segment in @($relative -split '\\')) {
        if (-not $segment) { continue }
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current)) {
            throw "supervisor trusted path component is missing: $current"
        }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "supervisor trusted path component is a reparse point: $current"
        }
    }
    $pathType = if ($ExpectedType -ceq 'Directory') { 'Container' } else { 'Leaf' }
    if (-not (Test-Path -LiteralPath $candidate -PathType $pathType)) {
        throw "supervisor trusted path has the wrong type: $candidate"
    }
    return $candidate
}

function Resolve-OwnBundleGeneration {
    param([Parameter(Mandatory)] [string] $ScriptRoot)

    [void](Assert-WdSupervisorPathWithoutReparse `
        -Path $ScriptRoot -ExpectedType Directory)
    $deploymentPath = Join-Path $ScriptRoot 'deployment-manifest.json'
    if (Test-Path -LiteralPath $deploymentPath -PathType Leaf) {
        [void](Assert-WdSupervisorPathWithoutReparse `
            -Path $deploymentPath -ExpectedType Leaf)
        $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
        $deploymentSnapshot = Read-Utf8SupervisorSnapshot -Path $deploymentPath
        $actualManifestHash = ([string]$deploymentSnapshot.Hash).ToUpperInvariant()
        if (
            $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
            $actualManifestHash -cne $expectedManifestHash.ToUpperInvariant()
        ) {
            throw 'supervisor deployment manifest is not externally anchored'
        }
        $deployment = [string]$deploymentSnapshot.Text |
            ConvertFrom-Json -ErrorAction Stop
        $generation = ([string]$deployment.source_commit).ToLowerInvariant()
        $expectedHashProperty = $deployment.files.PSObject.Properties[
            'wd_supervisor.ps1'
        ]
        $scriptPath = Join-Path $ScriptRoot 'wd_supervisor.ps1'
        [void](Assert-WdSupervisorPathWithoutReparse `
            -Path $scriptPath -ExpectedType Leaf)
        if (
            [int]$deployment.schema_version -ne 1 -or
            $generation -cnotmatch '^[0-9a-f]{40}$' -or
            [IO.Path]::GetFileName(
                [IO.Path]::GetFullPath($ScriptRoot).TrimEnd('\')
            ) -cne $generation -or
            $null -eq $expectedHashProperty -or
            (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash -cne
                [string]$expectedHashProperty.Value
        ) {
            throw 'supervisor deployment generation is not exact'
        }
        return $generation
    }

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& git -C $ScriptRoot rev-parse HEAD 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $generation = (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim().ToLowerInvariant()
    if ($exitCode -ne 0 -or $generation -cnotmatch '^[0-9a-f]{40}$') {
        throw 'supervisor source generation is not a full Git commit'
    }
    return $generation
}

function Assert-SupervisorBundleFileIntegrity {
    param([Parameter(Mandatory)] [string] $RelativePath)

    [void](Assert-WdSupervisorPathWithoutReparse `
        -Path $PSScriptRoot -ExpectedType Directory)
    $deploymentPath = Join-Path $PSScriptRoot 'deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $deploymentPath -PathType Leaf)) {
        return
    }
    [void](Assert-WdSupervisorPathWithoutReparse `
        -Path $deploymentPath -ExpectedType Leaf)
    $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
    $deploymentSnapshot = Read-Utf8SupervisorSnapshot -Path $deploymentPath
    if (
        $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$deploymentSnapshot.Hash -cne
            $expectedManifestHash.ToUpperInvariant()
    ) {
        throw 'supervisor deployment manifest changed after external attestation'
    }
    $manifestRelative = $RelativePath.Replace('\', '/').TrimStart('/')
    if (
        [IO.Path]::IsPathRooted($RelativePath) -or
        $manifestRelative -match '(^|/)\.\.(/|$)'
    ) {
        throw "unsafe supervisor bundle dependency path: $RelativePath"
    }
    $deployment = [string]$deploymentSnapshot.Text |
        ConvertFrom-Json -ErrorAction Stop
    $hashProperty = $deployment.files.PSObject.Properties[$manifestRelative]
    $candidate = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $RelativePath))
    $bundlePrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    if (
        -not $candidate.StartsWith(
            $bundlePrefix,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        $null -eq $hashProperty
    ) {
        throw "supervisor bundle dependency hash mismatch: $manifestRelative"
    }
    [void](Assert-WdSupervisorPathWithoutReparse `
        -Path $candidate -ExpectedType Leaf)
    if (
        (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -cne
            [string]$hashProperty.Value
    ) {
        throw "supervisor bundle dependency hash mismatch: $manifestRelative"
    }
}

function Assert-MachineToolsConfigExact {
    param([Parameter(Mandatory)] [string] $MachineConfigPath)

    Assert-SupervisorBundleFileIntegrity `
        -RelativePath 'wd_supervisor_loop.json'
    [void](Assert-WdSupervisorPathWithoutReparse `
        -Path $MachineConfigPath -ExpectedType Leaf)
    $bundledConfigPath = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
    if (
        -not (Test-Path -LiteralPath $MachineConfigPath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $MachineConfigPath -Algorithm SHA256).Hash -cne
            (Get-FileHash -LiteralPath $bundledConfigPath -Algorithm SHA256).Hash
    ) {
        throw 'machine Tools config differs from the externally anchored bundle'
    }
}

function Resolve-PowerShellChildHost {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $currentWindowsPowerShell = ''

    try {
        $current = Get-Process -Id $PID -ErrorAction Stop
        $currentHostName = [IO.Path]::GetFileName([string]$current.Path)
        if ($current.Path -and $currentHostName -match '^(?i:pwsh)\.exe$') {
            $candidates.Add($current.Path)
        }
        elseif ($current.Path -and $currentHostName -match '^(?i:powershell)\.exe$') {
            $currentWindowsPowerShell = $current.Path
        }
    }
    catch {
        $actions.Add("WARN could not inspect current PowerShell host: $($_.Exception.Message)")
    }

    foreach ($commandName in @('pwsh.exe', 'pwsh')) {
        try {
            $command = Get-Command $commandName -ErrorAction Stop |
                Where-Object {
                    $_.CommandType -eq [Management.Automation.CommandTypes]::Application
                } |
                Select-Object -First 1
            if ($null -ne $command) {
                $path = if ($command.Path) { $command.Path } else { $command.Source }
                if ($path) { $candidates.Add([string]$path) }
            }
        }
        catch {
            # Other dynamic probes below may still resolve a supported host.
        }
    }

    if ($env:ProgramFiles) {
        $powerShellRoot = Join-Path $env:ProgramFiles 'PowerShell'
        if (Test-Path -LiteralPath $powerShellRoot -PathType Container) {
            $versionDirectories = @(
                Get-ChildItem -LiteralPath $powerShellRoot -Directory -ErrorAction SilentlyContinue |
                    Sort-Object {
                        $version = [Version]'0.0'
                        [void][Version]::TryParse($_.Name, [ref]$version)
                        $version
                    } -Descending
            )
            foreach ($directory in $versionDirectories) {
                $candidates.Add((Join-Path $directory.FullName 'pwsh.exe'))
            }
        }
    }

    if ($env:SystemRoot) {
        if ($currentWindowsPowerShell) {
            $candidates.Add($currentWindowsPowerShell)
        }
        $candidates.Add(
            (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
        )
    }
    foreach ($commandName in @('powershell.exe', 'powershell')) {
        try {
            $command = Get-Command $commandName -ErrorAction Stop |
                Where-Object {
                    $_.CommandType -eq [Management.Automation.CommandTypes]::Application
                } |
                Select-Object -First 1
            if ($null -ne $command) {
                $path = if ($command.Path) { $command.Path } else { $command.Source }
                if ($path) { $candidates.Add([string]$path) }
            }
        }
        catch {
            # The final candidate validation below emits one fail-closed error.
        }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try {
            $full = [IO.Path]::GetFullPath($candidate)
        }
        catch {
            continue
        }
        $key = $full.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            return $full
        }
    }

    throw 'could not dynamically resolve a supported pwsh/powershell child executable'
}

function Test-TextContains {
    param(
        [AllowEmptyString()] [string] $Text,
        [Parameter(Mandatory)] [string] $Expected
    )
    return $Text.IndexOf($Expected, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-NamedCommandLineArgument {
    param(
        [AllowEmptyString()] [string] $CommandLine,
        [ValidateSet('Auto', 'WindowsPowerShell', 'Pwsh')]
        [string] $HostKind = 'Auto',
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Value
    )

    $invocation = Get-WdPowerShellFileInvocation `
        -CommandLine $CommandLine `
        -HostKind $HostKind
    if ($null -eq $invocation) { return $false }
    if ($Name -ieq 'File') {
        return ([string]$invocation.script_path).Equals(
            $Value,
            [StringComparison]::OrdinalIgnoreCase
        )
    }
    $arguments = @($invocation.arguments)
    for ($index = [int]$invocation.file_index + 2;
         $index -lt $arguments.Count - 1;
         $index++) {
        if (Test-WdPowerShellSwitchToken -Token ([string]$arguments[$index]) -Name $Name) {
            return ([string]$arguments[$index + 1]).Equals(
                $Value,
                [StringComparison]::OrdinalIgnoreCase
            )
        }
    }
    return $false
}

function Test-WdWatcherScriptArgument {
    param(
        [AllowEmptyString()] [string] $CommandLine,
        [ValidateSet('Auto', 'WindowsPowerShell', 'Pwsh')]
        [string] $HostKind = 'Auto',
        [Parameter(Mandatory)]
        [ValidateSet('Agent', 'RuntimeRoot')]
        [string] $Name,
        [Parameter(Mandatory)] [string] $Value
    )

    $invocation = Get-WdPowerShellFileInvocation `
        -CommandLine $CommandLine `
        -HostKind $HostKind
    if ($null -eq $invocation) { return $false }
    $prefixPattern = if ($Name -ceq 'Agent') {
        'a(?:g(?:e(?:n(?:t)?)?)?)?'
    } else {
        'ru(?:n(?:t(?:i(?:m(?:e(?:r(?:o(?:o(?:t)?)?)?)?)?)?)?)?)?'
    }
    $arguments = @($invocation.arguments)
    for ($index = [int]$invocation.file_index + 2;
         $index -lt $arguments.Count;
         $index++) {
        $token = [string]$arguments[$index]
        $colonPattern = (
            '^(?i:[\-\u2013\u2014\u2015]{0}:(?<value>.*))$' -f
                $prefixPattern
        )
        if ($token -match $colonPattern) {
            return ([string]$Matches['value']).Equals(
                $Value,
                [StringComparison]::OrdinalIgnoreCase
            )
        }
        $separatePattern = (
            '^(?i:[\-\u2013\u2014\u2015]{0})$' -f $prefixPattern
        )
        if ($token -match $separatePattern) {
            if ($index + 1 -ge $arguments.Count) { return $false }
            return ([string]$arguments[$index + 1]).Equals(
                $Value,
                [StringComparison]::OrdinalIgnoreCase
            )
        }
    }
    return $false
}

function Get-WdWatcherReconcileMutexName {
    param([Parameter(Mandatory)] [string] $RuntimeRoot)

    $normalized = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd(
        [char[]]@('\', '/')
    ).ToUpperInvariant()
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = [BitConverter]::ToString(
            $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalized))
        ).Replace('-', '')
    }
    finally {
        $sha.Dispose()
    }
    return "Global\WaggleDanceWatcherReconcileV1-$digest"
}

function Invoke-WdWatcherReconcileLocked {
    param(
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [Parameter(Mandatory)] [scriptblock] $Action
    )

    $mutex = $null
    $acquired = $false
    try {
        $mutex = [Threading.Mutex]::new(
            $false,
            (Get-WdWatcherReconcileMutexName -RuntimeRoot $RuntimeRoot)
        )
        try {
            $acquired = $mutex.WaitOne(0)
        }
        catch [Threading.AbandonedMutexException] {
            $acquired = $true
        }
        if (-not $acquired) { return $false }
        $null = & $Action
        return $true
    }
    finally {
        try {
            if ($acquired -and $null -ne $mutex) {
                $mutex.ReleaseMutex()
            }
        }
        finally {
            if ($null -ne $mutex) {
                $mutex.Dispose()
            }
        }
    }
}

function Initialize-WdSupervisorCommandLineParser {
    if ('WaggleDance.WdSupervisorCommandLineV1.NativeMethods' -as [type]) {
        return
    }
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace WaggleDance.WdSupervisorCommandLineV1
{
    public static class NativeMethods
    {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CommandLineToArgvW(
            string commandLine,
            out int argumentCount);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr LocalFree(IntPtr memory);

        public static string[] Split(string commandLine)
        {
            int argumentCount;
            IntPtr memory = CommandLineToArgvW(commandLine, out argumentCount);
            if (memory == IntPtr.Zero)
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            try
            {
                string[] arguments = new string[argumentCount];
                for (int index = 0; index < argumentCount; index++)
                {
                    IntPtr item = Marshal.ReadIntPtr(memory, index * IntPtr.Size);
                    arguments[index] = Marshal.PtrToStringUni(item);
                }
                return arguments;
            }
            finally
            {
                LocalFree(memory);
            }
        }
    }
}
'@
}

function ConvertFrom-WdWindowsCommandLine {
    param([AllowEmptyString()] [string] $CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return @() }
    Initialize-WdSupervisorCommandLineParser
    return @(
        [WaggleDance.WdSupervisorCommandLineV1.NativeMethods]::Split(
            $CommandLine
        )
    )
}

function Test-WdPowerShellSwitchToken {
    param(
        [AllowEmptyString()] [string] $Token,
        [Parameter(Mandatory)] [string] $Name
    )

    return $Token -match (
        '^(?i:[\-/\u2013\u2014\u2015]{0})$' -f
            [Regex]::Escape($Name)
    )
}

function Test-WdPowerShellHostOptionToken {
    param(
        [AllowEmptyString()] [string] $Token,
        [Parameter(Mandatory)]
        [ValidateSet('WindowsPowerShell', 'Pwsh')]
        [string] $HostKind,
        [Parameter(Mandatory)]
        [ValidateSet(
            'NoProfile',
            'NoLogo',
            'NonInteractive',
            'NoExit',
            'NoProfileLoadTime',
            'Sta',
            'Mta',
            'Login',
            'Interactive',
            'ExecutionPolicy',
            'WindowStyle',
            'WorkingDirectory',
            'InputFormat',
            'OutputFormat',
            'Version',
            'PSConsoleFile',
            'ConfigurationFile',
            'CustomPipeName',
            'SettingsFile',
            'EncodedCommand'
        )]
        [string] $Name
    )

    if ($Token -cnotmatch '^(?i:(?<introducer>[\-/\u2013\u2014\u2015])(?<body>[a-z]+))$') {
        return $false
    }
    $introducer = [string]$Matches['introducer']
    $body = ([string]$Matches['body']).ToLowerInvariant()
    $canonical = $Name.ToLowerInvariant()
    switch ($Name) {
        'NoProfile' {
            return $body.Length -ge 3 -and $canonical.StartsWith($body)
        }
        'NoLogo' {
            return $body.Length -ge 3 -and $canonical.StartsWith($body)
        }
        'NonInteractive' {
            return $body.Length -ge 4 -and $canonical.StartsWith($body)
        }
        'NoExit' {
            return $body.Length -ge 3 -and $canonical.StartsWith($body)
        }
        'NoProfileLoadTime' {
            return $HostKind -ceq 'Pwsh' -and $body -ceq $canonical
        }
        'Sta' {
            if ($HostKind -ceq 'WindowsPowerShell') {
                return $body -in @('st', 'sta')
            }
            return $body -ceq 'sta'
        }
        'Mta' { return $body -ceq 'mta' }
        'Login' {
            return $HostKind -ceq 'Pwsh' -and
                $body.Length -ge 1 -and $canonical.StartsWith($body)
        }
        'Interactive' {
            return $HostKind -ceq 'Pwsh' -and
                $body.Length -ge 1 -and $canonical.StartsWith($body)
        }
        'ExecutionPolicy' {
            return $body -ceq 'ep' -or (
                $body.Length -ge 2 -and $canonical.StartsWith($body)
            )
        }
        'WindowStyle' {
            return $body.Length -ge 1 -and $canonical.StartsWith($body)
        }
        'WorkingDirectory' {
            return $body -ceq 'wd' -or (
                $body.Length -ge 2 -and $canonical.StartsWith($body)
            )
        }
        'InputFormat' {
            $minimum = if ($HostKind -ceq 'WindowsPowerShell') { 1 } else { 3 }
            return $body -ceq 'if' -or (
                $body.Length -ge $minimum -and $canonical.StartsWith($body)
            )
        }
        'OutputFormat' {
            return $body -ceq 'of' -or (
                $body.Length -ge 1 -and $canonical.StartsWith($body)
            )
        }
        'Version' {
            return $HostKind -ceq 'WindowsPowerShell' -and
                $introducer -cne '/' -and
                $body.Length -ge 1 -and $canonical.StartsWith($body)
        }
        'PSConsoleFile' {
            return $HostKind -ceq 'WindowsPowerShell' -and
                $introducer -cne '/' -and
                $body.Length -ge 1 -and $canonical.StartsWith($body)
        }
        'ConfigurationFile' {
            return $HostKind -ceq 'Pwsh' -and $body -ceq $canonical
        }
        'CustomPipeName' {
            return $HostKind -ceq 'Pwsh' -and
                $body.Length -ge 3 -and $canonical.StartsWith($body)
        }
        'SettingsFile' {
            return $HostKind -ceq 'Pwsh' -and
                $body.Length -ge 8 -and $canonical.StartsWith($body)
        }
        'EncodedCommand' {
            return $body -ceq 'ec' -or (
                $body.Length -ge 1 -and $canonical.StartsWith($body)
            )
        }
    }
    return $false
}

function Test-WdPowerShellFileSwitchToken {
    param([AllowEmptyString()] [string] $Token)

    return $Token -match '^(?i:[\-/\u2013\u2014\u2015]f(?:i(?:l(?:e)?)?)?)$'
}

function Get-WdPowerShellHostKind {
    param([AllowEmptyString()] [string] $ExecutableToken)

    try {
        $leaf = [IO.Path]::GetFileName($ExecutableToken)
    }
    catch {
        return ''
    }
    if ($leaf -match '^(?i:powershell)\.exe$') { return 'WindowsPowerShell' }
    if ($leaf -match '^(?i:pwsh)\.exe$') { return 'Pwsh' }
    return ''
}

function Test-WdEncodedCommandValue {
    param([AllowEmptyString()] [string] $Value)

    try {
        # Both supported hosts continue to a following -File even for Base64
        # payloads that are not strict UTF-16. Mirror their lexical admission:
        # syntactically valid Base64 is enough for conservative discovery.
        [void][Convert]::FromBase64String($Value)
        return $true
    }
    catch {
        return $false
    }
}

function Get-WdPowerShellFileInvocation {
    param(
        [AllowEmptyString()] [string] $CommandLine,
        [ValidateSet('Auto', 'WindowsPowerShell', 'Pwsh')]
        [string] $HostKind = 'Auto'
    )

    $arguments = @(ConvertFrom-WdWindowsCommandLine -CommandLine $CommandLine)
    if ($arguments.Count -lt 2) { return $null }
    # A ValidateSet attribute also validates later assignments to a parameter
    # variable. Keep an unvalidated local for conservative auto-detection so
    # an unrecognized executable returns no match instead of terminating the
    # entire supervisor report.
    $effectiveHostKind = $HostKind
    if ($effectiveHostKind -ceq 'Auto') {
        $effectiveHostKind = Get-WdPowerShellHostKind `
            -ExecutableToken ([string]$arguments[0])
    }
    if ($effectiveHostKind -notin @('WindowsPowerShell', 'Pwsh')) {
        return $null
    }

    $index = 1
    $encodedPrelude = $false
    while ($index -lt $arguments.Count) {
        $token = [string]$arguments[$index]
        if (
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'NoProfile') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'NoLogo') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'NonInteractive') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'NoExit') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'NoProfileLoadTime') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'Sta') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'Mta') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'Login') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'Interactive')
        ) {
            $index++
            continue
        }
        if (
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'ExecutionPolicy') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'WindowStyle') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'WorkingDirectory') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'InputFormat') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'OutputFormat') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'ConfigurationFile') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'CustomPipeName') -or
            (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'SettingsFile')
        ) {
            if ($index + 1 -ge $arguments.Count) { return $null }
            $index += 2
            continue
        }
        if (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'Version') {
            # Windows PowerShell accepts -Version only as the first host option.
            if ($index -ne 1 -or $index + 1 -ge $arguments.Count) {
                return $null
            }
            $index += 2
            continue
        }
        if (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'PSConsoleFile') {
            # Windows PowerShell accepts -PSConsoleFile only as the first host option.
            if ($index -ne 1 -or $index + 1 -ge $arguments.Count) {
                return $null
            }
            $index += 2
            continue
        }
        if (Test-WdPowerShellHostOptionToken -Token $token -HostKind $effectiveHostKind -Name 'EncodedCommand') {
            if (
                $index + 1 -ge $arguments.Count -or
                -not (Test-WdEncodedCommandValue -Value ([string]$arguments[$index + 1]))
            ) {
                return $null
            }
            $encodedPrelude = $true
            $index += 2
            continue
        }
        if (Test-WdPowerShellFileSwitchToken -Token $token) {
            if ($index + 1 -ge $arguments.Count) { return $null }
            return [pscustomobject]@{
                arguments = $arguments
                file_index = $index
                file_token = $token
                script_path = [string]$arguments[$index + 1]
            }
        }
        if (
            $token -notmatch '^[\-/\u2013\u2014\u2015]' -and
            [IO.Path]::GetExtension($token) -ieq '.ps1'
        ) {
            if ($encodedPrelude -and $effectiveHostKind -ceq 'WindowsPowerShell') {
                # WinPS continues to explicit -File after EncodedCommand but
                # consumes a following bare script token as payload instead.
                return $null
            }
            # Both supported hosts accept a script path without an explicit
            # -File selector. Model it for conflict detection, never health.
            return [pscustomobject]@{
                arguments = $arguments
                file_index = $index - 1
                file_token = ''
                script_path = $token
            }
        }
        # An unknown host option or a command payload before the script means
        # this is not a proven File-mode watcher invocation.
        return $null
    }
    return $null
}

function Test-NamedCommandLineLeafArgument {
    param(
        [AllowEmptyString()] [string] $CommandLine,
        [ValidateSet('Auto', 'WindowsPowerShell', 'Pwsh')]
        [string] $HostKind = 'Auto',
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Leaf
    )

    if ($Name -ine 'File') { return $false }
    $invocation = Get-WdPowerShellFileInvocation `
        -CommandLine $CommandLine `
        -HostKind $HostKind
    if ($null -eq $invocation) { return $false }
    return [IO.Path]::GetFileName([string]$invocation.script_path).Equals(
        $Leaf,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Test-WdCanonicalWatcherProcess {
    param(
        [Parameter(Mandatory)] $Process,
        [Parameter(Mandatory)] [string] $ExpectedExecutable,
        [Parameter(Mandatory)] [string] $ScriptPath,
        [Parameter(Mandatory)] [string] $Agent,
        [Parameter(Mandatory)] [string] $RuntimeRoot
    )

    try {
        $actualExecutable = [IO.Path]::GetFullPath(
            [string]$Process.ExecutablePath
        )
        $expectedExecutableFull = [IO.Path]::GetFullPath($ExpectedExecutable)
    }
    catch {
        return $false
    }
    if (
        -not $actualExecutable.Equals(
            $expectedExecutableFull,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        return $false
    }
    $hostKind = if ([string]$Process.Name -ieq 'powershell.exe') {
        'WindowsPowerShell'
    } elseif ([string]$Process.Name -ieq 'pwsh.exe') {
        'Pwsh'
    } else {
        return $false
    }
    $invocation = Get-WdPowerShellFileInvocation `
        -CommandLine ([string]$Process.CommandLine) `
        -HostKind $hostKind
    if ($null -eq $invocation) { return $false }
    $arguments = @($invocation.arguments)
    if ($arguments.Count -ne 10) { return $false }
    return (
        [string]$arguments[1] -ceq '-NoProfile' -and
        [string]$arguments[2] -ceq '-ExecutionPolicy' -and
        [string]$arguments[3] -ceq 'Bypass' -and
        [string]$arguments[4] -ceq '-File' -and
        ([string]$arguments[5]).Equals(
            $ScriptPath,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        [string]$arguments[6] -ceq '-Agent' -and
        [string]$arguments[7] -ceq $Agent -and
        [string]$arguments[8] -ceq '-RuntimeRoot' -and
        ([string]$arguments[9]).Equals(
            $RuntimeRoot,
            [StringComparison]::OrdinalIgnoreCase
        )
    )
}

function ConvertTo-SupervisorUtc {
    param([Parameter(Mandatory)] $Value)

    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTime]) {
        $dateTime = [DateTime]$Value
        if ($dateTime.Kind -eq [DateTimeKind]::Unspecified) {
            $dateTime = [DateTime]::SpecifyKind($dateTime, [DateTimeKind]::Utc)
        }
        return ([DateTimeOffset]$dateTime).ToUniversalTime()
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            [string]$Value,
            [Globalization.CultureInfo]::InvariantCulture,
            (
                [Globalization.DateTimeStyles]::AssumeUniversal -bor
                [Globalization.DateTimeStyles]::AdjustToUniversal
            ),
            [ref]$parsed
        )) {
        throw 'invalid Tools timestamp'
    }
    return $parsed.ToUniversalTime()
}

function Test-ToolsWrapperReadiness {
    param(
        [Parameter(Mandatory)] $Process,
        [Parameter(Mandatory)] $Tools,
        [Parameter(Mandatory)] [string] $Generation,
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $ReadinessPath
    )

    try {
        if (-not (Test-Path -LiteralPath $ReadinessPath -PathType Leaf)) {
            return $false
        }
        $record = Get-Content -LiteralPath $ReadinessPath -Raw -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
        $resumeCurrent = [string]$Tools.resume_policy -ceq 'current_worktree'
        $pinValid = if ($resumeCurrent) {
            [string]$record.branch -cmatch '^\S+$' -and
            [string]$record.head -cmatch '^[0-9a-f]{40}$' -and
            [string]$record.baseline_branch -ceq [string]$Tools.expected_branch -and
            [string]$record.baseline_head -ceq [string]$Tools.expected_head
        }
        else {
            [string]$record.branch -ceq [string]$Tools.expected_branch -and
            [string]$record.head -ceq [string]$Tools.expected_head
        }
        if (
            [string]$record.schema -cne 'wd.tools-consumer-ready.v1' -or
            [string]$record.generation -cne $Generation -or
            [int]$record.pid -ne [int]$Process.ProcessId -or
            -not $pinValid -or
            [string]$record.resume_policy -cne [string]$Tools.resume_policy -or
            [string]$record.model -cne [string]$Tools.model -or
            [string]$record.reasoning_effort -cne [string]$Tools.reasoning_effort -or
            -not [bool]$record.target_state_manifested -or
            [string]$record.target_state_id -cne 'wd-swarm-target-state-v1' -or
            -not ([string]$record.config_path).Equals(
                $ConfigPath,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            -not ([string]$record.worktree).Equals(
                [string]$Tools.worktree,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        $processCreated = ConvertTo-SupervisorUtc $Process.CreationDate
        $recordCreated = ConvertTo-SupervisorUtc $record.process_start_utc
        $readyAt = ConvertTo-SupervisorUtc $record.ready_at_utc
        return (
            [Math]::Abs(($recordCreated - $processCreated).TotalSeconds) -le 1 -and
            $readyAt -ge $recordCreated
        )
    }
    catch {
        return $false
    }
}

function Test-ToolsWrapperWithinStartupGrace {
    param(
        [Parameter(Mandatory)] $Process,
        [Parameter(Mandatory)] [int] $GraceSeconds
    )

    try {
        $age = [DateTimeOffset]::UtcNow - (
            ConvertTo-SupervisorUtc $Process.CreationDate
        )
        return $age.TotalSeconds -ge -5 -and $age.TotalSeconds -le $GraceSeconds
    }
    catch {
        return $false
    }
}

function Get-AgentCommandProcesses {
    param(
        [Parameter(Mandatory)] [object[]] $Processes,
        [Parameter(Mandatory)] [string] $ScriptName,
        [Parameter(Mandatory)] [string] $Agent
    )

    return @(
        $Processes |
            Where-Object {
                $hostKind = if ([string]$_.Name -ieq 'powershell.exe') {
                    'WindowsPowerShell'
                } elseif ([string]$_.Name -ieq 'pwsh.exe') {
                    'Pwsh'
                } else {
                    ''
                }
                $hostKind -and
                (Test-NamedCommandLineLeafArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -HostKind $hostKind `
                    -Name 'File' `
                    -Leaf $ScriptName) -and
                (Test-WdWatcherScriptArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -HostKind $hostKind `
                    -Name 'Agent' `
                    -Value $Agent)
            }
    )
}

function Invoke-WithChildIdentity {
    param(
        [Parameter(Mandatory)] [string] $Agent,
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [Parameter(Mandatory)] [scriptblock] $Action
    )

    $values = @{}
    $names = @(
        'AGENT_BRIDGE_RUNTIME_ROOT',
        'AGENT_BRIDGE_AGENT',
        'AGENT_BRIDGE_RUN_ID',
        'AGENT_BRIDGE_SESSION_ID',
        'AGENT_BRIDGE_AGENT_UUID',
        'AGENT_BRIDGE_ROLE',
        'AGENT_BRIDGE_CAPABILITIES',
        'AGENT_BRIDGE_OWNER_SESSION_ID',
        'AGENT_BRIDGE_OWNER_TOKEN',
        'AGENT_BRIDGE_OWNER_PID',
        'AGENT_BRIDGE_OWNER_PROCESS_START_UTC'
    )
    foreach ($name in $names) {
        $values[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }

    try {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_RUNTIME_ROOT',
            $RuntimeRoot,
            'Process'
        )
        [Environment]::SetEnvironmentVariable('AGENT_BRIDGE_AGENT', $Agent, 'Process')
        & $Action
    }
    finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
        }
    }
}

function ConvertTo-WindowsCommandLineArgument {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $Value
    )

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    # Win32_Process.Create accepts one native command-line string. Quote each
    # argv element with the CommandLineToArgvW backslash/quote rules so paths
    # containing whitespace or literal quotes cannot change argument shape.
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes += 1
            continue
        }
        if ($character -eq '"') {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * ($backslashes * 2)))
            }
            [void]$builder.Append('\"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Start-OutOfTaskJobPowerShell {
    param(
        [Parameter(Mandatory)] [string] $HostPath,
        [Parameter(Mandatory)] [string[]] $ArgumentList,
        [Parameter(Mandatory)] [string] $Name
    )

    # A direct Start-Process child remains in Task Scheduler's job and can be
    # terminated as soon as this short supervisor action exits. WMI creates the
    # long-lived headless consumer outside that job while retaining the same
    # interactive user token and session.
    $commandParts = New-Object 'System.Collections.Generic.List[string]'
    $commandParts.Add((ConvertTo-WindowsCommandLineArgument $HostPath))
    foreach ($argument in $ArgumentList) {
        $commandParts.Add((ConvertTo-WindowsCommandLineArgument $argument))
    }
    $commandLine = $commandParts -join ' '
    $environment = New-Object 'System.Collections.Generic.List[string]'
    $processEnvironment = [Environment]::GetEnvironmentVariables('Process')
    foreach ($environmentKey in @($processEnvironment.Keys | Sort-Object)) {
        $environmentName = [string]$environmentKey
        $environmentValue = [string]$processEnvironment[$environmentKey]
        if (
            $environmentName.IndexOf([char]0) -ge 0 -or
            $environmentName.IndexOf('=') -ge 0 -or
            $environmentValue.IndexOf([char]0) -ge 0
        ) {
            throw "cannot forward invalid process environment entry '$environmentName'"
        }
        $environment.Add("${environmentName}=${environmentValue}")
    }
    $startup = New-CimInstance `
        -ClassName Win32_ProcessStartup `
        -Property @{
            CreateFlags = [uint32](
                0x08000000 -bor  # CREATE_NO_WINDOW
                0x00000400       # CREATE_UNICODE_ENVIRONMENT
            )
            ShowWindow = [uint16]0  # SW_HIDE
            EnvironmentVariables = [string[]]$environment.ToArray()
        } `
        -ClientOnly `
        -ErrorAction Stop
    $created = Invoke-CimMethod `
        -ClassName Win32_Process `
        -MethodName Create `
        -Arguments @{
            CommandLine = $commandLine
            ProcessStartupInformation = $startup
        } `
        -ErrorAction Stop
    if (
        [int]$created.ReturnValue -ne 0 -or
        [int]$created.ProcessId -le 0
    ) {
        throw (
            "out-of-task-job launch failed for ${Name}: " +
            "return_value=$($created.ReturnValue) pid=$($created.ProcessId)"
        )
    }
    $actions.Add(
        "RELAUNCHED $Name pid=$([int]$created.ProcessId) out-of-task-job"
    )
}

function Write-ToolsReplacementConflict {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [int] $RootPid,
        [Parameter(Mandatory)] [string] $Reason,
        [int[]] $ProcessIds = @()
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop)
    }
    $temporary = "$Path.$PID.tmp"
    $record = [ordered]@{
        schema = 'wd.tools-replacement-conflict.v1'
        created_at_utc = [DateTime]::UtcNow.ToString('o')
        supervisor_pid = $PID
        stale_root_pid = $RootPid
        process_ids = @($ProcessIds | Sort-Object -Unique)
        reason = $Reason
    }
    try {
        $record |
            ConvertTo-Json -Depth 4 |
            Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Throw-ToolsReplacementConflict {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [int] $RootPid,
        [Parameter(Mandatory)] [string] $Reason,
        [int[]] $ProcessIds = @()
    )

    try {
        Write-ToolsReplacementConflict `
            -Path $Path `
            -RootPid $RootPid `
            -Reason $Reason `
            -ProcessIds $ProcessIds
    }
    catch {
        throw (
            "$Reason; additionally failed to persist Tools replacement conflict " +
            "marker '$Path': $($_.Exception.Message)"
        )
    }
    throw "$Reason; persistent replacement conflict recorded at '$Path'"
}

function Test-ToolsLineageEdge {
    param(
        [Parameter(Mandatory)] $ParentProcess,
        [Parameter(Mandatory)] $ChildProcess
    )

    if (
        [int]$ChildProcess.ProcessId -eq [int]$ParentProcess.ProcessId -or
        [int]$ChildProcess.ParentProcessId -ne [int]$ParentProcess.ProcessId
    ) {
        return $false
    }
    $parentCreated = (
        [DateTime]$ParentProcess.CreationDate
    ).ToUniversalTime()
    $childCreated = (
        [DateTime]$ChildProcess.CreationDate
    ).ToUniversalTime()
    return $childCreated.Ticks -ge $parentCreated.Ticks
}

function Stop-VerifiedProcessTree {
    param(
        [Parameter(Mandatory)] $RootProcess,
        [Parameter(Mandatory)] [object[]] $InitialProcesses,
        [Parameter(Mandatory)] [string] $ConflictPath
    )

    $rootPid = [int]$RootProcess.ProcessId
    $initialTree = @($RootProcess)
    $initialLineage = @{ $rootPid = $RootProcess }
    do {
        $added = $false
        foreach ($candidate in $InitialProcesses) {
            $candidatePid = [int]$candidate.ProcessId
            $parentPid = [int]$candidate.ParentProcessId
            if (
                -not $initialLineage.ContainsKey($candidatePid) -and
                $initialLineage.ContainsKey($parentPid) -and
                (Test-ToolsLineageEdge `
                    -ParentProcess $initialLineage[$parentPid] `
                    -ChildProcess $candidate)
            ) {
                $initialLineage[$candidatePid] = $candidate
                $initialTree += $candidate
                $added = $true
            }
        }
    } while ($added)
    $lineageByPid = @{}
    foreach ($process in $initialTree) {
        $lineageByPid[[int]$process.ProcessId] = $process
    }

    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    if (-not (Test-Path -LiteralPath $taskkill -PathType Leaf)) {
        Throw-ToolsReplacementConflict `
            -Path $ConflictPath `
            -RootPid $rootPid `
            -Reason "taskkill is missing: $taskkill" `
            -ProcessIds @($lineageByPid.Keys)
    }

    $deadline = (Get-Date).AddSeconds(5)
    $clearSamples = 0
    $rootKillSucceeded = $false
    $lastActiveIds = @()
    do {
        $currentProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $currentRoot = @(
            $currentProcesses |
                Where-Object { [int]$_.ProcessId -eq $rootPid }
        )
        if ($currentRoot.Count -gt 1) {
            Throw-ToolsReplacementConflict `
                -Path $ConflictPath `
                -RootPid $rootPid `
                -Reason "multiple current processes reported stale Tools PID $rootPid" `
                -ProcessIds @($rootPid)
        }
        if (-not $rootKillSucceeded -and $currentRoot.Count -eq 0) {
            Throw-ToolsReplacementConflict `
                -Path $ConflictPath `
                -RootPid $rootPid `
                -Reason (
                    'stale Tools wrapper exited before its process tree could be ' +
                    "re-attested and stopped: $rootPid"
                ) `
                -ProcessIds @($lineageByPid.Keys)
        }

        # Persist every observed lineage identity across samples. This still
        # finds a grandchild after its parent has exited and closes the common
        # wrapper-exit/orphan race before a replacement can launch.
        $currentTree = @()
        foreach ($candidate in $currentProcesses) {
            $candidatePid = [int]$candidate.ProcessId
            if (-not $lineageByPid.ContainsKey($candidatePid)) {
                continue
            }
            $expected = $lineageByPid[$candidatePid]
            $expectedCreated = (
                [DateTime]$expected.CreationDate
            ).ToUniversalTime()
            $candidateCreated = (
                [DateTime]$candidate.CreationDate
            ).ToUniversalTime()
            if ($candidateCreated.Ticks -ne $expectedCreated.Ticks) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "stale Tools lineage PID identity changed: $candidatePid"
                    ) `
                    -ProcessIds @($candidatePid)
            }
            if (
                $candidatePid -eq $rootPid -and
                (
                    [string]$candidate.Name -cne [string]$RootProcess.Name -or
                    [string]$candidate.CommandLine -cne
                        [string]$RootProcess.CommandLine
                )
            ) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "stale Tools PID identity changed before tree replacement: " +
                        $rootPid
                    ) `
                    -ProcessIds @($rootPid)
            }
            $currentTree += $candidate
        }
        do {
            $added = $false
            foreach ($candidate in $currentProcesses) {
                $candidatePid = [int]$candidate.ProcessId
                $parentPid = [int]$candidate.ParentProcessId
                if (
                    -not $lineageByPid.ContainsKey($candidatePid) -and
                    $lineageByPid.ContainsKey($parentPid) -and
                    (Test-ToolsLineageEdge `
                        -ParentProcess $lineageByPid[$parentPid] `
                        -ChildProcess $candidate)
                ) {
                    $lineageByPid[$candidatePid] = $candidate
                    $currentTree += $candidate
                    $added = $true
                }
            }
        } while ($added)

        $lastActiveIds = @(
            $currentTree | ForEach-Object { [int]$_.ProcessId }
        )
        if ($lastActiveIds.Count -eq 0) {
            if (-not $rootKillSucceeded) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason 'stale Tools tree cleared without a verified root-tree stop' `
                    -ProcessIds @($lineageByPid.Keys)
            }
            $clearSamples++
            if ($clearSamples -ge 2) {
                return $initialTree.Count
            }
            Start-Sleep -Milliseconds 150
            continue
        }
        $clearSamples = 0

        # Never use taskkill /T here: Windows builds that tree from untrusted
        # live ParentProcessId values and could include a PID-reuse bystander.
        # Stop only lineage members whose creation identity is re-attested
        # immediately before the individual kill.
        $killTargets = @(
            $currentTree |
                Sort-Object {
                    ([DateTime]$_.CreationDate).ToUniversalTime()
                } -Descending
        )
        foreach ($killTarget in $killTargets) {
            $killPid = [int]$killTarget.ProcessId
            $reattested = Get-CimInstance `
                -ClassName Win32_Process `
                -Filter "ProcessId=$killPid" `
                -ErrorAction SilentlyContinue
            if ($null -eq $reattested) {
                continue
            }
            $expectedCreated = (
                [DateTime]$killTarget.CreationDate
            ).ToUniversalTime()
            $reattestedCreated = (
                [DateTime]$reattested.CreationDate
            ).ToUniversalTime()
            if ($reattestedCreated.Ticks -ne $expectedCreated.Ticks) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "stale Tools kill target PID identity changed: $killPid"
                    ) `
                    -ProcessIds @($killPid)
            }
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $killOutput = @(& $taskkill /PID $killPid /F 2>&1)
                $killExit = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $previousPreference
            }
            if ($killExit -ne 0) {
                $afterFailure = Get-CimInstance `
                    -ClassName Win32_Process `
                    -Filter "ProcessId=$killPid" `
                    -ErrorAction SilentlyContinue
                if (
                    $killPid -ne $rootPid -and
                    $null -eq $afterFailure
                ) {
                    continue
                }
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "taskkill failed for stale Tools lineage PID $killPid " +
                        "with exit $killExit`: $($killOutput -join ' ')"
                    ) `
                    -ProcessIds $lastActiveIds
            }
            if ($killPid -eq $rootPid) {
                $rootKillSucceeded = $true
            }
        }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)

    Throw-ToolsReplacementConflict `
        -Path $ConflictPath `
        -RootPid $rootPid `
        -Reason (
            'stale Tools process tree did not stop before replacement deadline; ' +
            "survivors=$($lastActiveIds -join ',')"
        ) `
        -ProcessIds $lastActiveIds
}

function Test-LegacyDriverProvenNonApply {
    param(
        [Parameter(Mandatory)] $Task,
        [Parameter(Mandatory)] [string] $ExpectedScript
    )

    $taskActions = @($Task.Actions)
    if ($taskActions.Count -eq 0) { return $false }
    $escapedScript = [Regex]::Escape($ExpectedScript)
    $safeArguments = (
        '^(?i:-NoProfile\s+-ExecutionPolicy\s+Bypass\s+-File\s+' +
        '(?:"{0}"|{0})\s+-Loop\s+-PollSeconds\s+120)\s*$'
    ) -f $escapedScript
    $stablePowerShell = Join-Path $env:SystemRoot (
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    )
    foreach ($taskAction in $taskActions) {
        $execute = [string]$taskAction.Execute
        $arguments = [string]$taskAction.Arguments
        if ([string]::IsNullOrWhiteSpace($execute) -or
            [string]::IsNullOrWhiteSpace($arguments)) {
            return $false
        }
        if (
            -not $execute.Equals(
                'powershell.exe',
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            -not $execute.Equals(
                $stablePowerShell,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        if ($arguments -notmatch $safeArguments) {
            return $false
        }
    }
    return $true
}

function Get-OptionalScheduledTask {
    param([Parameter(Mandatory)] [string] $TaskName)

    try {
        return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    }
    catch {
        if (
            [string]$_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*' -or
            $_.CategoryInfo.Category -eq [Management.Automation.ErrorCategory]::ObjectNotFound
        ) {
            return $null
        }
        throw
    }
}

function Invoke-TaskContainment {
    param(
        [Parameter(Mandatory)] [string] $TaskName,
        [Parameter(Mandatory)] [string] $Reason
    )

    $task = Get-OptionalScheduledTask -TaskName $TaskName
    if ($null -eq $task) {
        $actions.Add("WARN scheduled task '$TaskName' not found for $Reason")
        return
    }

    $isRunning = [string]$task.State -eq 'Running'
    $isEnabled = [bool]$task.Settings.Enabled
    if (-not $isRunning -and -not $isEnabled) {
        $actions.Add("HOLD verified $TaskName disabled/not-running ($Reason)")
        return
    }

    if (-not $Apply) {
        if ($isRunning) {
            $actions.Add("WOULD-STOP $TaskName ($Reason)")
        }
        if ($isEnabled) {
            $actions.Add("WOULD-DISABLE $TaskName ($Reason)")
        }
        return
    }

    if ($isRunning) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $actions.Add("STOPPED $TaskName ($Reason)")
    }
    if ($isEnabled) {
        Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        $actions.Add("DISABLED $TaskName ($Reason)")
    }
}

$configFull = [IO.Path]::GetFullPath($ConfigPath)
[void](Assert-WdSupervisorPathWithoutReparse `
    -Path $PSScriptRoot -ExpectedType Directory)
if (-not (Test-Path -LiteralPath $configFull -PathType Leaf)) {
    throw "supervisor configuration not found: $configFull"
}
[void](Assert-WdSupervisorPathWithoutReparse `
    -Path $configFull -ExpectedType Leaf)
$supervisorConfigSnapshot = Read-Utf8SupervisorSnapshot -Path $configFull
$loadedSupervisorConfigHash = [string]$supervisorConfigSnapshot.Hash
$configuration = [string]$supervisorConfigSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
if ([string]$configuration.schema -cne 'wd.supervisor-loop.v2') {
    throw "unsupported supervisor configuration schema: $($configuration.schema)"
}
Assert-SupervisorBundleFileIntegrity -RelativePath 'wd_supervisor_loop.json'
$bundledSupervisorConfig = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
if (
    $loadedSupervisorConfigHash -cne
        (Get-FileHash -LiteralPath $bundledSupervisorConfig -Algorithm SHA256).Hash -or
    (Get-FileHash -LiteralPath $configFull -Algorithm SHA256).Hash -cne
        $loadedSupervisorConfigHash
) {
    throw 'supervisor config differs from the externally anchored bundle'
}

$runtimeRoot = [IO.Path]::GetFullPath((Get-RequiredText $configuration 'runtime_root'))
[void](Assert-WdSupervisorPathWithoutReparse `
    -Path $runtimeRoot -ExpectedType Directory)
if (-not $LogPath) {
    $LogPath = Get-RequiredText $configuration 'log_path'
}
$logFull = [IO.Path]::GetFullPath($LogPath)
Initialize-SupervisorLogParent -Path $logFull -Apply:$Apply

$powerShellHost = Resolve-PowerShellChildHost
# Process identity matching is an admission boundary.  If the native argv
# parser is unavailable, abort before any reconciliation can launch a duplicate.
Initialize-WdSupervisorCommandLineParser
$processes = @(
    Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object {
            $_.ProcessId -ne $selfPid -and
            -not [string]::IsNullOrWhiteSpace([string]$_.CommandLine)
        }
)

if ($null -eq $configuration.tools_consumer) {
    throw 'supervisor configuration has no tools_consumer object'
}
$tools = $configuration.tools_consumer
$toolsEnabled = [bool]$tools.enabled
$toolsAgent = ''
$toolsGeneration = ''
$toolsExpectedHead = ''
$toolsLauncher = ''
$configuredToolsLauncher = ''
$toolsConfig = ''
$toolsConflictPath = ''
$toolsPowerShellHost = ''
if ($toolsEnabled) {
    $toolsAgent = Get-RequiredText $tools 'agent'
    $toolsExpectedHead = (Get-RequiredText $tools 'expected_head').ToLowerInvariant()
    if ($toolsExpectedHead -cnotmatch '^[0-9a-f]{40}$') {
        throw 'tools expected_head must be a full lowercase Git commit'
    }
    $toolsResumePolicy = Get-RequiredText $tools 'resume_policy'
    if ($toolsResumePolicy -cnotin @('pinned', 'current_worktree')) {
        throw "unsupported tools resume_policy: $toolsResumePolicy"
    }
    $toolsModel = Get-RequiredText $tools 'model'
    $toolsReasoningEffort = Get-RequiredText $tools 'reasoning_effort'
    if (
        $toolsModel -cne 'gpt-5.6-terra' -or
        $toolsReasoningEffort -cnotin @('low', 'medium', 'high', 'xhigh', 'max')
    ) {
        throw 'Tools model or reasoning effort is unsupported'
    }
    $toolsGeneration = Resolve-OwnBundleGeneration -ScriptRoot $PSScriptRoot
    $configuredToolsLauncher = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'launcher_script')
    )
    $toolsLauncher = [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot 'start-wd-tools-consumer.ps1')
    )
    Assert-SupervisorBundleFileIntegrity `
        -RelativePath 'start-wd-tools-consumer.ps1'
    $toolsConfig = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'config_path')
    )
    $toolsConflictPath = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'replacement_conflict_path')
    )
    $readinessPath = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'readiness_path')
    )
    if (
        -not (Split-Path -Parent $toolsConflictPath).Equals(
            (Split-Path -Parent $readinessPath),
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [IO.Path]::GetExtension($toolsConflictPath) -cne '.json'
    ) {
        throw 'Tools replacement conflict marker is outside the readiness directory'
    }
    if (-not (Test-Path -LiteralPath $toolsLauncher -PathType Leaf)) {
        throw "required tools consumer launcher is missing: $toolsLauncher"
    }
    [void](Assert-WdSupervisorPathWithoutReparse `
        -Path $toolsLauncher -ExpectedType Leaf)
    if (-not (Test-Path -LiteralPath $toolsConfig -PathType Leaf)) {
        throw "required tools consumer config is missing: $toolsConfig"
    }
    Assert-MachineToolsConfigExact -MachineConfigPath $toolsConfig
    Assert-SupervisorBundleFileIntegrity `
        -RelativePath 'start-wd-tools-consumer.ps1'
    $toolsValidationOutput = @(
        & $toolsLauncher `
            -ConfigPath $toolsConfig `
            -Generation $toolsGeneration `
            -ValidateOnly
    )
    $toolsValidation = @(
        $toolsValidationOutput |
            Where-Object {
                $_ -is [psobject] -and
                [string]$_.schema -ceq 'wd.tools-consumer-validation.v1'
            }
    )
    if ($toolsValidation.Count -ne 1) {
        throw 'Tools consumer preflight returned no exact validation record'
    }
    $toolsValidation = $toolsValidation[0]
    $toolsValidationPinValid = if ($toolsResumePolicy -ceq 'current_worktree') {
        [string]$toolsValidation.branch -cmatch '^\S+$' -and
        [string]$toolsValidation.head -cmatch '^[0-9a-f]{40}$' -and
        [string]$toolsValidation.baseline_branch -ceq [string]$tools.expected_branch -and
        [string]$toolsValidation.baseline_head -ceq [string]$tools.expected_head
    }
    else {
        [string]$toolsValidation.branch -ceq [string]$tools.expected_branch -and
        [string]$toolsValidation.head -ceq $toolsExpectedHead
    }
    if (
        -not [bool]$toolsValidation.validated -or
        [string]$toolsValidation.generation -cne $toolsGeneration -or
        -not $toolsValidationPinValid -or
        [string]$toolsValidation.resume_policy -cne $toolsResumePolicy -or
        [string]$toolsValidation.model -cne $toolsModel -or
        [string]$toolsValidation.reasoning_effort -cne $toolsReasoningEffort -or
        [string]$toolsValidation.target_state_id -cne 'wd-swarm-target-state-v1' -or
        -not ([string]$toolsValidation.readiness_path).Equals(
            $readinessPath,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not ([string]$toolsValidation.worktree).Equals(
            [string]$tools.worktree,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not [bool]$toolsValidation.require_dedicated_worktree
    ) {
        throw 'Tools consumer preflight does not match supervisor generation'
    }
    $toolsPowerShellHost = [IO.Path]::GetFullPath(
        [string]$toolsValidation.consumer_host
    )
    $stableWindowsPowerShell = [IO.Path]::GetFullPath(
        (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    )
    if (
        -not $toolsPowerShellHost.Equals(
            $stableWindowsPowerShell,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Test-Path -LiteralPath $toolsPowerShellHost -PathType Leaf)
    ) {
        throw 'Tools consumer preflight did not resolve stable Windows PowerShell'
    }
    if (Test-Path -LiteralPath $toolsConflictPath -PathType Leaf) {
        throw (
            'Tools replacement is blocked by persistent orphan-conflict marker: ' +
            $toolsConflictPath
        )
    }
}

if ($null -eq $configuration.watchers) {
    throw 'supervisor configuration has no watchers object'
}
$watcherRelative = Get-RequiredText $configuration.watchers 'script_relative'
$watcherReaderRelative = `
    'tools-bootstrap\.agent-bridge\bin\BridgeIncrementalReader.ps1'
$watcherLogReaderRelative = `
    'tools-bootstrap\.agent-bridge\bin\BridgeLogReader.ps1'
if ([IO.Path]::IsPathRooted($watcherRelative)) {
    throw 'supervisor watcher script must be relative to the reboot bundle'
}
$watcherScript = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot $watcherRelative)
)
$bundlePrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
if (-not $watcherScript.StartsWith(
        $bundlePrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "supervisor watcher script escapes the reboot bundle: $watcherRelative"
}
$watcherAgents = @(
    @($configuration.watchers.agents) |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($watcherAgents.Count -eq 0) {
    throw 'supervisor watcher agent list must not be empty'
}
if (-not (Test-Path -LiteralPath $watcherScript -PathType Leaf)) {
    throw "required watcher source is missing: $watcherScript"
}
else {
    Assert-SupervisorBundleFileIntegrity -RelativePath $watcherRelative
    $watcherDependencies = @(
        @($configuration.watchers.dependency_relatives) |
            ForEach-Object {
                $dependency = [string]$_
                if ([string]::IsNullOrWhiteSpace($dependency)) {
                    throw 'supervisor watcher dependency must not be empty'
                }
                $dependency
            }
    )
    $expectedWatcherDependencies = @(
        $watcherReaderRelative,
        $watcherLogReaderRelative
    )
    if (
        $watcherDependencies.Count -ne $expectedWatcherDependencies.Count -or
        @(Compare-Object `
            -ReferenceObject $expectedWatcherDependencies `
            -DifferenceObject $watcherDependencies `
            -CaseSensitive).Count -ne 0
    ) {
        throw 'supervisor watcher dependency set is not exact'
    }
    foreach ($watcherDependency in $watcherDependencies) {
        Assert-SupervisorBundleFileIntegrity -RelativePath $watcherDependency
    }
    $watcherReconciled = Invoke-WdWatcherReconcileLocked `
        -RuntimeRoot $runtimeRoot `
        -Action {
            $watcherProcesses = @(
                Get-CimInstance Win32_Process -ErrorAction Stop |
                    Where-Object {
                        $_.ProcessId -ne $selfPid -and
                        -not [string]::IsNullOrWhiteSpace(
                            [string]$_.CommandLine
                        )
                    }
            )
            foreach ($agent in $watcherAgents) {
                if ($agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
                    throw "invalid configured watcher identity: $agent"
                }
                $allAgentWatchers = @(
                    Get-AgentCommandProcesses `
                        $watcherProcesses `
                        'Watch-Bridge.ps1' `
                        $agent
                )
                $exactAgentWatchers = @(
                    $allAgentWatchers |
                        Where-Object {
                            Test-WdCanonicalWatcherProcess `
                                -Process $_ `
                                -ExpectedExecutable $powerShellHost `
                                -ScriptPath $watcherScript `
                                -Agent $agent `
                                -RuntimeRoot $runtimeRoot
                        }
                )

                if (
                    $exactAgentWatchers.Count -eq 1 -and
                    $allAgentWatchers.Count -eq 1
                ) {
                    continue
                }
                if ($allAgentWatchers.Count -gt 0) {
                    $ids = @(
                        $allAgentWatchers |
                            ForEach-Object { [string]$_.ProcessId }
                    ) -join ','
                    $actions.Add(
                        "CONFLICT watcher:$agent count=$($allAgentWatchers.Count) pids=$ids; no duplicate launched"
                    )
                    continue
                }

                if (-not $Apply) {
                    $actions.Add("WOULD-RELAUNCH watcher:$agent")
                    continue
                }

                $watcherArguments = @(
                    '-NoProfile',
                    '-ExecutionPolicy', 'Bypass',
                    '-File', $watcherScript,
                    '-Agent', $agent,
                    '-RuntimeRoot', $runtimeRoot
                )
                Assert-SupervisorBundleFileIntegrity `
                    -RelativePath $watcherRelative
                foreach ($watcherDependency in $watcherDependencies) {
                    Assert-SupervisorBundleFileIntegrity `
                        -RelativePath $watcherDependency
                }
                Invoke-WithChildIdentity $agent $runtimeRoot {
                    Start-OutOfTaskJobPowerShell `
                        $powerShellHost `
                        $watcherArguments `
                        "watcher:$agent"
                }
            }
        }
    if (-not $watcherReconciled) {
        $actions.Add(
            'SKIPPED watcher reconciliation: another supervisor owns the runtime mutex'
        )
    }
}

if ($toolsEnabled) {
    $wrapperProcesses = @(
        $processes |
            Where-Object {
                if ([string]$_.Name -notmatch '^(?i:powershell|pwsh)\.exe$') {
                    return $false
                }
                $processHostKind = if ([string]$_.Name -ieq 'powershell.exe') {
                    'WindowsPowerShell'
                } else {
                    'Pwsh'
                }
                return (
                    (Test-NamedCommandLineArgument `
                        -CommandLine ([string]$_.CommandLine) `
                        -HostKind $processHostKind `
                        -Name 'File' `
                        -Value $toolsLauncher) -or
                    (Test-NamedCommandLineArgument `
                        -CommandLine ([string]$_.CommandLine) `
                        -HostKind $processHostKind `
                        -Name 'File' `
                        -Value $configuredToolsLauncher)
                )
            }
    )
    $configuredWrapperProcesses = @(
        $wrapperProcesses |
            Where-Object {
                $processHostKind = if ([string]$_.Name -ieq 'powershell.exe') {
                    'WindowsPowerShell'
                } else {
                    'Pwsh'
                }
                Test-NamedCommandLineArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -HostKind $processHostKind `
                    -Name 'ConfigPath' `
                    -Value $toolsConfig
            }
    )
    $exactWrapperProcesses = @(
        $configuredWrapperProcesses |
            Where-Object {
                $processHostKind = if ([string]$_.Name -ieq 'powershell.exe') {
                    'WindowsPowerShell'
                } else {
                    'Pwsh'
                }
                (Test-NamedCommandLineArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -HostKind $processHostKind `
                    -Name 'File' `
                    -Value $toolsLauncher) -and
                (Test-NamedCommandLineArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -HostKind $processHostKind `
                    -Name 'Generation' `
                    -Value $toolsGeneration)
            }
    )
    $readyWrapperProcesses = @(
        $exactWrapperProcesses |
            Where-Object {
                Test-ToolsWrapperReadiness `
                    -Process $_ `
                    -Tools $tools `
                    -Generation $toolsGeneration `
                    -ConfigPath $toolsConfig `
                    -ReadinessPath $readinessPath
            }
    )
    $readyWrapperIds = @(
        $readyWrapperProcesses |
            ForEach-Object { [int]$_.ProcessId }
    )
    $startingWrapperProcesses = @(
        $exactWrapperProcesses |
            Where-Object { [int]$_.ProcessId -notin $readyWrapperIds }
    )
    $toolsStartupGraceSeconds = [int]$tools.codex_timeout_seconds + 60
    $graceWrapperProcesses = @(
        $startingWrapperProcesses |
            Where-Object {
                Test-ToolsWrapperWithinStartupGrace `
                    -Process $_ `
                    -GraceSeconds $toolsStartupGraceSeconds
            }
    )
    $graceWrapperIds = @(
        $graceWrapperProcesses |
            ForEach-Object { [int]$_.ProcessId }
    )
    $expiredWrapperProcesses = @(
        $startingWrapperProcesses |
            Where-Object { [int]$_.ProcessId -notin $graceWrapperIds }
    )
    $legacyConsumers = @(
        Get-AgentCommandProcesses `
            $processes `
            'Start-AgentBridgeConsumerLoop.ps1' `
            $toolsAgent
    )
    $toolsArguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $toolsLauncher,
        '-ConfigPath', $toolsConfig,
        '-Generation', $toolsGeneration
    )

    if ($readyWrapperProcesses.Count -eq 1 -and
        $exactWrapperProcesses.Count -eq 1 -and
        $wrapperProcesses.Count -eq 1 -and
        $legacyConsumers.Count -eq 0) {
        # Healthy: readiness is bound to this PID, start time, and generation.
    }
    elseif ($graceWrapperProcesses.Count -eq 1 -and
        $exactWrapperProcesses.Count -eq 1 -and
        $wrapperProcesses.Count -eq 1 -and
        $legacyConsumers.Count -eq 0) {
        $actions.Add(
            "STARTING consumer-loop:codex-tools-1 pid=$($graceWrapperProcesses[0].ProcessId)"
        )
    }
    elseif ($wrapperProcesses.Count -eq 1 -and
        $configuredWrapperProcesses.Count -eq 1 -and
        (
            $exactWrapperProcesses.Count -eq 0 -or
            $expiredWrapperProcesses.Count -eq 1
        ) -and
        $legacyConsumers.Count -eq 0) {
        $staleProcess = $wrapperProcesses[0]
        $replacementReason = if ($expiredWrapperProcesses.Count -eq 1) {
            'expired-startup'
        } else {
            'stale-generation'
        }
        if (-not $Apply) {
            $actions.Add(
                "WOULD-REPLACE $replacementReason consumer-loop:codex-tools-1 pid=$($staleProcess.ProcessId)"
            )
        }
        else {
            $stalePid = [int]$staleProcess.ProcessId
            $stoppedCount = Stop-VerifiedProcessTree `
                -RootProcess $staleProcess `
                -InitialProcesses $processes `
                -ConflictPath $toolsConflictPath
            if ($stoppedCount -gt 0) {
                $actions.Add(
                    "STOPPED $replacementReason consumer-loop:codex-tools-1 tree=$stoppedCount pid=$stalePid"
                )
            }
            else {
                $actions.Add(
                    "STALE-EXITED consumer-loop:codex-tools-1 pid=$stalePid"
                )
            }
            Assert-MachineToolsConfigExact -MachineConfigPath $toolsConfig
            Assert-SupervisorBundleFileIntegrity `
                -RelativePath 'start-wd-tools-consumer.ps1'
            Start-OutOfTaskJobPowerShell `
                $toolsPowerShellHost `
                $toolsArguments `
                'consumer-loop:codex-tools-1'
        }
    }
    elseif ($wrapperProcesses.Count -gt 0 -or $legacyConsumers.Count -gt 0) {
        $wrapperIds = @($wrapperProcesses | ForEach-Object { [string]$_.ProcessId }) -join ','
        $legacyIds = @($legacyConsumers | ForEach-Object { [string]$_.ProcessId }) -join ','
        $actions.Add(
            "CONFLICT tools consumer wrappers=$wrapperIds legacy=$legacyIds; no duplicate launched"
        )
    }
    elseif (-not $Apply) {
        $actions.Add('WOULD-RELAUNCH consumer-loop:codex-tools-1')
    }
    else {
        Assert-MachineToolsConfigExact -MachineConfigPath $toolsConfig
        Assert-SupervisorBundleFileIntegrity `
            -RelativePath 'start-wd-tools-consumer.ps1'
        Start-OutOfTaskJobPowerShell `
            $toolsPowerShellHost `
            $toolsArguments `
            'consumer-loop:codex-tools-1'
    }
}

if ($null -eq $configuration.driver_containment) {
    throw 'supervisor configuration has no driver_containment object'
}
$driver = $configuration.driver_containment
$standingTaskName = Get-RequiredText $driver 'standing_task'
$legacyTaskName = Get-RequiredText $driver 'legacy_task'
$legacyScript = [IO.Path]::GetFullPath(
    (Get-RequiredText $driver 'legacy_script_path')
)

Invoke-TaskContainment $standingTaskName 'deliberate standing-driver HOLD'

$legacyTask = Get-OptionalScheduledTask -TaskName $legacyTaskName
if ($null -eq $legacyTask) {
    $actions.Add("WARN scheduled task '$legacyTaskName' not found for legacy verification")
}
else {
    if (Test-LegacyDriverProvenNonApply $legacyTask $legacyScript) {
        $actions.Add("LEGACY-NONAPPLY verified $legacyTaskName; left unchanged")
    }
    else {
        Invoke-TaskContainment $legacyTaskName 'legacy action is not proven non-Apply'
    }
}

$bridgeNote = 'bridge-last-event-age=unknown'
try {
    $eventsPath = Join-Path $runtimeRoot 'shared\events.jsonl'
    if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
        [void](Assert-WdSupervisorPathWithoutReparse `
            -Path $eventsPath -ExpectedType Leaf)
        Assert-SupervisorBundleFileIntegrity -RelativePath $watcherReaderRelative
        Assert-SupervisorBundleFileIntegrity -RelativePath $watcherLogReaderRelative
        . ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot $watcherReaderRelative)))
        $freshnessResult = Read-BridgeEventTail -Path $eventsPath -MaxLines 80
        if ($freshnessResult.status -in @('BLOCKED', 'RETRY')) {
            throw "bridge freshness snapshot unavailable: $($freshnessResult.reason)"
        }
        $newest = $null
        foreach ($event in @($freshnessResult.rows)) {
            try {
                $timestamp = [string]$event.ts_utc
                if ($timestamp) {
                    $candidate = [DateTimeOffset]::Parse(
                        $timestamp,
                        [Globalization.CultureInfo]::InvariantCulture,
                        [Globalization.DateTimeStyles]::AssumeUniversal
                    ).UtcDateTime
                    if ($null -eq $newest -or $candidate -gt $newest) {
                        $newest = $candidate
                    }
                }
            }
            catch {
                # One malformed timestamp cannot hide other valid rows.
            }
        }
        if ($null -ne $newest) {
            $ageMinutes = ([DateTime]::UtcNow - $newest).TotalMinutes
            $bridgeNote = 'bridge-last-event-age={0:N1}min' -f $ageMinutes
            if ($ageMinutes -gt 30) {
                $actions.Add("ALERT bridge quiet for $([int]$ageMinutes)min")
            }
        }
    }
}
catch {
    $actions.Add("WARN bridge freshness check failed: $($_.Exception.Message)")
}

$mode = if ($Apply) { 'APPLY' } else { 'dry-run' }
$timestampUtc = [DateTime]::UtcNow.ToString(
    'yyyy-MM-ddTHH:mm:ssZ',
    [Globalization.CultureInfo]::InvariantCulture
)
$summary = if ($actions.Count -eq 0) {
    'all configured helpers healthy; driver HOLD verified'
}
else {
    $actions -join '; '
}
$line = "[$timestampUtc] [$mode] host=$powerShellHost; $bridgeNote :: $summary"
Write-SupervisorLogLine -Path $logFull -Line $line -Apply:$Apply
$line
$conflicts = @($actions | Where-Object { [string]$_ -cmatch '^CONFLICT\s' })
if ($conflicts.Count -gt 0) {
    throw "supervisor reconciliation conflict: $($conflicts -join '; ')"
}

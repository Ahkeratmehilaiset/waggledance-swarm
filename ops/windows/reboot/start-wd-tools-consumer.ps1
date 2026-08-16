#requires -Version 5.1
<#
.SYNOPSIS
    Start the durable codex-tools-1 bridge consumer from a pinned repo state.

.DESCRIPTION
    Validates the configured C-drive worktree, branch, full commit, and exact
    tracked bootstrap scripts before loading any bridge code. The process
    receives one initial bounded Codex tick so a reboot handoff is read even
    when no wake sentinel exists, then remains in the wake-only consumer loop.

    This wrapper supplies an explicit balanced model and reasoning effort from
    the hash-anchored supervisor configuration.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $Generation,
    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:WdGitExecutable = ''
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
}

function Get-RequiredText {
    param(
        [Parameter(Mandatory)] [psobject] $Object,
        [Parameter(Mandatory)] [string] $Name
    )

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "tools consumer configuration is missing '$Name'"
    }
    return [string]$property.Value
}

function Get-InitialTickDisposition {
    param([Parameter(Mandatory)] [psobject] $Result)

    $exitCodeProperty = $Result.PSObject.Properties['exit_code']
    $timedOutProperty = $Result.PSObject.Properties['codex_timed_out']
    $ranCodexProperty = $Result.PSObject.Properties['ran_codex']
    if (
        $null -eq $exitCodeProperty -or
        $null -eq $timedOutProperty -or
        $null -eq $ranCodexProperty -or
        $exitCodeProperty.Value -isnot [int] -or
        $timedOutProperty.Value -isnot [bool] -or
        $ranCodexProperty.Value -isnot [bool]
    ) {
        return 'invalid'
    }
    if (-not [bool]$ranCodexProperty.Value) {
        return 'failed'
    }
    if (
        [int]$exitCodeProperty.Value -eq 0 -and
        -not [bool]$timedOutProperty.Value
    ) {
        return 'success'
    }
    if (
        [int]$exitCodeProperty.Value -eq 124 -and
        [bool]$timedOutProperty.Value
    ) {
        return 'recoverable_timeout'
    }
    if (
        [int]$exitCodeProperty.Value -ne 0 -and
        -not [bool]$timedOutProperty.Value
    ) {
        return 'recoverable_failure'
    }
    return 'failed'
}

function Resolve-ContainedScript {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string] $RelativePath,
        [Parameter(Mandatory)] [string] $Label
    )

    if ([IO.Path]::IsPathRooted($RelativePath)) {
        throw "$Label must be relative to the pinned worktree"
    }
    $candidate = [IO.Path]::GetFullPath((Join-Path $Worktree $RelativePath))
    $prefix = $Worktree.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escapes the pinned worktree: $RelativePath"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "missing $Label at pinned head: $candidate"
    }
    return $candidate
}

function Resolve-ToolsGitApplication {
    param([Parameter(Mandatory)] [string] $ConfiguredPath)

    if (-not [IO.Path]::IsPathRooted($ConfiguredPath)) {
        throw 'Tools Git executable path must be absolute'
    }
    $candidate = [IO.Path]::GetFullPath($ConfiguredPath)
    if ([IO.Path]::GetExtension($candidate) -cne '.exe') {
        throw 'Tools Git executable must be an .exe application'
    }
    $command = Get-Command `
        -Name $candidate `
        -CommandType Application `
        -ErrorAction Stop
    if (-not ([IO.Path]::GetFullPath([string]$command.Source)).Equals(
            $candidate,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'Tools Git command is not the configured application'
    }
    Assert-FilePathWithoutReparse `
        -Candidate $candidate `
        -Root ([IO.Path]::GetPathRoot($candidate))
    return $candidate
}

function Invoke-GitText {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $ArgumentList,
        [Parameter(Mandatory)] [string] $Operation,
        [string] $GitExecutable = [string]$script:WdGitExecutable
    )

    $gitPath = Resolve-ToolsGitApplication -ConfiguredPath $GitExecutable
    $savedGitEnvironment = @(
        Get-ChildItem Env: |
            Where-Object { [string]$_.Name -cmatch '^(?i:GIT_)' } |
            ForEach-Object {
                [pscustomobject]@{
                    Name = [string]$_.Name
                    Value = [string]$_.Value
                }
            }
    )
    $previousPreference = $ErrorActionPreference
    try {
        foreach ($entry in $savedGitEnvironment) {
            Remove-Item `
                -LiteralPath "Env:$([string]$entry.Name)" `
                -ErrorAction Stop
        }
        $env:GIT_CONFIG_NOSYSTEM = '1'
        $env:GIT_CONFIG_GLOBAL = 'NUL'
        $env:GIT_OPTIONAL_LOCKS = '0'
        $env:GIT_TERMINAL_PROMPT = '0'
        $ErrorActionPreference = 'Continue'
        $output = @(
            & $gitPath --no-replace-objects -C $Worktree @ArgumentList 2>&1
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
        foreach ($entry in @(Get-ChildItem Env: | Where-Object {
                    [string]$_.Name -cmatch '^(?i:GIT_)'
                })) {
            Remove-Item -LiteralPath "Env:$([string]$entry.Name)" `
                -ErrorAction SilentlyContinue
        }
        foreach ($entry in $savedGitEnvironment) {
            [Environment]::SetEnvironmentVariable(
                [string]$entry.Name,
                [string]$entry.Value,
                [EnvironmentVariableTarget]::Process
            )
        }
    }
    if ($exitCode -ne 0) {
        throw "git $Operation failed in ${Worktree}: $($output -join ' ')"
    }
    return (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Read-Utf8FileSnapshot {
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

function Resolve-OwnBundleGeneration {
    param([Parameter(Mandatory)] [string] $ScriptRoot)

    $deploymentPath = Join-Path $ScriptRoot 'deployment-manifest.json'
    if (Test-Path -LiteralPath $deploymentPath -PathType Leaf) {
        $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
        $deploymentSnapshot = Read-Utf8FileSnapshot -Path $deploymentPath
        $actualManifestHash = ([string]$deploymentSnapshot.Hash).ToUpperInvariant()
        if (
            $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
            $actualManifestHash -cne $expectedManifestHash.ToUpperInvariant()
        ) {
            throw 'Tools launcher deployment manifest is not externally anchored'
        }
        $deployment = [string]$deploymentSnapshot.Text |
            ConvertFrom-Json -ErrorAction Stop
        $generation = ([string]$deployment.source_commit).ToLowerInvariant()
        $expectedHashProperty = $deployment.files.PSObject.Properties[
            'start-wd-tools-consumer.ps1'
        ]
        $scriptPath = Join-Path $ScriptRoot 'start-wd-tools-consumer.ps1'
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
            throw 'Tools launcher deployment generation is not exact'
        }
        return $generation
    }

    $sourceGeneration = (
        Invoke-GitText `
            -Worktree $ScriptRoot `
            -ArgumentList @('rev-parse', 'HEAD') `
            -Operation 'source generation validation'
    ).ToLowerInvariant()
    if ($sourceGeneration -cnotmatch '^[0-9a-f]{40}$') {
        throw 'Tools launcher source generation is not a full Git commit'
    }
    return $sourceGeneration
}

function Assert-TrackedScriptsMatchHead {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $RelativePaths,
        [Parameter(Mandatory)] [string] $Label
    )

    $gitPaths = @()
    foreach ($relativePath in $RelativePaths) {
        if (
            [string]::IsNullOrWhiteSpace($relativePath) -or
            [IO.Path]::IsPathRooted($relativePath)
        ) {
            throw "$Label contains a non-relative Git path"
        }
        $candidate = [IO.Path]::GetFullPath(
            (Join-Path $Worktree $relativePath)
        )
        $worktreePrefix = $Worktree.TrimEnd('\') + '\'
        if (-not $candidate.StartsWith(
                $worktreePrefix,
                [StringComparison]::OrdinalIgnoreCase
            )) {
            throw "$Label path escapes the pinned worktree: $relativePath"
        }
        $gitPath = $relativePath.Replace('\', '/')
        [void](Invoke-GitText `
            -Worktree $Worktree `
            -ArgumentList @(
                'ls-files',
                '--error-unmatch',
                '--',
                $gitPath
            ) `
            -Operation "$Label tracked-file validation")
        $gitPaths += $gitPath
    }

    $statusArguments = @(
        'status',
        '--porcelain=v1',
        '--untracked-files=all',
        '--'
    ) + $gitPaths
    $status = Invoke-GitText `
        -Worktree $Worktree `
        -ArgumentList $statusArguments `
        -Operation "$Label HEAD validation"
    if (-not [string]::IsNullOrWhiteSpace($status)) {
        throw "$Label does not match pinned HEAD: $status"
    }
}

function Assert-ToolsBootstrapIntegrity {
    param(
        [Parameter(Mandatory)] [string] $ScriptRoot,
        [Parameter(Mandatory)] [string] $BootstrapRoot,
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $LoadedConfigHash
    )

    $trustedDrive = [IO.Path]::GetPathRoot(
        [IO.Path]::GetFullPath($ScriptRoot)
    )
    Assert-DirectoryPathWithoutReparse `
        -Candidate $ScriptRoot -Root $trustedDrive
    Assert-DirectoryPathWithoutReparse `
        -Candidate $BootstrapRoot -Root $trustedDrive
    Assert-FilePathWithoutReparse `
        -Candidate $ConfigPath -Root $trustedDrive

    $deploymentPath = Join-Path $ScriptRoot 'deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $deploymentPath -PathType Leaf)) {
        $sourceTop = [IO.Path]::GetFullPath(
            (Invoke-GitText `
                -Worktree $ScriptRoot `
                -ArgumentList @('rev-parse', '--show-toplevel') `
                -Operation 'source bootstrap top-level validation')
        )
        $expectedSourceRoot = [IO.Path]::GetFullPath(
            (Join-Path $sourceTop '.agent-bridge\bin')
        )
        if (-not $BootstrapRoot.Equals(
                $expectedSourceRoot,
                [StringComparison]::OrdinalIgnoreCase
            )) {
            throw 'source Tools bootstrap root is not canonical'
        }
        Assert-TrackedScriptsMatchHead `
            -Worktree $sourceTop `
            -RelativePaths @(
                '.agent-bridge/bin',
                'configs/bridge_identity_registry.json'
            ) `
            -Label 'source Tools bootstrap inputs'
        return
    }

    Assert-FilePathWithoutReparse `
        -Candidate $deploymentPath -Root $trustedDrive

    $expectedBundleRoot = [IO.Path]::GetFullPath(
        (Join-Path $ScriptRoot 'tools-bootstrap\.agent-bridge\bin')
    )
    if (-not $BootstrapRoot.Equals(
            $expectedBundleRoot,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'deployed Tools bootstrap root is outside its reboot bundle'
    }
    if (-not (Test-Path -LiteralPath $BootstrapRoot -PathType Container)) {
        throw "deployed Tools bootstrap directory is missing: $BootstrapRoot"
    }
    $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
    $deploymentSnapshot = Read-Utf8FileSnapshot -Path $deploymentPath
    if (
        $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$deploymentSnapshot.Hash -cne
            $expectedManifestHash.ToUpperInvariant()
    ) {
        throw 'Tools deployment manifest changed after external attestation'
    }
    $deployment = [string]$deploymentSnapshot.Text |
        ConvertFrom-Json -ErrorAction Stop
    foreach ($topLevelName in @(
            'Invoke-WdToolsCodex.ps1',
            'wd_supervisor_loop.json'
        )) {
        $topLevelProperty = $deployment.files.PSObject.Properties[$topLevelName]
        $topLevelPath = Join-Path $ScriptRoot $topLevelName
        Assert-FilePathWithoutReparse `
            -Candidate $topLevelPath -Root $trustedDrive
        if (
            $null -eq $topLevelProperty -or
            -not (Test-Path -LiteralPath $topLevelPath -PathType Leaf) -or
            (Get-FileHash -LiteralPath $topLevelPath -Algorithm SHA256).Hash -cne
                [string]$topLevelProperty.Value
        ) {
            throw "Tools bundle dependency hash mismatch: $topLevelName"
        }
    }
    $bundledConfigPath = Join-Path $ScriptRoot 'wd_supervisor_loop.json'
    $machineConfigHash = (
        Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256
    ).Hash
    $bundledConfigHash = (
        Get-FileHash -LiteralPath $bundledConfigPath -Algorithm SHA256
    ).Hash
    if (
        $LoadedConfigHash -cne $bundledConfigHash -or
        $machineConfigHash -cne $bundledConfigHash
    ) {
        throw 'machine Tools config differs from the externally anchored bundle'
    }
    $manifestPrefix = 'tools-bootstrap/.agent-bridge/bin/'
    $expectedFiles = @{}
    foreach ($property in @($deployment.files.PSObject.Properties)) {
        $relativeName = [string]$property.Name
        if (-not $relativeName.StartsWith(
                $manifestPrefix,
                [StringComparison]::Ordinal
            )) {
            continue
        }
        $leaf = $relativeName.Substring($manifestPrefix.Length)
        if (
            [string]::IsNullOrWhiteSpace($leaf) -or
            $leaf.IndexOfAny([char[]]@('\', '/')) -ge 0
        ) {
            throw "unsafe Tools bootstrap manifest path: $relativeName"
        }
        $candidate = Join-Path $BootstrapRoot $leaf
        Assert-FilePathWithoutReparse `
            -Candidate $candidate -Root $trustedDrive
        if (
            -not (Test-Path -LiteralPath $candidate -PathType Leaf) -or
            (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -cne
                [string]$property.Value
        ) {
            throw "Tools bootstrap bundle hash mismatch: $relativeName"
        }
        $expectedFiles[$leaf.ToLowerInvariant()] = $true
    }
    foreach ($requiredLeaf in @(
            'BridgeIncrementalReader.ps1',
            'BridgeLogReader.ps1',
            'Send-Liveness.ps1',
            'Start-AgentBridgeConsumerLoop.ps1',
            'Start-AgentBridgeSession.ps1',
            'Start-BridgeHeartbeat.ps1',
            'Test-BridgeWake.ps1',
            'Watch-Bridge.ps1',
            'Write-AgentEvent.ps1'
        )) {
        if (-not $expectedFiles.ContainsKey($requiredLeaf.ToLowerInvariant())) {
            throw "Tools bootstrap manifest is missing required helper: $requiredLeaf"
        }
    }
    $actualFiles = @(
        Get-ChildItem -LiteralPath $BootstrapRoot -File -ErrorAction Stop
    )
    if ($actualFiles.Count -ne $expectedFiles.Count) {
        throw 'Tools bootstrap bundle contains an unexpected file set'
    }
    foreach ($file in $actualFiles) {
        if (-not $expectedFiles.ContainsKey($file.Name.ToLowerInvariant())) {
            throw "Tools bootstrap bundle contains an unexpected file: $($file.Name)"
        }
    }
    $registryRelative = 'tools-bootstrap/configs/bridge_identity_registry.json'
    $registryHashProperty = $deployment.files.PSObject.Properties[$registryRelative]
    $registryPath = Join-Path (
        Split-Path -Parent (Split-Path -Parent $BootstrapRoot)
    ) 'configs\bridge_identity_registry.json'
    Assert-FilePathWithoutReparse `
        -Candidate $registryPath -Root $trustedDrive
    if (
        $null -eq $registryHashProperty -or
        -not (Test-Path -LiteralPath $registryPath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $registryPath -Algorithm SHA256).Hash -cne
            [string]$registryHashProperty.Value
    ) {
        throw 'Tools bridge identity registry bundle hash mismatch'
    }
}

function Test-PathAtOrBelow {
    param(
        [Parameter(Mandatory)] [string] $Candidate,
        [Parameter(Mandatory)] [string] $Root
    )

    try {
        $candidateFull = [IO.Path]::GetFullPath(
            $Candidate.Trim().Trim([char]34)
        ).TrimEnd([char]92, [char]47)
        $rawRoot = [IO.Path]::GetFullPath($Root.Trim().Trim([char]34))
        $rootFull = if ($rawRoot.Equals(
                [IO.Path]::GetPathRoot($rawRoot),
                [StringComparison]::OrdinalIgnoreCase
            )) { $rawRoot } else { $rawRoot.TrimEnd([char]92, [char]47) }
    }
    catch {
        return $false
    }

    return (
        $candidateFull.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase) -or
        $candidateFull.StartsWith(
            $rootFull.TrimEnd([char]92, [char]47) +
                [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    )
}

function Assert-DirectoryPathWithoutReparse {
    param(
        [Parameter(Mandatory)] [string] $Candidate,
        [Parameter(Mandatory)] [string] $Root,
        [switch] $AllowMissing
    )

    $candidateFull = [IO.Path]::GetFullPath($Candidate).TrimEnd(
        [char]92,
        [char]47
    )
    $rawRoot = [IO.Path]::GetFullPath($Root)
    $rootFull = if ($rawRoot.Equals(
            [IO.Path]::GetPathRoot($rawRoot),
            [StringComparison]::OrdinalIgnoreCase
        )) { $rawRoot } else { $rawRoot.TrimEnd([char]92, [char]47) }
    if (-not (Test-PathAtOrBelow -Candidate $candidateFull -Root $rootFull)) {
        throw "directory path escaped its trusted root: $candidateFull"
    }
    if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
        throw "trusted directory root is missing: $rootFull"
    }
    $rootItem = Get-Item -LiteralPath $rootFull -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "trusted directory root cannot be a reparse point: $rootFull"
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
        if (-not (Test-Path -LiteralPath $current)) {
            if ($AllowMissing) {
                return
            }
            throw "required directory path component is missing: $current"
        }
        if (-not (Test-Path -LiteralPath $current -PathType Container)) {
            throw "directory path component is not a directory: $current"
        }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "directory path component cannot be a reparse point: $current"
        }
    }
}

function Assert-FilePathWithoutReparse {
    param(
        [Parameter(Mandatory)] [string] $Candidate,
        [Parameter(Mandatory)] [string] $Root
    )

    $candidateFull = [IO.Path]::GetFullPath($Candidate)
    Assert-DirectoryPathWithoutReparse `
        -Candidate (Split-Path -Parent $candidateFull) `
        -Root $Root
    if (-not (Test-Path -LiteralPath $candidateFull -PathType Leaf)) {
        throw "required trusted file is missing: $candidateFull"
    }
    $item = Get-Item -LiteralPath $candidateFull -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "trusted file cannot be a reparse point: $candidateFull"
    }
}

function New-CodexSandboxPath {
    param(
        [Parameter(Mandatory)] [string] $CurrentPath,
        [Parameter(Mandatory)] [string] $PythonExecutable,
        [Parameter(Mandatory)] [string] $PowerShellExecutable,
        [string] $GitExecutable = '',
        [Parameter(Mandatory)] [string[]] $WindowsAppsRoots
    )

    $pythonFull = [IO.Path]::GetFullPath($PythonExecutable)
    $powerShellFull = [IO.Path]::GetFullPath($PowerShellExecutable)
    $pythonDirectory = Split-Path -Parent $pythonFull
    $candidateEntries = New-Object 'System.Collections.Generic.List[string]'
    $candidateEntries.Add((Split-Path -Parent $powerShellFull))
    $candidateEntries.Add([Environment]::SystemDirectory)
    $candidateEntries.Add($pythonDirectory)
    $candidateEntries.Add((Join-Path $pythonDirectory 'Scripts'))
    if (-not [string]::IsNullOrWhiteSpace($GitExecutable)) {
        $candidateEntries.Add((Split-Path -Parent (
            [IO.Path]::GetFullPath($GitExecutable)
        )))
    }
    foreach ($entry in @($CurrentPath -split [IO.Path]::PathSeparator)) {
        $candidateEntries.Add([string]$entry)
    }

    $plannedEntries = New-Object 'System.Collections.Generic.List[string]'
    $removedEntries = New-Object 'System.Collections.Generic.List[string]'
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' (
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($candidateEntry in $candidateEntries) {
        if ([string]::IsNullOrWhiteSpace($candidateEntry)) {
            continue
        }
        $trimmedEntry = $candidateEntry.Trim().Trim([char]34)
        if (-not [IO.Path]::IsPathRooted($trimmedEntry)) {
            throw "Tools process PATH contains a relative entry: $trimmedEntry"
        }
        $entryFull = [IO.Path]::GetFullPath($trimmedEntry)
        $entryRoot = [IO.Path]::GetPathRoot($entryFull)
        if (-not $entryFull.Equals(
                $entryRoot,
                [StringComparison]::OrdinalIgnoreCase
            )) {
            $entryFull = $entryFull.TrimEnd([char]92, [char]47)
        }

        $isWindowsApps = $false
        foreach ($windowsAppsRoot in $WindowsAppsRoots) {
            if (
                -not [string]::IsNullOrWhiteSpace($windowsAppsRoot) -and
                (Test-PathAtOrBelow $entryFull $windowsAppsRoot)
            ) {
                $isWindowsApps = $true
                break
            }
        }
        if ($isWindowsApps) {
            $removedEntries.Add($entryFull)
            continue
        }
        if ($seen.Add($entryFull)) {
            $plannedEntries.Add($entryFull)
        }
    }

    return [pscustomobject][ordered]@{
        Path = $plannedEntries -join [IO.Path]::PathSeparator
        Entries = @($plannedEntries)
        RemovedWindowsAppsEntries = @($removedEntries)
    }
}

function Resolve-ToolsPythonExecutable {
    param(
        [Parameter(Mandatory)] [string] $ConfiguredPath,
        [Parameter(Mandatory)] [string[]] $WindowsAppsRoots
    )

    if (-not [IO.Path]::IsPathRooted($ConfiguredPath)) {
        throw 'Tools Python executable path must be absolute'
    }
    $pythonFull = [IO.Path]::GetFullPath($ConfiguredPath)
    if ([IO.Path]::GetExtension($pythonFull) -cne '.exe') {
        throw 'Tools Python executable must be an .exe application'
    }
    $localProgramsRoot = [IO.Path]::GetFullPath(
        (Join-Path (
            [Environment]::GetFolderPath(
                [Environment+SpecialFolder]::LocalApplicationData
            )
        ) 'Programs\Python')
    ).TrimEnd([char]92, [char]47)
    if (-not (Test-PathAtOrBelow -Candidate $pythonFull -Root $localProgramsRoot)) {
        throw "Tools Python is outside the trusted per-user Python root: $pythonFull"
    }
    Assert-FilePathWithoutReparse `
        -Candidate $pythonFull `
        -Root ([IO.Path]::GetPathRoot($pythonFull))
    $command = Get-Command `
        -Name $pythonFull `
        -CommandType Application `
        -ErrorAction Stop
    if (-not ([IO.Path]::GetFullPath([string]$command.Source)).Equals(
            $pythonFull,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'Tools Python command is not the configured application'
    }
    foreach ($windowsAppsRoot in $WindowsAppsRoots) {
        if (Test-PathAtOrBelow $pythonFull $windowsAppsRoot) {
            throw "Tools Python resolves inside WindowsApps and is not sandbox-launchable: $pythonFull"
        }
    }
    return $pythonFull
}

function Resolve-ToolsCodexApplication {
    $roamingRoot = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::ApplicationData
    )
    if ([string]::IsNullOrWhiteSpace($roamingRoot)) {
        throw 'Tools roaming application-data root is unavailable'
    }
    $candidate = [IO.Path]::GetFullPath(
        (Join-Path $roamingRoot (
            'npm\node_modules\@openai\codex\node_modules\' +
            '@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\' +
            'bin\codex.exe'
        ))
    )
    Assert-FilePathWithoutReparse `
        -Candidate $candidate `
        -Root ([IO.Path]::GetPathRoot($candidate))
    $command = Get-Command `
        -Name $candidate `
        -CommandType Application `
        -ErrorAction Stop
    if (-not ([IO.Path]::GetFullPath([string]$command.Source)).Equals(
            $candidate,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'Tools Codex command is not the trusted npm-native application'
    }
    return $candidate
}

function Test-CodexSandboxShell {
    param(
        [Parameter(Mandatory)] [string] $CodexCommand,
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string] $ShellPath
    )

    $marker = 'WD_TOOLS_SANDBOX_OK'
    $probeArguments = @(
        'sandbox',
        '-P', ':workspace',
        '-C', $Worktree,
        '--',
        $ShellPath,
        '-NoLogo',
        '-NoProfile',
        '-NonInteractive',
        '-Command', "[Console]::Out.Write('$marker')"
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $probeOutput = @(& $CodexCommand @probeArguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $probeLines = @(
        $probeOutput |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($exitCode -ne 0 -or $probeLines -cnotcontains $marker) {
        throw (
            "Codex workspace sandbox cannot launch the validated shell " +
            "'$ShellPath' (exit=$exitCode): $($probeLines -join ' | ')"
        )
    }
}

$isDeployedLauncher = Test-Path -LiteralPath (
    Join-Path $PSScriptRoot 'deployment-manifest.json'
) -PathType Leaf
if (-not $isDeployedLauncher -and -not $ValidateOnly) {
    throw 'source Tools launcher supports -ValidateOnly only; live use requires a deployed bundle'
}

$configFull = [IO.Path]::GetFullPath($ConfigPath)
$toolsTrustedDrive = [IO.Path]::GetPathRoot(
    [IO.Path]::GetFullPath($PSScriptRoot)
)
Assert-DirectoryPathWithoutReparse `
    -Candidate $PSScriptRoot -Root $toolsTrustedDrive
if (-not (Test-Path -LiteralPath $configFull -PathType Leaf)) {
    throw "tools consumer configuration not found: $configFull"
}
Assert-FilePathWithoutReparse `
    -Candidate $configFull -Root $toolsTrustedDrive
$configSnapshot = Read-Utf8FileSnapshot -Path $configFull
$loadedConfigHash = [string]$configSnapshot.Hash
$configuration = [string]$configSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
if ([string]$configuration.schema -cne 'wd.supervisor-loop.v2') {
    throw "unsupported tools consumer configuration schema: $($configuration.schema)"
}
if ($null -eq $configuration.watchers) {
    throw 'tools consumer configuration has no watchers object'
}
$script:WdGitExecutable = Resolve-ToolsGitApplication `
    -ConfiguredPath (Get-RequiredText $configuration.watchers 'git_executable')
if ($null -eq $configuration.tools_consumer) {
    throw 'tools consumer configuration has no tools_consumer object'
}

$tools = $configuration.tools_consumer
if (-not [bool]$tools.enabled) {
    throw 'tools consumer is disabled in configuration'
}

$runtimeRoot = [IO.Path]::GetFullPath((Get-RequiredText $configuration 'runtime_root'))
$worktree = [IO.Path]::GetFullPath((Get-RequiredText $tools 'worktree'))
$primaryRepoRoot = [IO.Path]::GetFullPath(
    (Get-RequiredText $tools 'primary_repo_root')
)
$expectedCommonGitDir = [IO.Path]::GetFullPath(
    (Get-RequiredText $tools 'expected_common_git_dir')
)
$dedicatedProperty = $tools.PSObject.Properties['require_dedicated_worktree']
if (
    $null -eq $dedicatedProperty -or
    $dedicatedProperty.Value -isnot [bool] -or
    -not [bool]$dedicatedProperty.Value
) {
    throw 'tools consumer configuration requires require_dedicated_worktree=true'
}
$requireDedicatedWorktree = $true
$expectedBranch = Get-RequiredText $tools 'expected_branch'
$expectedHead = (Get-RequiredText $tools 'expected_head').ToLowerInvariant()
$bundleGeneration = Resolve-OwnBundleGeneration -ScriptRoot $PSScriptRoot
if ($Generation -cne $bundleGeneration) {
    throw (
        "tools process generation mismatch: expected '$bundleGeneration', " +
        "got '$Generation'"
    )
}
$agent = Get-RequiredText $tools 'agent'
$agentUuid = (Get-RequiredText $tools 'agent_uuid').ToLowerInvariant()
$role = Get-RequiredText $tools 'role'
$runIdPrefix = Get-RequiredText $tools 'run_id_prefix'
$logDir = [IO.Path]::GetFullPath((Get-RequiredText $tools 'log_dir'))
$readinessPath = [IO.Path]::GetFullPath(
    (Get-RequiredText $tools 'readiness_path')
)
$sandbox = Get-RequiredText $tools 'sandbox'
$approvalPolicy = Get-RequiredText $tools 'approval_policy'
$prompt = Get-RequiredText $tools 'prompt'
$resumePolicy = Get-RequiredText $tools 'resume_policy'
$model = Get-RequiredText $tools 'model'
$reasoningEffort = Get-RequiredText $tools 'reasoning_effort'

if ([IO.Path]::GetPathRoot($worktree).TrimEnd('\') -cne 'C:') {
    throw "tools worktree must be on persistent C: drive: $worktree"
}
if (-not (Test-Path -LiteralPath $worktree -PathType Container)) {
    throw "tools worktree does not exist: $worktree"
}
if (-not (Test-Path -LiteralPath $primaryRepoRoot -PathType Container)) {
    throw "primary repo root does not exist: $primaryRepoRoot"
}
if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
    throw "bridge runtime root does not exist: $runtimeRoot"
}
if (-not (Test-Path -LiteralPath $expectedCommonGitDir -PathType Container)) {
    throw "expected common Git directory does not exist: $expectedCommonGitDir"
}
if (-not (Test-PathAtOrBelow -Candidate $expectedCommonGitDir -Root $primaryRepoRoot)) {
    throw (
        "expected common Git directory is outside the primary repo: " +
        "$expectedCommonGitDir"
    )
}
if (-not (Test-Path -LiteralPath (Join-Path $worktree '.git') -PathType Leaf)) {
    throw "tools worktree is not a dedicated linked Git worktree: $worktree"
}
if (
    $worktree.TrimEnd('\').Equals(
        $primaryRepoRoot.TrimEnd('\'),
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "dedicated Tools worktree must differ from the primary repo: $worktree"
}
if (-not (Test-PathAtOrBelow -Candidate $logDir -Root $worktree)) {
    throw "tools log_dir must stay inside the dedicated worktree: $logDir"
}
$readinessRoot = [IO.Path]::GetFullPath('C:\Python\wd-reboot-runtime')
if (
    -not (Split-Path -Parent $readinessPath).Equals(
        $readinessRoot,
        [StringComparison]::OrdinalIgnoreCase
    ) -or
    [IO.Path]::GetExtension($readinessPath) -cne '.json'
) {
    throw "tools readiness_path must be one JSON file in ${readinessRoot}: $readinessPath"
}
if ($expectedHead -cnotmatch '^[0-9a-f]{40}$') {
    throw 'tools expected_head must be a full lowercase Git commit'
}
if ($agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "invalid tools agent identity: $agent"
}
if ($agentUuid -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
    throw 'tools agent_uuid must be a UUID'
}
if ($role -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
    throw "invalid tools role: $role"
}
if ($runIdPrefix -cnotmatch '^[A-Za-z0-9._:-]{1,80}$') {
    throw 'tools run_id_prefix is malformed'
}
if ($sandbox -cnotin @('read-only', 'workspace-write', 'danger-full-access')) {
    throw "unsupported tools sandbox: $sandbox"
}
if ($approvalPolicy -cnotin @('untrusted', 'on-failure', 'on-request', 'never')) {
    throw "unsupported tools approval policy: $approvalPolicy"
}
if ($resumePolicy -cnotin @('pinned', 'current_worktree')) {
    throw "unsupported tools resume_policy: $resumePolicy"
}
if ($model -cne 'gpt-5.6-terra') {
    throw "unsupported Tools model: $model"
}
if ($reasoningEffort -cnotin @('low', 'medium', 'high', 'xhigh', 'max')) {
    throw "unsupported Tools reasoning_effort: $reasoningEffort"
}

$codexWritableDirectories = @(
    (Join-Path $runtimeRoot 'shared'),
    (Join-Path (Join-Path $runtimeRoot 'outbox') $agent),
    (Join-Path $runtimeRoot 'spool'),
    (Join-Path $runtimeRoot 'work_queue')
) | ForEach-Object { [IO.Path]::GetFullPath($_) }
foreach ($writableDirectory in $codexWritableDirectories) {
    Assert-DirectoryPathWithoutReparse `
        -Candidate $writableDirectory `
        -Root $runtimeRoot `
        -AllowMissing
    if (-not $ValidateOnly) {
        if (-not (Test-Path -LiteralPath $writableDirectory -PathType Container)) {
            [void](New-Item `
                -ItemType Directory `
                -Path $writableDirectory `
                -Force `
                -ErrorAction Stop)
        }
        Assert-DirectoryPathWithoutReparse `
            -Candidate $writableDirectory `
            -Root $runtimeRoot
    }
}

$capabilities = @(
    @($tools.capabilities) |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($capabilities.Count -eq 0) {
    throw 'tools capabilities must not be empty'
}
foreach ($capability in $capabilities) {
    if ($capability -cnotmatch '^[a-z][a-z0-9_.:-]{1,64}$') {
        throw "invalid tools capability: $capability"
    }
}

$pollSeconds = [int]$tools.poll_seconds
$codexTimeoutSeconds = [int]$tools.codex_timeout_seconds
if ($pollSeconds -lt 1) {
    throw 'tools poll_seconds must be at least 1'
}
if ($codexTimeoutSeconds -lt 1) {
    throw 'tools codex_timeout_seconds must be at least 1'
}

$inside = Invoke-GitText $worktree @('rev-parse', '--is-inside-work-tree') 'worktree validation'
if ($inside -cne 'true') {
    throw "configured tools path is not a Git worktree: $worktree"
}
$actualTop = [IO.Path]::GetFullPath(
    (Invoke-GitText $worktree @('rev-parse', '--show-toplevel') 'top-level validation')
)
if (-not $actualTop.Equals($worktree, [StringComparison]::OrdinalIgnoreCase)) {
    throw "tools worktree has unexpected Git top-level: $actualTop"
}
$actualCommonGitDir = [IO.Path]::GetFullPath(
    (Invoke-GitText $worktree @(
            'rev-parse',
            '--path-format=absolute',
            '--git-common-dir'
        ) 'common Git directory validation')
)
if (-not $actualCommonGitDir.Equals(
        $expectedCommonGitDir,
        [StringComparison]::OrdinalIgnoreCase
    )) {
    throw (
        "tools worktree has unexpected common Git directory: " +
        "$actualCommonGitDir"
    )
}
$actualBranch = Invoke-GitText $worktree @('symbolic-ref', '--quiet', '--short', 'HEAD') 'branch validation'
$actualHead = (Invoke-GitText $worktree @('rev-parse', 'HEAD') 'head validation').ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($actualBranch)) {
    throw 'tools consumer cannot resume a detached HEAD'
}
if ($actualHead -cnotmatch '^[0-9a-f]{40}$') {
    throw "tools worktree resolved a malformed HEAD: $actualHead"
}
$pinExact = $actualBranch -ceq $expectedBranch -and $actualHead -ceq $expectedHead
if (-not $pinExact -and $resumePolicy -ceq 'pinned') {
    if ($actualBranch -cne $expectedBranch) {
        throw "tools worktree branch mismatch: expected '$expectedBranch', got '$actualBranch'"
    }
    throw "tools worktree head mismatch: expected '$expectedHead', got '$actualHead'"
}
if (-not $pinExact -and -not $ValidateOnly) {
    Write-Warning (
        'Tools is resuming its canonical current worktree at ' +
        "$actualBranch@$actualHead instead of the deployment baseline " +
        "$expectedBranch@$expectedHead"
    )
}

$sessionScriptRelative = (
    Get-RequiredText $tools 'session_script_relative'
).Replace('/', '\')
$consumerScriptRelative = (
    Get-RequiredText $tools 'consumer_script_relative'
).Replace('/', '\')
$expectedBridgeBinRelative = '.agent-bridge\bin'
if (
    (Split-Path -Parent $sessionScriptRelative) -cne $expectedBridgeBinRelative -or
    (Split-Path -Parent $consumerScriptRelative) -cne $expectedBridgeBinRelative
) {
    throw 'Tools session and consumer scripts must come from .agent-bridge\bin'
}
$bootstrapRoot = if (Test-Path -LiteralPath (
        Join-Path $PSScriptRoot 'deployment-manifest.json'
    ) -PathType Leaf) {
    [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot 'tools-bootstrap\.agent-bridge\bin')
    )
} else {
    $sourceTop = [IO.Path]::GetFullPath(
        (Invoke-GitText `
            $PSScriptRoot `
            @('rev-parse', '--show-toplevel') `
            'source top-level validation')
    )
    [IO.Path]::GetFullPath((Join-Path $sourceTop '.agent-bridge\bin'))
}
$targetState = $configuration.target_state
if (
    $null -eq $targetState -or
    [string]$targetState.id -cne 'wd-swarm-target-state-v1' -or
    [string]$targetState.capability_effect -cne 'none' -or
    [string]$targetState.relative_path -cne 'WD_SWARM_TARGET_STATE_V1.md' -or
    [string]$targetState.sha256 -cnotmatch '^[0-9A-F]{64}$'
) {
    throw 'Tools target-state manifest is missing or unsafe'
}
$targetStatePath = Join-Path $PSScriptRoot ([string]$targetState.relative_path)
if (
    -not (Test-Path -LiteralPath $targetStatePath -PathType Leaf) -or
    (Get-FileHash -LiteralPath $targetStatePath -Algorithm SHA256).Hash -cne
        [string]$targetState.sha256
) {
    throw 'Tools target-state document hash mismatch'
}
Assert-ToolsBootstrapIntegrity `
    -ScriptRoot $PSScriptRoot `
    -BootstrapRoot $bootstrapRoot `
    -ConfigPath $configFull `
    -LoadedConfigHash $loadedConfigHash
$sessionScript = Resolve-ContainedScript `
    $bootstrapRoot `
    ([IO.Path]::GetFileName($sessionScriptRelative)) `
    'bridge session script'
$consumerScript = Resolve-ContainedScript `
    $bootstrapRoot `
    ([IO.Path]::GetFileName($consumerScriptRelative)) `
    'bridge consumer script'
$codexShim = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot 'Invoke-WdToolsCodex.ps1')
)
if (-not [IO.File]::Exists($codexShim)) {
    throw "Tools Codex PATH shim is missing: $codexShim"
}

$windowsAppsRoots = @(
    Join-Path (
        [Environment]::GetFolderPath(
            [Environment+SpecialFolder]::ProgramFiles
        )
    ) 'WindowsApps'
    Join-Path (
        [Environment]::GetFolderPath(
            [Environment+SpecialFolder]::LocalApplicationData
        )
    ) 'Microsoft\WindowsApps'
)
$pythonExecutable = Resolve-ToolsPythonExecutable `
    -ConfiguredPath (Get-RequiredText $tools 'python_executable') `
    -WindowsAppsRoots $windowsAppsRoots
$systemPowerShell = Join-Path `
    ([Environment]::SystemDirectory) `
    'WindowsPowerShell\v1.0\powershell.exe'
if (-not [IO.File]::Exists($systemPowerShell)) {
    throw "System Windows PowerShell is missing: $systemPowerShell"
}
$codexPathPlan = New-CodexSandboxPath `
    -CurrentPath $env:Path `
    -PythonExecutable $pythonExecutable `
    -PowerShellExecutable $systemPowerShell `
    -GitExecutable $script:WdGitExecutable `
    -WindowsAppsRoots $windowsAppsRoots
$sandboxShell = [IO.Path]::GetFullPath($systemPowerShell)
try {
    $currentPowerShellHost = [IO.Path]::GetFullPath(
        [string](Get-Process -Id $PID -ErrorAction Stop).Path
    )
}
catch {
    throw "Could not resolve the current Tools PowerShell host: $($_.Exception.Message)"
}
$codexCommand = Resolve-ToolsCodexApplication
$codexCommandHash = (
    Get-FileHash -LiteralPath $codexCommand -Algorithm SHA256
).Hash
$pythonExecutableHash = (
    Get-FileHash -LiteralPath $pythonExecutable -Algorithm SHA256
).Hash

$validation = [pscustomobject]@{
    schema = 'wd.tools-consumer-validation.v1'
    config_path = $configFull
    generation = $Generation
    readiness_path = $readinessPath
    runtime_root = $runtimeRoot
    worktree = $worktree
    git_top = $actualTop
    primary_repo_root = $primaryRepoRoot
    common_git_dir = $actualCommonGitDir
    require_dedicated_worktree = $requireDedicatedWorktree
    branch = $actualBranch
    head = $actualHead
    agent = $agent
    agent_uuid = $agentUuid
    role = $role
    capabilities = @($capabilities)
    session_script = $sessionScript
    consumer_script = $consumerScript
    codex_command = $codexCommand
    codex_command_sha256 = $codexCommandHash
    codex_shim = $codexShim
    consumer_host = $currentPowerShellHost
    sandbox_shell = $sandboxShell
    python_executable = $pythonExecutable
    python_executable_sha256 = $pythonExecutableHash
    codex_additional_writable_directories = @($codexWritableDirectories)
    windows_apps_path_entries_removed = @(
        $codexPathPlan.RemovedWindowsAppsEntries
    )
    model = $model
    reasoning_effort = $reasoningEffort
    resume_policy = $resumePolicy
    baseline_branch = $expectedBranch
    baseline_head = $expectedHead
    target_state_id = [string]$targetState.id
    validated = $true
}
if ($ValidateOnly) {
    $validation
    return
}

$processStartUtc = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime()
if (-not (Test-Path -LiteralPath $readinessRoot -PathType Container)) {
    [void](New-Item `
        -ItemType Directory `
        -Path $readinessRoot `
        -Force `
        -ErrorAction Stop)
}
if (Test-Path -LiteralPath $readinessPath -PathType Leaf) {
    Remove-Item -LiteralPath $readinessPath -Force -ErrorAction Stop
}

$env:Path = $codexPathPlan.Path
[Environment]::SetEnvironmentVariable(
    'WD_TOOLS_CODEX_REAL_COMMAND',
    $codexCommand,
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'WD_TOOLS_CODEX_SAFE_PATH',
    $codexPathPlan.Path,
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'WD_TOOLS_CODEX_ADDITIONAL_WRITABLE_DIRS',
    ($codexWritableDirectories | ConvertTo-Json -Compress),
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'WD_TOOLS_CODEX_RUNTIME_ROOT',
    $runtimeRoot,
    'Process'
)
[Environment]::SetEnvironmentVariable(
    'WD_TOOLS_CODEX_REASONING_EFFORT',
    $reasoningEffort,
    'Process'
)
Test-CodexSandboxShell `
    -CodexCommand $codexCommand `
    -Worktree $worktree `
    -ShellPath $sandboxShell

# The supervisor may itself have been invoked from an agent-bound shell. This
# is a new dedicated process, so inherited identity must not constrain the
# configured tools identity before Start-AgentBridgeSession establishes it.
$identityVariables = @(
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
foreach ($variableName in $identityVariables) {
    [Environment]::SetEnvironmentVariable($variableName, $null, 'Process')
}

$runStamp = [DateTime]::UtcNow.ToString(
    'yyyyMMddTHHmmssfffZ',
    [Globalization.CultureInfo]::InvariantCulture
)
$runId = "$runIdPrefix-$runStamp-$PID"
if ($runId.Length -gt 128) {
    throw 'generated tools run id exceeds 128 characters'
}

Assert-ToolsBootstrapIntegrity `
    -ScriptRoot $PSScriptRoot `
    -BootstrapRoot $bootstrapRoot `
    -ConfigPath $configFull `
    -LoadedConfigHash $loadedConfigHash
. $sessionScript `
    -Agent $agent `
    -RuntimeRoot $runtimeRoot `
    -RepoRoot $worktree `
    -PrimaryRepoRoot $primaryRepoRoot `
    -RequireDedicatedWorktree:$requireDedicatedWorktree `
    -RunId $runId `
    -Role $role `
    -AgentUuid $agentUuid `
    -Capabilities $capabilities `
    -SkipBridgeRead `
    -SkipGitStatus `
    -SkipWakeWatcher `
    -SkipHeartbeatJob

$writer = Join-Path $bootstrapRoot 'Write-AgentEvent.ps1'
Assert-ToolsBootstrapIntegrity `
    -ScriptRoot $PSScriptRoot `
    -BootstrapRoot $bootstrapRoot `
    -ConfigPath $configFull `
    -LoadedConfigHash $loadedConfigHash
if (-not (Test-Path -LiteralPath $writer -PathType Leaf)) {
    throw "Tools bridge writer is missing: $writer"
}
$targetPayload = [ordered]@{
    target_state_id = [string]$targetState.id
    target_state_sha256 = [string]$targetState.sha256
    source_image_sha256 = [string]$targetState.source_image_sha256
    capability_effect = 'none'
    model = $model
    effort = $reasoningEffort
    resume_policy = $resumePolicy
    baseline_branch = $expectedBranch
    baseline_head = $expectedHead
    resumed_branch = $actualBranch
    resumed_head = $actualHead
} | ConvertTo-Json -Compress
& $writer `
    -Agent $agent `
    -Type status `
    -TaskId ([string]$targetState.id) `
    -Status target_state_manifested `
    -Message "Manifested the shared WaggleDance target state for Tools generation $runId; this grants no capability or authority." `
    -RunId $runId `
    -Role $role `
    -AgentUuid $agentUuid `
    -SessionId $runId `
    -Capabilities $capabilities `
    -PayloadJson $targetPayload |
    Out-Host
Assert-ToolsBootstrapIntegrity `
    -ScriptRoot $PSScriptRoot `
    -BootstrapRoot $bootstrapRoot `
    -ConfigPath $configFull `
    -LoadedConfigHash $loadedConfigHash

$canaryTaskId = "wd-append-canary-$runId"
$canaryPayload = [ordered]@{
    schema_version = 1
    generation = $Generation
    target_state_id = [string]$targetState.id
    manifest_writer = 'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'
} | ConvertTo-Json -Compress
$canaryStartedUtc = [DateTimeOffset]::UtcNow
$canaryOutput = @(
    & $writer `
        -Agent $agent `
        -Type status `
        -TaskId $canaryTaskId `
        -Status append_canary `
        -Message "Verified the manifest-hashed canonical writer for $agent generation $runId." `
        -To '' `
        -RunId $runId `
        -Role $role `
        -AgentUuid $agentUuid `
        -SessionId $runId `
        -Capabilities $capabilities `
        -PayloadJson $canaryPayload
)
$canaryCompletedUtc = [DateTimeOffset]::UtcNow
$canaryEvents = @(
    $canaryOutput | Where-Object {
        $_ -is [psobject] -and [string]$_.status -ceq 'append_canary'
    }
)
$canaryLatencyMs = [int64][Math]::Ceiling(
    ($canaryCompletedUtc - $canaryStartedUtc).TotalMilliseconds
)
if (
    $canaryEvents.Count -ne 1 -or
    [string]$canaryEvents[0].agent -cne $agent -or
    [string]$canaryEvents[0].agent_uuid -cne $agentUuid -or
    [string]$canaryEvents[0].run_id -cne $runId -or
    [string]$canaryEvents[0].session_id -cne $runId -or
    [string]$canaryEvents[0].task_id -cne $canaryTaskId -or
    [string]$canaryEvents[0].to -cne '' -or
    [int]$canaryEvents[0].pid -ne $PID -or
    $canaryLatencyMs -gt 5000
) {
    throw "manifest-writer append canary failed for $agent"
}
$canaryOutput | Out-Host
Assert-ToolsBootstrapIntegrity `
    -ScriptRoot $PSScriptRoot `
    -BootstrapRoot $bootstrapRoot `
    -ConfigPath $configFull `
    -LoadedConfigHash $loadedConfigHash

$commonConsumerArguments = @{
    Agent = $agent
    AgentUuid = $agentUuid
    Role = $role
    Capabilities = @($capabilities)
    RuntimeRoot = $runtimeRoot
    Worktree = $worktree
    Sandbox = $sandbox
    ApprovalPolicy = $approvalPolicy
    CodexTimeoutSeconds = $codexTimeoutSeconds
    LogDir = $logDir
    Prompt = $prompt
    CodexCommand = $codexShim
    Model = $model
}

# The first tick is intentionally not WakeOnly. It reads the durable handoff
# immediately after reboot without fabricating an operator/lead wake event.
$initialArguments = @{} + $commonConsumerArguments
$initialArguments['DurationMinutes'] = 0
$initialArguments['MaxIterations'] = 1
$initialArguments['PollSeconds'] = 0
try {
    Assert-ToolsBootstrapIntegrity `
        -ScriptRoot $PSScriptRoot `
        -BootstrapRoot $bootstrapRoot `
        -ConfigPath $configFull `
        -LoadedConfigHash $loadedConfigHash
    $initialOutput = @(& $consumerScript @initialArguments)
}
finally {
    Assert-ToolsBootstrapIntegrity `
        -ScriptRoot $PSScriptRoot `
        -BootstrapRoot $bootstrapRoot `
        -ConfigPath $configFull `
        -LoadedConfigHash $loadedConfigHash
}
$initialResult = @(
    $initialOutput |
        Where-Object {
            $_ -is [psobject] -and
            $_.PSObject.Properties.Name -contains 'exit_code'
        }
) | Select-Object -Last 1
if ($null -eq $initialResult) {
    throw 'initial tools consumer tick returned no structured result'
}
# A structured result after a real Codex launch describes one bounded task,
# not the health of the durable wrapper. Preserve a degraded readiness state
# for a native failure or timeout so the headless service can wait and retry.
# Malformed results and attempts that never launched Codex remain fatal.
$initialTickDisposition = Get-InitialTickDisposition -Result $initialResult
$initialTickTimedOut = $initialTickDisposition -ceq 'recoverable_timeout'
if (
    $initialTickDisposition -cnotin @(
        'success',
        'recoverable_timeout',
        'recoverable_failure'
    )
) {
    throw "initial tools consumer tick failed with exit_code=$($initialResult.exit_code)"
}
$initialReadyStatus = if ($initialTickDisposition -ceq 'success') {
    'ready'
} else {
    'degraded'
}

$readinessTemporary = "$readinessPath.$PID.tmp"
$readinessRecord = [ordered]@{
    schema = 'wd.tools-consumer-ready.v1'
    status = $initialReadyStatus
    generation = $Generation
    pid = $PID
    process_start_utc = $processStartUtc.ToString('o')
    config_path = $configFull
    worktree = $worktree
    branch = $actualBranch
    head = $actualHead
    baseline_branch = $expectedBranch
    baseline_head = $expectedHead
    resume_policy = $resumePolicy
    model = $model
    reasoning_effort = $reasoningEffort
    codex_command = $codexCommand
    codex_command_sha256 = $codexCommandHash
    python_executable = $pythonExecutable
    python_executable_sha256 = $pythonExecutableHash
    target_state_id = [string]$targetState.id
    target_state_sha256 = [string]$targetState.sha256
    target_state_manifested = $true
    run_id = $runId
    session_id = $runId
    append_canary = $true
    append_canary_task_id = $canaryTaskId
    append_canary_event_utc = [string]$canaryEvents[0].ts_utc
    append_canary_latency_ms = $canaryLatencyMs
    initial_tick_disposition = $initialTickDisposition
    initial_tick_exit_code = [int]$initialResult.exit_code
    initial_tick_timed_out = $initialTickTimedOut
    initial_tick_log_path = [string]$initialResult.log_path
    ready_at_utc = [DateTime]::UtcNow.ToString('o')
}
$utf8NoBom = New-Object Text.UTF8Encoding($false)
try {
    [IO.File]::WriteAllText(
        $readinessTemporary,
        (($readinessRecord | ConvertTo-Json -Depth 4) + [Environment]::NewLine),
        $utf8NoBom
    )
    Move-Item `
        -LiteralPath $readinessTemporary `
        -Destination $readinessPath `
        -Force
}
finally {
    if (Test-Path -LiteralPath $readinessTemporary -PathType Leaf) {
        Remove-Item `
            -LiteralPath $readinessTemporary `
            -Force `
            -ErrorAction SilentlyContinue
    }
}

$wakeArguments = @{} + $commonConsumerArguments
$wakeArguments['DurationMinutes'] = 0
$wakeArguments['MaxIterations'] = 1
$wakeArguments['WakeOnly'] = $true
$wakeArguments['PollSeconds'] = 0

# The immutable outer wrapper owns the long-lived loop. Each bounded workspace
# invocation is enclosed by tracked-tree gates so a workspace-write Codex tick
# can never plant bridge code for a later unsandboxed iteration.
while ($true) {
    Assert-ToolsBootstrapIntegrity `
        -ScriptRoot $PSScriptRoot `
        -BootstrapRoot $bootstrapRoot `
        -ConfigPath $configFull `
        -LoadedConfigHash $loadedConfigHash
    try {
        $wakeOutput = @(& $consumerScript @wakeArguments)
    }
    finally {
        Assert-ToolsBootstrapIntegrity `
            -ScriptRoot $PSScriptRoot `
            -BootstrapRoot $bootstrapRoot `
            -ConfigPath $configFull `
            -LoadedConfigHash $loadedConfigHash
    }
    $wakeResult = @(
        $wakeOutput |
            Where-Object {
                $_ -is [psobject] -and
                $_.PSObject.Properties.Name -contains 'exit_code'
            }
    ) | Select-Object -Last 1
    if ($null -eq $wakeResult) {
        throw 'wake-only tools consumer tick returned no structured result'
    }
    Start-Sleep -Seconds $pollSeconds
}

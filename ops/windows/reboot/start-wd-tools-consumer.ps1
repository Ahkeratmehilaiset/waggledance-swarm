#requires -Version 5.1
<#
.SYNOPSIS
    Start the durable codex-tools-1 bridge consumer from a pinned repo state.

.DESCRIPTION
    Validates the configured C-drive worktree, branch, full commit, and exact
    tracked bootstrap scripts before loading any bridge code. The process
    receives one initial bounded Codex tick so a reboot handoff is read even
    when no wake sentinel exists, then remains in the wake-only consumer loop.

    This wrapper deliberately supplies no model override. Codex therefore uses
    the provider default selected by the installed CLI.
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

function Invoke-GitText {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $ArgumentList,
        [Parameter(Mandatory)] [string] $Operation
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& git -C $Worktree @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
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
            'AgentBridgeSessionIdentity.ps1',
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
        $rootFull = [IO.Path]::GetFullPath(
            $Root.Trim().Trim([char]34)
        ).TrimEnd([char]92, [char]47)
    }
    catch {
        return $false
    }

    return (
        $candidateFull.Equals($rootFull, [StringComparison]::OrdinalIgnoreCase) -or
        $candidateFull.StartsWith(
            $rootFull + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    )
}

function New-CodexSandboxPath {
    param(
        [Parameter(Mandatory)] [string] $CurrentPath,
        [Parameter(Mandatory)] [string] $PythonExecutable,
        [Parameter(Mandatory)] [string] $PowerShellExecutable,
        [Parameter(Mandatory)] [string[]] $WindowsAppsRoots
    )

    $pythonFull = [IO.Path]::GetFullPath($PythonExecutable)
    $powerShellFull = [IO.Path]::GetFullPath($PowerShellExecutable)
    $pythonDirectory = Split-Path -Parent $pythonFull
    $candidateEntries = New-Object 'System.Collections.Generic.List[string]'
    $candidateEntries.Add((Split-Path -Parent $powerShellFull))
    $candidateEntries.Add($pythonDirectory)
    $candidateEntries.Add((Join-Path $pythonDirectory 'Scripts'))
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

function Find-ApplicationInPath {
    param(
        [Parameter(Mandatory)] [string] $PathValue,
        [Parameter(Mandatory)] [string[]] $ExecutableNames
    )

    $entries = @(
        $PathValue -split [IO.Path]::PathSeparator |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    foreach ($executableName in $ExecutableNames) {
        foreach ($entry in $entries) {
            $candidate = Join-Path $entry $executableName
            if ([IO.File]::Exists($candidate)) {
                return [IO.Path]::GetFullPath($candidate)
            }
        }
    }
    return $null
}

function Resolve-ToolsPythonExecutable {
    param([Parameter(Mandatory)] [string[]] $WindowsAppsRoots)

    $pythonFull = $null
    $launcher = Get-Command py.exe `
        -CommandType Application `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $launcher) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $pythonOutput = @(
                & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>&1
            )
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($exitCode -ne 0) {
            throw "Python launcher failed with exit code ${exitCode}: $($pythonOutput -join ' ')"
        }
        $pythonLines = @(
            $pythonOutput |
                ForEach-Object { [string]$_ } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
        if ($pythonLines.Count -ne 1) {
            throw 'Python launcher did not return one exact executable path'
        }
        $pythonFull = [IO.Path]::GetFullPath($pythonLines[0].Trim())
    }
    else {
        $python = Get-Command python.exe `
            -CommandType Application `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $python) {
            $pythonFull = [IO.Path]::GetFullPath($python.Source)
        }
    }

    if ([string]::IsNullOrWhiteSpace($pythonFull) -or -not [IO.File]::Exists($pythonFull)) {
        throw 'Could not resolve a real Python executable for the Tools consumer'
    }
    foreach ($windowsAppsRoot in $WindowsAppsRoots) {
        if (Test-PathAtOrBelow $pythonFull $windowsAppsRoot) {
            throw "Tools Python resolves inside WindowsApps and is not sandbox-launchable: $pythonFull"
        }
    }
    return $pythonFull
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
if (-not (Test-Path -LiteralPath $configFull -PathType Leaf)) {
    throw "tools consumer configuration not found: $configFull"
}
$configSnapshot = Read-Utf8FileSnapshot -Path $configFull
$loadedConfigHash = [string]$configSnapshot.Hash
$configuration = [string]$configSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
if ([string]$configuration.schema -cne 'wd.supervisor-loop.v2') {
    throw "unsupported tools consumer configuration schema: $($configuration.schema)"
}
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

if ([IO.Path]::GetPathRoot($worktree).TrimEnd('\') -cne 'C:') {
    throw "tools worktree must be on persistent C: drive: $worktree"
}
if (-not (Test-Path -LiteralPath $worktree -PathType Container)) {
    throw "tools worktree does not exist: $worktree"
}
if (-not (Test-Path -LiteralPath $primaryRepoRoot -PathType Container)) {
    throw "primary repo root does not exist: $primaryRepoRoot"
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
if ($actualBranch -cne $expectedBranch) {
    throw "tools worktree branch mismatch: expected '$expectedBranch', got '$actualBranch'"
}
$actualHead = (Invoke-GitText $worktree @('rev-parse', 'HEAD') 'head validation').ToLowerInvariant()
if ($actualHead -cne $expectedHead) {
    throw "tools worktree head mismatch: expected '$expectedHead', got '$actualHead'"
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
    Join-Path $env:ProgramFiles 'WindowsApps'
    Join-Path (
        [Environment]::GetFolderPath(
            [Environment+SpecialFolder]::LocalApplicationData
        )
    ) 'Microsoft\WindowsApps'
)
$pythonExecutable = Resolve-ToolsPythonExecutable `
    -WindowsAppsRoots $windowsAppsRoots
$systemPowerShell = Join-Path `
    $env:SystemRoot `
    'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not [IO.File]::Exists($systemPowerShell)) {
    throw "System Windows PowerShell is missing: $systemPowerShell"
}
$codexPathPlan = New-CodexSandboxPath `
    -CurrentPath $env:Path `
    -PythonExecutable $pythonExecutable `
    -PowerShellExecutable $systemPowerShell `
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
$codexCommand = Find-ApplicationInPath `
    -PathValue $codexPathPlan.Path `
    -ExecutableNames @('codex.cmd', 'codex.exe')
if ([string]::IsNullOrWhiteSpace($codexCommand)) {
    throw 'Tools process PATH contains no Codex CLI application'
}

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
    codex_shim = $codexShim
    consumer_host = $currentPowerShellHost
    sandbox_shell = $sandboxShell
    python_executable = $pythonExecutable
    windows_apps_path_entries_removed = @(
        $codexPathPlan.RemovedWindowsAppsEntries
    )
    model_override = $null
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
}

# The first tick is intentionally not WakeOnly. It reads the durable handoff
# immediately after reboot without fabricating an operator/lead wake event.
$initialArguments = @{} + $commonConsumerArguments
$initialArguments['DurationMinutes'] = 0
$initialArguments['MaxIterations'] = 1
$initialArguments['PollSeconds'] = 0
try {
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
if ($null -eq $initialResult.exit_code -or [int]$initialResult.exit_code -ne 0) {
    throw "initial tools consumer tick failed with exit_code=$($initialResult.exit_code)"
}

$readinessTemporary = "$readinessPath.$PID.tmp"
$readinessRecord = [ordered]@{
    schema = 'wd.tools-consumer-ready.v1'
    generation = $Generation
    pid = $PID
    process_start_utc = $processStartUtc.ToString('o')
    config_path = $configFull
    worktree = $worktree
    branch = $actualBranch
    head = $actualHead
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

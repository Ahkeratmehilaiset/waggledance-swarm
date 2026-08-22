#requires -Version 5.1
<#
.SYNOPSIS
    Installs the committed WaggleDance reboot bundle on persistent C: storage.

.DESCRIPTION
    The Git repository remains the source of truth.  This installer refuses a
    dirty or unpushed reboot bundle, copies the exact committed files into a
    commit-addressed directory, verifies every copied hash, and only then
    replaces the small machine-local entry-point wrappers.

    Existing machine-local launchers are copied to a timestamped backup
    directory before replacement.  Runtime bridge state is never copied,
    deleted, reset, or migrated by this script.
#>
[CmdletBinding()]
param(
    [string] $MachineRoot = 'C:\Python',
    [string] $BundleStore = 'C:\Python\wd-reboot-bundles',
    [switch] $SkipTaskRegistration,
    [switch] $SkipGrokResolve,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-FullPath {
    param([Parameter(Mandatory)] [string] $Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-WdPathWithoutReparse {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $TrustedRoot,
        [ValidateSet('Any', 'Directory', 'Leaf')]
        [string] $ExpectedType = 'Any',
        [switch] $AllowMissing
    )

    $candidate = Resolve-FullPath $Path
    $root = Resolve-FullPath $TrustedRoot
    $rootPrefix = $root.TrimEnd('\') + '\'
    if (
        -not $candidate.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
        -not $candidate.StartsWith(
            $rootPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "path escaped its trusted root: $candidate"
    }
    $relative = if ($candidate.Equals(
            $root,
            [StringComparison]::OrdinalIgnoreCase
        )) { '' } else { $candidate.Substring($rootPrefix.Length) }
    if (-not (Test-Path -LiteralPath $root)) {
        if ($AllowMissing) { return $candidate }
        throw "required trusted path component is missing: $root"
    }
    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "trusted path component cannot be a reparse point: $root"
    }
    $current = $root
    foreach ($segment in @($relative -split '[\\/]')) {
        if (-not $segment) { continue }
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current)) {
            if ($AllowMissing) { return $candidate }
            throw "required trusted path component is missing: $current"
        }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "trusted path component cannot be a reparse point: $current"
        }
    }
    if (
        $ExpectedType -ceq 'Directory' -and
        -not (Test-Path -LiteralPath $candidate -PathType Container)
    ) {
        throw "trusted path is not a directory: $candidate"
    }
    if (
        $ExpectedType -ceq 'Leaf' -and
        -not (Test-Path -LiteralPath $candidate -PathType Leaf)
    ) {
        throw "trusted path is not a file: $candidate"
    }
    return $candidate
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory)] [string] $Parent,
        [Parameter(Mandatory)] [string] $Child
    )
    $parentFull = (Resolve-FullPath $Parent).TrimEnd('\') + '\'
    $childFull = Resolve-FullPath $Child
    if (-not $childFull.StartsWith(
            $parentFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "unsafe deployment path outside '$parentFull': $childFull"
    }
    return $childFull
}

function Write-Utf8NoBomAtomic {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Content
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $temporary = Join-Path $parent (
        '.{0}.{1}.tmp' -f ([System.IO.Path]::GetFileName($Path)), ([guid]::NewGuid().ToString('N'))
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temporary, $Content, $utf8)
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Read-Utf8DeploymentSnapshot {
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
    return [pscustomobject]@{ Hash = $hash; Text = $text }
}

function Get-RelativeFileHashMap {
    param([Parameter(Mandatory)] [string] $Root)
    $result = [ordered]@{}
    foreach ($file in @(
            Get-ChildItem -LiteralPath $Root -File |
                Where-Object { $_.Name -ne 'deployment-manifest.json' } |
                Sort-Object Name
        )) {
        $result[$file.Name] = (
            Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
        ).Hash.ToUpperInvariant()
    }
    return $result
}

function Invoke-GitCapture {
    param(
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $ArgumentList
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
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = @($output)
        Text = (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
    }
}

function New-ForwardingWrapper {
    param(
        [Parameter(Mandatory)] [string] $Target,
        [Parameter(Mandatory)] [string] $ExpectedHash,
        [Parameter(Mandatory)] [string] $ExpectedManifestHash,
        [Parameter(Mandatory)]
        [ValidateSet('fleet', 'agent', 'tools', 'supervisor')]
        [string] $WrapperKind,
        [string] $FixedAgent = ''
    )

    $parameterBlock = switch ($WrapperKind) {
        'fleet' {
@'
param(
    [string] $ManifestPath = '',
    [string] $RunId = '',
    [ValidateRange(10, 300)]
    [int] $HandshakeTimeoutSeconds = 90,
    [switch] $SkipCliUpdate,
    [switch] $Auto,
    [switch] $Apply,
    [switch] $DryRun
)
'@
        }
        'agent' {
            if ($FixedAgent) {
@'
param(
    [string] $RunId = '',
    [string] $ManifestPath = '',
    [string] $HandshakeDirectory = '',
    [string] $ExpectedManifestHash = '',
    [switch] $DryRun
)
'@
            }
            else {
@'
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9_-]{1,32}$')]
    [string] $Agent,
    [string] $RunId = '',
    [string] $ManifestPath = '',
    [string] $HandshakeDirectory = '',
    [string] $ExpectedManifestHash = '',
    [switch] $DryRun
)
'@
            }
        }
        'tools' {
@'
param(
    [string] $ConfigPath = '',
    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $Generation,
    [switch] $ValidateOnly
)
'@
        }
        'supervisor' {
@'
param(
    [switch] $Apply,
    [string] $ConfigPath = '',
    [string] $LogPath = ''
)
'@
        }
    }

    $targetInvocation = if ($WrapperKind -ceq 'fleet') {
@'
if ($Auto -and ($Apply -or $DryRun)) {
    throw 'Auto cannot be combined with Apply or DryRun'
}
$targetParameters = @{}
foreach ($key in $PSBoundParameters.Keys) {
    if ($key -notin @('Auto', 'Apply', 'DryRun')) {
        $targetParameters[$key] = $PSBoundParameters[$key]
    }
}
function Test-WdWrapperAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator
        )
    }
    finally {
        $identity.Dispose()
    }
}
function ConvertTo-WdSingleQuotedLiteral {
    param([AllowEmptyString()] [string] $Value)
    return "'" + $Value.Replace("'", "''") + "'"
}
if ($Auto -and -not (Test-WdWrapperAdministrator)) {
    $elevationHost = [IO.Path]::Combine(
        [Environment]::SystemDirectory,
        'WindowsPowerShell',
        'v1.0',
        'powershell.exe'
    )
    if (-not (Test-Path -LiteralPath $elevationHost -PathType Leaf)) {
        throw "stable Windows PowerShell elevation host is missing: $elevationHost"
    }
    $commandParts = New-Object 'System.Collections.Generic.List[string]'
    [void]$commandParts.Add('&')
    [void]$commandParts.Add((ConvertTo-WdSingleQuotedLiteral -Value $PSCommandPath))
    [void]$commandParts.Add('-Auto')
    foreach ($name in @('ManifestPath', 'RunId')) {
        if ($targetParameters.ContainsKey($name)) {
            [void]$commandParts.Add("-$name")
            [void]$commandParts.Add((ConvertTo-WdSingleQuotedLiteral -Value ([string]$targetParameters[$name])))
        }
    }
    if ($targetParameters.ContainsKey('HandshakeTimeoutSeconds')) {
        [void]$commandParts.Add('-HandshakeTimeoutSeconds')
        [void]$commandParts.Add(([int]$targetParameters['HandshakeTimeoutSeconds']).ToString(
            [Globalization.CultureInfo]::InvariantCulture
        ))
    }
    if ([bool]$targetParameters['SkipCliUpdate']) {
        [void]$commandParts.Add('-SkipCliUpdate')
    }
    $elevationLogRoot = Join-Path (
        Split-Path -Parent $PSCommandPath
    ) 'wd-reboot-runtime\elevated-auto'
    if (-not (Test-Path -LiteralPath $elevationLogRoot)) {
        [void](New-Item -ItemType Directory -Path $elevationLogRoot -Force)
    }
    $elevationLogRootItem = Get-Item -LiteralPath $elevationLogRoot -Force
    if (
        -not $elevationLogRootItem.PSIsContainer -or
        ($elevationLogRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
        throw "automatic restore log root is not a trusted directory: $elevationLogRoot"
    }
    $elevationLogPath = Join-Path $elevationLogRoot (
        'auto-{0}-{1}-{2}.log' -f
            [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'),
            $PID,
            [Guid]::NewGuid().ToString('N')
    )
    $restoreCommand = $commandParts -join ' '
    $loggedCommand = @(
        '$ErrorActionPreference = ''Stop''',
        '$restoreExitCode = 0',
        '$transcriptStarted = $false',
        'try {',
        ("  Start-Transcript -LiteralPath {0} -Force | Out-Null" -f
            (ConvertTo-WdSingleQuotedLiteral -Value $elevationLogPath)),
        '  $transcriptStarted = $true',
        ("  {0}" -f $restoreCommand),
        '}',
        'catch {',
        '  $restoreExitCode = 1',
        '  [Console]::Error.WriteLine(($_ | Out-String))',
        '}',
        'finally {',
        '  if ($transcriptStarted) { Stop-Transcript | Out-Null }',
        '}',
        'exit $restoreExitCode'
    ) -join [Environment]::NewLine
    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($loggedCommand)
    )
    Write-Host 'Automatic restore needs Administrator rights for Task Scheduler; requesting one UAC elevation...' -ForegroundColor Yellow
    try {
        $elevated = Start-Process `
            -FilePath $elevationHost `
            -Verb RunAs `
            -ArgumentList @(
                '-NoLogo',
                '-NoProfile',
                '-ExecutionPolicy', 'Bypass',
                '-EncodedCommand', $encodedCommand
            ) `
            -Wait `
            -PassThru `
            -ErrorAction Stop
    }
    catch {
        throw "automatic Administrator elevation was declined or failed: $($_.Exception.Message)"
    }
    if ([int]$elevated.ExitCode -ne 0) {
        Write-Host "Elevated restore failure log: $elevationLogPath" -ForegroundColor Yellow
        if (Test-Path -LiteralPath $elevationLogPath -PathType Leaf) {
            Get-Content -LiteralPath $elevationLogPath -Tail 120 |
                ForEach-Object { Write-Host ([string]$_) }
        }
        throw (
            "elevated automatic restore failed with exit code {0}; log={1}" -f
                [int]$elevated.ExitCode,
                $elevationLogPath
        )
    }
    Write-Host "Elevated restore log: $elevationLogPath"
    return
}
if ($Auto) {
    Write-Host 'Running byte-inert fleet preflight before automatic restore...'
    $dryRunParameters = @{} + $targetParameters
    $dryRunParameters['DryRun'] = $true
    & $target @dryRunParameters
    Write-Host 'Preflight passed; applying the verified fleet restore...'
    $applyParameters = @{} + $targetParameters
    $applyParameters['Apply'] = $true
    & $target @applyParameters
}
else {
    if ($Apply) { $targetParameters['Apply'] = $true }
    if ($DryRun) { $targetParameters['DryRun'] = $true }
    & $target @targetParameters
}
'@
    } elseif ($FixedAgent) {
        $escapedAgent = $FixedAgent.Replace("'", "''")
@"
`$targetParameters = @{}
foreach (`$key in `$PSBoundParameters.Keys) {
    `$targetParameters[`$key] = `$PSBoundParameters[`$key]
}
`$targetParameters['Agent'] = '$escapedAgent'
& `$target @targetParameters
"@
    } else {
        '& $target @PSBoundParameters'
    }
    $escapedTarget = $Target.Replace("'", "''")

    return @"
#requires -Version 5.1
# Generated by Deploy-WdRebootBundle.ps1 from a committed Git bundle.
[CmdletBinding()]
$parameterBlock
`$ErrorActionPreference = 'Stop'
function Set-WdWrapperWindowsPowerShellModulePath {
    if (`$PSVersionTable.PSEdition -cne 'Desktop') { return }
    `$roots = [Collections.Generic.List[string]]::new()
    `$documents = [Environment]::GetFolderPath('MyDocuments')
    if (-not [string]::IsNullOrWhiteSpace(`$documents)) {
        [void]`$roots.Add([IO.Path]::Combine(`$documents, 'WindowsPowerShell', 'Modules'))
    }
    `$programFiles = [string]`$env:ProgramFiles
    if (-not [string]::IsNullOrWhiteSpace(`$programFiles)) {
        [void]`$roots.Add([IO.Path]::Combine(`$programFiles, 'WindowsPowerShell', 'Modules'))
    }
    [void]`$roots.Add([IO.Path]::Combine(`$PSHOME, 'Modules'))
    `$env:PSModulePath = @(`$roots) -join [IO.Path]::PathSeparator
}
Set-WdWrapperWindowsPowerShellModulePath
`$target = '$escapedTarget'
`$manifestPath = Join-Path (Split-Path -Parent `$target) 'deployment-manifest.json'
if (-not (Test-Path -LiteralPath `$target -PathType Leaf)) {
    throw "WD reboot bundle entry point is missing: `$target"
}
if (
    -not (Test-Path -LiteralPath `$manifestPath -PathType Leaf) -or
    (Get-FileHash -LiteralPath `$manifestPath -Algorithm SHA256).Hash -cne
        '$ExpectedManifestHash'
) {
    throw "WD reboot deployment manifest integrity mismatch for `$target"
}
`$actualHash = (Get-FileHash -LiteralPath `$target -Algorithm SHA256).Hash
if (`$actualHash -cne '$ExpectedHash') {
    throw "WD reboot bundle integrity mismatch for `$target"
}
`$env:WD_REBOOT_EXPECTED_MANIFEST_HASH = '$ExpectedManifestHash'
$targetInvocation
"@
}

$sourceRoot = Resolve-FullPath $PSScriptRoot
$machineFull = Resolve-FullPath $MachineRoot
$storeFull = Resolve-FullPath $BundleStore
foreach ($persistentPath in @($sourceRoot, $machineFull, $storeFull)) {
    if ([System.IO.Path]::GetPathRoot($persistentPath).TrimEnd('\') -ne 'C:') {
        throw "reboot assets must stay on persistent C: storage: $persistentPath"
    }
}
$persistentDriveRoot = [IO.Path]::GetPathRoot($machineFull)
[void](Assert-WdPathWithoutReparse `
    -Path $sourceRoot `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory)
[void](Assert-WdPathWithoutReparse `
    -Path $machineFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory `
    -AllowMissing)
[void](Assert-WdPathWithoutReparse `
    -Path $storeFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory `
    -AllowMissing)

$gitRootProbe = Invoke-GitCapture `
    -Worktree $sourceRoot `
    -ArgumentList @('rev-parse', '--show-toplevel')
$gitRoot = [string]$gitRootProbe.Text
if ($gitRootProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($gitRoot)) {
    throw "reboot bundle is not inside the source-of-truth Git repository: $sourceRoot"
}
$gitRoot = Resolve-FullPath ([string]$gitRoot)
$commonGitProbe = Invoke-GitCapture `
    -Worktree $gitRoot `
    -ArgumentList @('rev-parse', '--path-format=absolute', '--git-common-dir')
$commonGit = [string]$commonGitProbe.Text
if ($commonGitProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($commonGit)) {
    throw "could not resolve Git common directory for reboot source: $gitRoot"
}
$commonGit = Resolve-FullPath $commonGit
$expectedCommonGit = Resolve-FullPath 'C:\Python\project2\.git'
if (-not $commonGit.Equals(
        $expectedCommonGit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "reboot source is not a worktree of C:\Python\project2: $gitRoot"
}

$statusProbe = Invoke-GitCapture `
    -Worktree $gitRoot `
    -ArgumentList @(
        'status',
        '--porcelain',
        '--',
        'ops/windows/reboot',
        '.agent-bridge/bin',
        'configs/bridge_identity_registry.json'
    )
if ($statusProbe.ExitCode -ne 0) {
    throw 'git status failed for reboot sources'
}
$relativeSource = @($statusProbe.Output)
if (@($relativeSource).Count -gt 0) {
    throw 'refusing to deploy uncommitted reboot or Tools bootstrap sources'
}

$headProbe = Invoke-GitCapture -Worktree $gitRoot -ArgumentList @('rev-parse', 'HEAD')
$head = [string]$headProbe.Text
if ($headProbe.ExitCode -ne 0 -or $head -cnotmatch '^[0-9a-f]{40}$') {
    throw 'could not resolve the exact reboot-bundle commit'
}
$branchProbe = Invoke-GitCapture `
    -Worktree $gitRoot `
    -ArgumentList @('branch', '--show-current')
if ($branchProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($branchProbe.Text)) {
    throw 'could not resolve the reboot-bundle branch'
}
$branch = [string]$branchProbe.Text
$upstreamProbe = Invoke-GitCapture `
    -Worktree $gitRoot `
    -ArgumentList @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
$upstream = [string]$upstreamProbe.Text
if ($upstreamProbe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($upstream)) {
    throw 'reboot-bundle branch has no upstream; push it before deployment'
}
$upstreamHeadProbe = Invoke-GitCapture `
    -Worktree $gitRoot `
    -ArgumentList @('rev-parse', '@{u}')
$upstreamHead = [string]$upstreamHeadProbe.Text
if ($upstreamHeadProbe.ExitCode -ne 0 -or $upstreamHead -cne $head) {
    throw "reboot-bundle commit is not pushed to its upstream ($upstream)"
}

$temporaryParent = Resolve-FullPath ([IO.Path]::GetTempPath())
$materializationRoot = Assert-ChildPath `
    -Parent $temporaryParent `
    -Child (Join-Path $temporaryParent (
        'wd-reboot-head-' + [guid]::NewGuid().ToString('N')
    ))
[void](New-Item -ItemType Directory -Path $materializationRoot -ErrorAction Stop)
try {
$archivePath = Join-Path $materializationRoot 'head.zip'
$archiveProbe = Invoke-GitCapture `
    -Worktree $gitRoot `
    -ArgumentList @(
        'archive',
        '--format=zip',
        "--output=$archivePath",
        $head,
        '--',
        'ops/windows/reboot',
        '.agent-bridge/bin',
        'configs/bridge_identity_registry.json'
    )
if (
    $archiveProbe.ExitCode -ne 0 -or
    -not (Test-Path -LiteralPath $archivePath -PathType Leaf)
) {
    throw "could not materialize exact reboot inputs from commit $head"
}
$archiveRoot = Join-Path $materializationRoot 'tree'
Expand-Archive `
    -LiteralPath $archivePath `
    -DestinationPath $archiveRoot `
    -Force

$materializedRebootRoot = Join-Path $archiveRoot 'ops\windows\reboot'
$toolsBootstrapSource = Join-Path $archiveRoot '.agent-bridge\bin'
$identityRegistrySource = Join-Path (
    Join-Path $archiveRoot 'configs'
) 'bridge_identity_registry.json'
foreach ($materializedPath in @(
        $materializedRebootRoot,
        $toolsBootstrapSource
    )) {
    if (-not (Test-Path -LiteralPath $materializedPath -PathType Container)) {
        throw "commit archive is missing required directory: $materializedPath"
    }
}
if (-not (Test-Path -LiteralPath $identityRegistrySource -PathType Leaf)) {
    throw "commit archive is missing bridge identity registry: $identityRegistrySource"
}

$sourceHashes = Get-RelativeFileHashMap -Root $materializedRebootRoot
$sourcePaths = @{}
foreach ($name in $sourceHashes.Keys) {
    $sourcePaths[[string]$name] = Join-Path $materializedRebootRoot ([string]$name)
}
foreach ($file in @(
        Get-ChildItem -LiteralPath $toolsBootstrapSource -File |
            Sort-Object Name
    )) {
    $relativeName = 'tools-bootstrap/.agent-bridge/bin/{0}' -f $file.Name
    $sourceHashes[$relativeName] = (
        Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
    ).Hash.ToUpperInvariant()
    $sourcePaths[$relativeName] = $file.FullName
}
$identityRegistryRelative = 'tools-bootstrap/configs/bridge_identity_registry.json'
$sourceHashes[$identityRegistryRelative] = (
    Get-FileHash -LiteralPath $identityRegistrySource -Algorithm SHA256
).Hash.ToUpperInvariant()
$sourcePaths[$identityRegistryRelative] = $identityRegistrySource
foreach ($required in @(
        'start-wd-all.ps1',
        'start-wd-agent.ps1',
        'Get-WdSwarmParallelStatus.ps1',
        'Write-WdLaneCurrentState.ps1',
        'Watch-CodexPrompts.ps1',
        'start-wd-tools-consumer.ps1',
        'Invoke-WdToolsCodex.ps1',
        'wd-fleet.json',
        'wd_supervisor.ps1',
        'wd_supervisor_loop.json',
        'Resolve-WdGrokModel.ps1',
        'Register-WdScheduledTasks.ps1',
        'Set-WdTaskConsoleContainment.ps1',
        'BOOT_AFTER_REBOOT.md',
        'WD_LOCAL_GPU_GUIDE.md',
        'WD_SWARM_PARALLEL_POLICY_V1.md',
        'WD_SWARM_TARGET_STATE_V1.md',
        'WaggleDanceSwarmAi.png',
        'tools-bootstrap/.agent-bridge/bin/AgentBridgeSessionIdentity.ps1',
        'tools-bootstrap/.agent-bridge/bin/BridgeIncrementalReader.ps1',
        'tools-bootstrap/.agent-bridge/bin/BridgeLogReader.ps1',
        'tools-bootstrap/.agent-bridge/bin/Drain-AcceptedBridgeQueue.ps1',
        'tools-bootstrap/.agent-bridge/bin/Restore-BridgeSpool.ps1',
        'tools-bootstrap/.agent-bridge/bin/Send-Liveness.ps1',
        'tools-bootstrap/.agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1',
        'tools-bootstrap/.agent-bridge/bin/Start-AgentBridgeSession.ps1',
        'tools-bootstrap/.agent-bridge/bin/Start-BridgeHeartbeat.ps1',
        'tools-bootstrap/.agent-bridge/bin/Test-BridgeWake.ps1',
        'tools-bootstrap/.agent-bridge/bin/Watch-Bridge.ps1',
        'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1',
        'tools-bootstrap/configs/bridge_identity_registry.json'
    )) {
    if (-not $sourceHashes.Contains($required)) {
        throw "required reboot bundle file is missing: $required"
    }
}

$targetRoot = Assert-ChildPath -Parent $storeFull -Child (Join-Path $storeFull $head)
$manifestObject = [ordered]@{
    schema_version = 1
    source_commit = $head
    source_branch = $branch
    source_upstream = $upstream
    installed_at_utc = [DateTime]::UtcNow.ToString('o')
    files = $sourceHashes
}

Write-Host "WD reboot bundle source: $branch@$head"
Write-Host "Install target: $targetRoot"

Write-Host 'Running mutation-free deployment preflight...'
if (-not $SkipGrokResolve) {
    & (Join-Path $materializedRebootRoot 'Resolve-WdGrokModel.ps1') `
        -DryRun `
        -OutputDirectory $machineFull |
        Out-Host
}
if (-not $SkipTaskRegistration) {
    & (Join-Path $materializedRebootRoot 'Register-WdScheduledTasks.ps1') `
        -SupervisorScript (Join-Path $materializedRebootRoot 'wd_supervisor.ps1')
}

if ($DryRun) {
    Write-Host 'DRY RUN: no files, tasks, caches, or processes will be changed.'
    foreach ($name in $sourceHashes.Keys) {
        Write-Host ("  {0}  {1}" -f $sourceHashes[$name], $name)
    }
    return
}

if (-not (Test-Path -LiteralPath $storeFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $storeFull -Force)
}
[void](Assert-WdPathWithoutReparse `
    -Path $storeFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory)
if (Test-Path -LiteralPath $targetRoot) {
    [void](Assert-WdPathWithoutReparse `
        -Path $targetRoot `
        -TrustedRoot $storeFull `
        -ExpectedType Directory)
    $existingManifest = Join-Path $targetRoot 'deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $existingManifest -PathType Leaf)) {
        throw "existing commit directory is incomplete: $targetRoot"
    }
} else {
    $stage = Assert-ChildPath -Parent $storeFull -Child (
        Join-Path $storeFull ('.stage-' + [guid]::NewGuid().ToString('N'))
    )
    [void](New-Item -ItemType Directory -Path $stage)
    [void](Assert-WdPathWithoutReparse `
        -Path $stage `
        -TrustedRoot $storeFull `
        -ExpectedType Directory)
    try {
        foreach ($name in $sourceHashes.Keys) {
            $destination = Join-Path $stage $name
            $destinationParent = Split-Path -Parent $destination
            if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
                [void](New-Item -ItemType Directory -Path $destinationParent -Force)
            }
            Copy-Item -LiteralPath $sourcePaths[[string]$name] -Destination $destination
            [void](Assert-WdPathWithoutReparse `
                -Path $destination `
                -TrustedRoot $stage `
                -ExpectedType Leaf)
            $copiedHash = (
                Get-FileHash -LiteralPath $destination -Algorithm SHA256
            ).Hash.ToUpperInvariant()
            if ($copiedHash -cne $sourceHashes[$name]) {
                throw "copied reboot bundle hash mismatch: $name"
            }
        }
        Write-Utf8NoBomAtomic `
            -Path (Join-Path $stage 'deployment-manifest.json') `
            -Content (($manifestObject | ConvertTo-Json -Depth 8) + [Environment]::NewLine)
        Move-Item -LiteralPath $stage -Destination $targetRoot
        [void](Assert-WdPathWithoutReparse `
            -Path $targetRoot `
            -TrustedRoot $storeFull `
            -ExpectedType Directory)
    } catch {
        if (Test-Path -LiteralPath $stage) {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
        throw
    }
}

$installedManifestPath = Join-Path $targetRoot 'deployment-manifest.json'
[void](Assert-WdPathWithoutReparse `
    -Path $installedManifestPath `
    -TrustedRoot $targetRoot `
    -ExpectedType Leaf)
$installedManifestSnapshot = Read-Utf8DeploymentSnapshot `
    -Path $installedManifestPath
$installedManifest = [string]$installedManifestSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
$installedManifestHash = ([string]$installedManifestSnapshot.Hash).ToUpperInvariant()
if (
    [int]$installedManifest.schema_version -ne 1 -or
    [string]$installedManifest.source_commit -cne $head
) {
    throw "installed reboot bundle manifest does not match commit $head"
}
$installedHashProperties = @($installedManifest.files.PSObject.Properties)
if ($installedHashProperties.Count -ne $sourceHashes.Count) {
    throw "installed reboot bundle file set does not match commit $head"
}
foreach ($name in $sourceHashes.Keys) {
    $installedProperty = $installedManifest.files.PSObject.Properties[$name]
    if (
        $null -eq $installedProperty -or
        [string]$installedProperty.Value -cne [string]$sourceHashes[$name]
    ) {
        throw "installed reboot bundle manifest hash differs from source commit: $name"
    }
}
foreach ($property in $installedManifest.files.PSObject.Properties) {
    $installedFile = Join-Path $targetRoot $property.Name
    [void](Assert-WdPathWithoutReparse `
        -Path $installedFile `
        -TrustedRoot $targetRoot `
        -ExpectedType Leaf)
    $actual = (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash
    if ($actual -cne [string]$property.Value) {
        throw "installed reboot bundle integrity mismatch: $($property.Name)"
    }
}
$expectedInstalledSet = @{ 'deployment-manifest.json' = $true }
foreach ($property in $installedManifest.files.PSObject.Properties) {
    $expectedInstalledSet[
        ([string]$property.Name).Replace('\', '/').ToLowerInvariant()
    ] = $true
}
$targetPrefix = (Resolve-FullPath $targetRoot).TrimEnd('\') + '\'
$actualInstalledFiles = @(
    Get-ChildItem -LiteralPath $targetRoot -Recurse -File -ErrorAction Stop
)
if ($actualInstalledFiles.Count -ne $expectedInstalledSet.Count) {
    throw "installed reboot bundle contains an unexpected recursive file set"
}
foreach ($file in $actualInstalledFiles) {
    $fullName = Resolve-FullPath $file.FullName
    if (-not $fullName.StartsWith(
            $targetPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw "installed reboot bundle enumeration escaped target root: $fullName"
    }
    $relativeName = $fullName.Substring($targetPrefix.Length).Replace('\', '/')
    if (-not $expectedInstalledSet.ContainsKey($relativeName.ToLowerInvariant())) {
        throw "installed reboot bundle contains unexpected file: $relativeName"
    }
}

$wrapperSpecs = @(
    [pscustomobject]@{ Name = 'start-wd-all.ps1'; Target = 'start-wd-all.ps1'; Kind = 'fleet'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-agent.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-codex-lead.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'codex-lead-1' },
    [pscustomobject]@{ Name = 'start-wd-codex-tools.ps1'; Target = 'start-wd-tools-consumer.ps1'; Kind = 'tools'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-claude-rco.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'claude-rco-1' },
    [pscustomobject]@{ Name = 'start-wd-claude-rco2.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'claude-rco-2' },
    [pscustomobject]@{ Name = 'start-wd-fable-5.ps1'; Target = 'start-wd-agent.ps1'; Kind = 'agent'; Agent = 'fable-5' },
    [pscustomobject]@{ Name = 'wd_supervisor.ps1'; Target = 'wd_supervisor.ps1'; Kind = 'supervisor'; Agent = '' },
    [pscustomobject]@{ Name = 'start-wd-tools-consumer.ps1'; Target = 'start-wd-tools-consumer.ps1'; Kind = 'tools'; Agent = '' }
)
$dataSpecs = @(
    [pscustomobject]@{ Name = 'wd_supervisor_loop.json'; Target = 'wd_supervisor_loop.json' },
    # Replace the legacy opaque-command snapshot too, so no machine-local
    # reboot artifact retains a dead, versioned WindowsApps executable.
    [pscustomobject]@{ Name = 'wd_supervisor_loop_snapshot.json'; Target = 'wd_supervisor_loop.json' },
    [pscustomobject]@{ Name = 'BOOT_AFTER_REBOOT.md'; Target = 'BOOT_AFTER_REBOOT.md' },
    [pscustomobject]@{ Name = 'Set-WdTaskConsoleContainment.ps1'; Target = 'Set-WdTaskConsoleContainment.ps1' },
    [pscustomobject]@{ Name = 'WD_LOCAL_GPU_GUIDE.md'; Target = 'WD_LOCAL_GPU_GUIDE.md' },
    [pscustomobject]@{ Name = 'WD_SWARM_PARALLEL_POLICY_V1.md'; Target = 'WD_SWARM_PARALLEL_POLICY_V1.md' },
    [pscustomobject]@{ Name = 'Write-WdLaneCurrentState.ps1'; Target = 'Write-WdLaneCurrentState.ps1' },
    [pscustomobject]@{ Name = 'Get-WdSwarmParallelStatus.ps1'; Target = 'Get-WdSwarmParallelStatus.ps1' }
)

if (-not (Test-Path -LiteralPath $machineFull -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $machineFull -Force)
}
[void](Assert-WdPathWithoutReparse `
    -Path $machineFull `
    -TrustedRoot $persistentDriveRoot `
    -ExpectedType Directory)

$backupRoot = Join-Path $machineFull (
    'wd-reboot-backups\{0}-{1}-{2}' -f
        [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'),
        $PID,
        [guid]::NewGuid().ToString('N').Substring(0, 8)
)
$transactionNames = @(
    @($wrapperSpecs | ForEach-Object { [string]$_.Name })
    @($dataSpecs | ForEach-Object { [string]$_.Name })
    @(
        'WD_REBOOT_STATE_CURRENT.json',
        'WD_REBOOT_STATE_CURRENT.md',
        'WD_REBOOT_INTEGRITY_CURRENT.sha256',
        'WD_REBOOT_INTEGRITY_CURRENT.json',
        'WD_GROK_MODEL_CURRENT.json',
        'WD_GROK_MODEL_CURRENT.md'
    )
) | Select-Object -Unique
$backedUp = $false
$originallyPresent = @{}
foreach ($name in $transactionNames) {
    $machinePath = Assert-ChildPath -Parent $machineFull -Child (
        Join-Path $machineFull $name
    )
    $present = Test-Path -LiteralPath $machinePath -PathType Leaf
    $originallyPresent[$name] = $present
    if ($present) {
        if (-not $backedUp) {
            [void](New-Item -ItemType Directory -Path $backupRoot -Force)
            $backedUp = $true
        }
        Copy-Item -LiteralPath $machinePath -Destination (Join-Path $backupRoot $name)
    }
}

$machineMutationStarted = $false
try {
$machineMutationStarted = $true
foreach ($spec in $wrapperSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $target = Join-Path $targetRoot $spec.Target
    $hash = [string]$installedManifest.files.($spec.Target)
    $wrapper = New-ForwardingWrapper `
        -Target $target `
        -ExpectedHash $hash `
        -ExpectedManifestHash $installedManifestHash `
        -WrapperKind $spec.Kind `
        -FixedAgent $spec.Agent
    Write-Utf8NoBomAtomic -Path $machinePath -Content $wrapper
}

foreach ($spec in $dataSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $content = Get-Content -LiteralPath (Join-Path $targetRoot $spec.Target) -Raw
    Write-Utf8NoBomAtomic -Path $machinePath -Content $content
}

$state = [ordered]@{
    schema_version = 1
    source_commit = $head
    source_branch = $branch
    active_bundle = $targetRoot
    fleet_manifest = (Join-Path $targetRoot 'wd-fleet.json')
    installed_at_utc = [DateTime]::UtcNow.ToString('o')
    precedence = @(
        'live bridge state',
        'valid compact per-lane checkpoint',
        'WD_REBOOT_STATE_CURRENT',
        'newer per-agent and fleet Markdown handoffs as fallback',
        'dated historical reboot snapshots'
    )
}
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_STATE_CURRENT.json') `
    -Content (($state | ConvertTo-Json -Depth 6) + [Environment]::NewLine)
$stateMarkdown = @"
# WaggleDance reboot state — current pointer

Generated from pushed Git commit ``$head`` on branch ``$branch``.

Active bundle: ``$targetRoot``

Precedence after restart:

1. Live bridge state read without acknowledging stale events.
2. A valid compact per-lane checkpoint.
3. This current bundle pointer, fleet roles, lane prompt, and parallel policy.
4. Newer fleet and per-agent Markdown handoffs as fallback.
5. Dated reboot snapshots as historical evidence only.

The reboot path grants no merge, deploy, signature, canary, runtime-authority,
or ``claim_safe`` permission.  The merge-driver StandingOneShot remains in
deliberate HOLD.
"@
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_STATE_CURRENT.md') `
    -Content ($stateMarkdown + [Environment]::NewLine)

$integrityLines = New-Object System.Collections.Generic.List[string]
foreach ($spec in $wrapperSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $integrityLines.Add((
        '{0} *{1}' -f (
            Get-FileHash -LiteralPath $machinePath -Algorithm SHA256
        ).Hash.ToUpperInvariant(),
        $machinePath
    ))
}
foreach ($spec in $dataSpecs) {
    $machinePath = Join-Path $machineFull $spec.Name
    $integrityLines.Add((
        '{0} *{1}' -f (
            Get-FileHash -LiteralPath $machinePath -Algorithm SHA256
        ).Hash.ToUpperInvariant(),
        $machinePath
    ))
}
foreach ($property in $installedManifest.files.PSObject.Properties) {
    $integrityLines.Add((
        '{0} *{1}' -f [string]$property.Value, (Join-Path $targetRoot $property.Name)
    ))
}
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_INTEGRITY_CURRENT.sha256') `
    -Content (($integrityLines -join [Environment]::NewLine) + [Environment]::NewLine)
Write-Utf8NoBomAtomic `
    -Path (Join-Path $machineFull 'WD_REBOOT_INTEGRITY_CURRENT.json') `
    -Content (Get-Content -LiteralPath $installedManifestPath -Raw)

if (-not $SkipGrokResolve) {
    & (Join-Path $targetRoot 'Resolve-WdGrokModel.ps1') -OutputDirectory $machineFull | Out-Host
}
if (-not $SkipTaskRegistration) {
    & (Join-Path $targetRoot 'Register-WdScheduledTasks.ps1') `
        -Apply `
        -SupervisorScript (Join-Path $machineFull 'wd_supervisor.ps1')
}
}
catch {
    $deploymentFailure = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]
    if ($machineMutationStarted) {
        foreach ($name in $transactionNames) {
            $machinePath = Join-Path $machineFull $name
            try {
                if ([bool]$originallyPresent[$name]) {
                    Copy-Item `
                        -LiteralPath (Join-Path $backupRoot $name) `
                        -Destination $machinePath `
                        -Force
                }
                elseif (Test-Path -LiteralPath $machinePath -PathType Leaf) {
                    Remove-Item -LiteralPath $machinePath -Force
                }
            }
            catch {
                $rollbackErrors.Add("${name}: $($_.Exception.Message)")
            }
        }
    }
    if ($rollbackErrors.Count -gt 0) {
        throw (
            "deployment failed: {0}; rollback also failed: {1}" -f
                $deploymentFailure.Exception.Message,
                ($rollbackErrors -join '; ')
        )
    }
    throw $deploymentFailure
}

Write-Host ''
Write-Host 'WD reboot bundle installed and verified.' -ForegroundColor Green
Write-Host 'One-line restore:'
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1 -Auto'
if ($backedUp) {
    Write-Host "Previous machine-local launchers were backed up to: $backupRoot"
}
}
finally {
    if (Test-Path -LiteralPath $materializationRoot -PathType Container) {
        $verifiedTemporary = Assert-ChildPath `
            -Parent $temporaryParent `
            -Child $materializationRoot
        Remove-Item `
            -LiteralPath $verifiedTemporary `
            -Recurse `
            -Force `
            -ErrorAction SilentlyContinue
    }
}

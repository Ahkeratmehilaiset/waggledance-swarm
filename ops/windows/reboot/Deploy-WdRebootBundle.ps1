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
    [switch] $DryRun
)
'@
            }
        }
        'tools' {
@'
param(
    [string] $ConfigPath = '',
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

    $targetInvocation = if ($FixedAgent) {
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
`$target = '$escapedTarget'
if (-not (Test-Path -LiteralPath `$target -PathType Leaf)) {
    throw "WD reboot bundle entry point is missing: `$target"
}
`$actualHash = (Get-FileHash -LiteralPath `$target -Algorithm SHA256).Hash
if (`$actualHash -cne '$ExpectedHash') {
    throw "WD reboot bundle integrity mismatch for `$target"
}
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
    -ArgumentList @('status', '--porcelain', '--', 'ops/windows/reboot')
if ($statusProbe.ExitCode -ne 0) {
    throw 'git status failed for ops/windows/reboot'
}
$relativeSource = @($statusProbe.Output)
if (@($relativeSource).Count -gt 0) {
    throw 'refusing to deploy an uncommitted reboot bundle'
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

$sourceHashes = Get-RelativeFileHashMap -Root $sourceRoot
foreach ($required in @(
        'start-wd-all.ps1',
        'start-wd-agent.ps1',
        'start-wd-tools-consumer.ps1',
        'wd-fleet.json',
        'wd_supervisor.ps1',
        'wd_supervisor_loop.json',
        'Resolve-WdGrokModel.ps1',
        'Register-WdScheduledTasks.ps1',
        'BOOT_AFTER_REBOOT.md'
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
    & (Join-Path $sourceRoot 'Resolve-WdGrokModel.ps1') `
        -DryRun `
        -OutputDirectory $machineFull |
        Out-Host
}
if (-not $SkipTaskRegistration) {
    & (Join-Path $sourceRoot 'Register-WdScheduledTasks.ps1') `
        -SupervisorScript (Join-Path $sourceRoot 'wd_supervisor.ps1')
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
if (Test-Path -LiteralPath $targetRoot) {
    $existingManifest = Join-Path $targetRoot 'deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $existingManifest -PathType Leaf)) {
        throw "existing commit directory is incomplete: $targetRoot"
    }
} else {
    $stage = Assert-ChildPath -Parent $storeFull -Child (
        Join-Path $storeFull ('.stage-' + [guid]::NewGuid().ToString('N'))
    )
    [void](New-Item -ItemType Directory -Path $stage)
    try {
        foreach ($name in $sourceHashes.Keys) {
            Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $stage $name)
            $copiedHash = (
                Get-FileHash -LiteralPath (Join-Path $stage $name) -Algorithm SHA256
            ).Hash.ToUpperInvariant()
            if ($copiedHash -cne $sourceHashes[$name]) {
                throw "copied reboot bundle hash mismatch: $name"
            }
        }
        Write-Utf8NoBomAtomic `
            -Path (Join-Path $stage 'deployment-manifest.json') `
            -Content (($manifestObject | ConvertTo-Json -Depth 8) + [Environment]::NewLine)
        Move-Item -LiteralPath $stage -Destination $targetRoot
    } catch {
        if (Test-Path -LiteralPath $stage) {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
        throw
    }
}

$installedManifestPath = Join-Path $targetRoot 'deployment-manifest.json'
$installedManifest = Get-Content -LiteralPath $installedManifestPath -Raw | ConvertFrom-Json
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
    $actual = (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash
    if ($actual -cne [string]$property.Value) {
        throw "installed reboot bundle integrity mismatch: $($property.Name)"
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
    [pscustomobject]@{ Name = 'BOOT_AFTER_REBOOT.md'; Target = 'BOOT_AFTER_REBOOT.md' }
)

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
        'newer per-agent and fleet handoffs',
        'WD_REBOOT_STATE_CURRENT',
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
2. Newer fleet and per-agent handoffs.
3. This current bundle pointer.
4. Dated reboot snapshots as historical evidence only.

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
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-all.ps1'
if ($backedUp) {
    Write-Host "Previous machine-local launchers were backed up to: $backupRoot"
}

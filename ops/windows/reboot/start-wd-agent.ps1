#requires -Version 5.1
<#
.SYNOPSIS
  Starts one integrity-pinned WaggleDance interactive bridge lane.

.DESCRIPTION
  This script never creates, fetches, checks out, resets, or advances a Git
  worktree. It verifies canonical repository membership and either the exact
  recorded branch/HEAD or an explicit current_worktree resume policy, then
  dot-sources the commit-anchored bridge session starter from the deployed
  reboot bundle. The local handshake proves that bridge bootstrap and the
  target-state event completed before the CLI was invoked; it does not claim
  that the model completed any work.
#>
[CmdletBinding()]
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

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:WdGitExecutable = ''
if (-not $ManifestPath) {
  $ManifestPath = Join-Path $PSScriptRoot 'wd-fleet.json'
}
$script:LaneManifestAnchor = if ($ExpectedManifestHash) {
  $ExpectedManifestHash
} else {
  [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
}

function Resolve-NormalizedPath {
  param([Parameter(Mandatory)] [string] $Path)
  return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Assert-LanePathWithoutReparse {
  param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [string] $TrustedRoot,
    [ValidateSet('Directory', 'Leaf')] [string] $ExpectedType
  )
  $candidate = [IO.Path]::GetFullPath($Path).TrimEnd('\')
  $rootCandidate = [IO.Path]::GetFullPath($TrustedRoot)
  $root = if ($rootCandidate.Equals(
      [IO.Path]::GetPathRoot($rootCandidate),
      [StringComparison]::OrdinalIgnoreCase
    )) { $rootCandidate } else { $rootCandidate.TrimEnd('\') }
  if (
    -not $candidate.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
    -not $candidate.StartsWith(
      $root.TrimEnd('\') + '\',
      [StringComparison]::OrdinalIgnoreCase
    )
  ) {
    throw "lane trusted path escaped its root: $candidate"
  }
  if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "lane trusted path root is missing: $root"
  }
  $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "lane trusted path root is a reparse point: $root"
  }
  $relative = $candidate.Substring($root.Length).TrimStart('\')
  $current = $root
  foreach ($segment in @($relative -split '\\')) {
    if (-not $segment) { continue }
    $current = Join-Path $current $segment
    if (-not (Test-Path -LiteralPath $current)) {
      throw "lane trusted path component is missing: $current"
    }
    $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "lane trusted path component is a reparse point: $current"
    }
  }
  $pathType = if ($ExpectedType -ceq 'Directory') { 'Container' } else { 'Leaf' }
  if (-not (Test-Path -LiteralPath $candidate -PathType $pathType)) {
    throw "lane trusted path has the wrong type: $candidate"
  }
  return $candidate
}

function Read-Utf8LaneSnapshot {
  param([Parameter(Mandatory)] [string] $Path)

  $bytes = [IO.File]::ReadAllBytes($Path)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $hash = [BitConverter]::ToString(
      $sha.ComputeHash($bytes)
    ).Replace('-', '')
  } finally {
    $sha.Dispose()
  }
  $text = [Text.Encoding]::UTF8.GetString($bytes)
  if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
    $text = $text.Substring(1)
  }
  return [pscustomobject]@{ Hash = $hash; Text = $text }
}

function Read-NonEmptyFile {
  param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [string] $Label
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label is missing: $Path"
  }
  $text = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
  if ([string]::IsNullOrWhiteSpace($text)) {
    throw "$Label is empty: $Path"
  }
  return $text
}

function Resolve-WdLaneGitApplication {
  param([Parameter(Mandatory)] [string] $ConfiguredPath)

  if (-not [IO.Path]::IsPathRooted($ConfiguredPath)) {
    throw 'lane Git executable path must be absolute'
  }
  $candidate = [IO.Path]::GetFullPath($ConfiguredPath)
  if ([IO.Path]::GetExtension($candidate) -cne '.exe') {
    throw 'lane Git executable must be an .exe application'
  }
  $command = Get-Command `
    -Name $candidate `
    -CommandType Application `
    -ErrorAction Stop
  if (-not ([IO.Path]::GetFullPath([string]$command.Source)).Equals(
      $candidate,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw 'lane Git command is not the configured application'
  }
  [void](Assert-LanePathWithoutReparse `
      -Path $candidate `
      -TrustedRoot ([IO.Path]::GetPathRoot($candidate)) `
      -ExpectedType Leaf)
  return $candidate
}

function Invoke-CheckedGit {
  param(
    [Parameter(Mandatory)] [string] $Worktree,
    [Parameter(Mandatory)] [string[]] $Arguments,
    [string] $GitExecutable = [string]$script:WdGitExecutable
  )
  $gitPath = Resolve-WdLaneGitApplication -ConfiguredPath $GitExecutable
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
      Remove-Item -LiteralPath "Env:$([string]$entry.Name)" -ErrorAction Stop
    }
    $env:GIT_CONFIG_NOSYSTEM = '1'
    $env:GIT_CONFIG_GLOBAL = 'NUL'
    $env:GIT_OPTIONAL_LOCKS = '0'
    $env:GIT_TERMINAL_PROMPT = '0'
    $ErrorActionPreference = 'Continue'
    $output = @(
      & $gitPath --no-replace-objects -C $Worktree @Arguments 2>&1
    )
    $exitCode = $LASTEXITCODE
  } finally {
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
    throw "trusted git -C '$Worktree' $($Arguments -join ' ') failed ($exitCode): $($output -join ' ')"
  }
  return (($output -join "`n").Trim())
}

function Resolve-WdLaneCliApplication {
  param(
    [Parameter(Mandatory)]
    [ValidateSet('codex.cmd', 'claude.cmd')]
    [string] $Name
  )

  $roamingRoot = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ApplicationData
  )
  if ([string]::IsNullOrWhiteSpace($roamingRoot)) {
    throw 'lane roaming application-data root is unavailable'
  }
  $relative = if ($Name -ceq 'codex.cmd') {
    'npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
  } else {
    'npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe'
  }
  $candidate = [IO.Path]::GetFullPath((Join-Path $roamingRoot $relative))
  [void](Assert-LanePathWithoutReparse `
      -Path $candidate `
      -TrustedRoot ([IO.Path]::GetPathRoot($candidate)) `
      -ExpectedType Leaf)
  $command = Get-Command `
    -Name $candidate `
    -CommandType Application `
    -ErrorAction Stop
  if (-not ([IO.Path]::GetFullPath([string]$command.Source)).Equals(
      $candidate,
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "lane CLI '$Name' is not the trusted npm-native application"
  }
  return $candidate
}

function Assert-LaneBootstrapIntegrity {
  param(
    [Parameter(Mandatory)] [string] $ScriptRoot,
    [Parameter(Mandatory)] [string] $BootstrapRoot
  )

  $trustedRoot = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($ScriptRoot))
  [void](Assert-LanePathWithoutReparse `
    -Path $ScriptRoot -TrustedRoot $trustedRoot -ExpectedType Directory)
  [void](Assert-LanePathWithoutReparse `
    -Path $BootstrapRoot -TrustedRoot $trustedRoot -ExpectedType Directory)

  $deploymentPath = Join-Path $ScriptRoot 'deployment-manifest.json'
  if (-not (Test-Path -LiteralPath $deploymentPath -PathType Leaf)) {
    return
  }
  [void](Assert-LanePathWithoutReparse `
    -Path $deploymentPath -TrustedRoot $trustedRoot -ExpectedType Leaf)
  $expectedManifestHash = [string]$script:LaneManifestAnchor
  if (
    $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
    (Get-FileHash -LiteralPath $deploymentPath -Algorithm SHA256).Hash -cne
      $expectedManifestHash.ToUpperInvariant()
  ) {
    throw 'lane deployment manifest is not externally anchored'
  }
  $deploymentSnapshot = Read-Utf8LaneSnapshot -Path $deploymentPath
  if ([string]$deploymentSnapshot.Hash -cne $expectedManifestHash.ToUpperInvariant()) {
    throw 'lane deployment manifest changed during bootstrap verification'
  }
  $deployment = [string]$deploymentSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
  $prefix = 'tools-bootstrap/.agent-bridge/bin/'
  $expectedFiles = @{}
  foreach ($property in @($deployment.files.PSObject.Properties)) {
    $relativeName = [string]$property.Name
    if (-not $relativeName.StartsWith(
        $prefix,
        [StringComparison]::Ordinal
      )) {
      continue
    }
    $leaf = $relativeName.Substring($prefix.Length)
    if (
      [string]::IsNullOrWhiteSpace($leaf) -or
      $leaf.IndexOfAny([char[]]@('\', '/')) -ge 0
    ) {
      throw "unsafe lane bootstrap manifest path: $relativeName"
    }
    $candidate = Join-Path $BootstrapRoot $leaf
    [void](Assert-LanePathWithoutReparse `
      -Path $candidate -TrustedRoot $trustedRoot -ExpectedType Leaf)
    if (
      -not (Test-Path -LiteralPath $candidate -PathType Leaf) -or
      (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -cne
        [string]$property.Value
    ) {
      throw "lane bootstrap bundle hash mismatch: $relativeName"
    }
    $expectedFiles[$leaf.ToLowerInvariant()] = $true
  }
  if (-not $expectedFiles.ContainsKey('start-agentbridgesession.ps1')) {
    throw 'lane bootstrap manifest is missing Start-AgentBridgeSession.ps1'
  }
  $actualFiles = @(Get-ChildItem -LiteralPath $BootstrapRoot -File)
  if ($actualFiles.Count -ne $expectedFiles.Count) {
    throw 'lane bootstrap bundle contains an unexpected file set'
  }
  $registryRelative = 'tools-bootstrap/configs/bridge_identity_registry.json'
  $registryProperty = $deployment.files.PSObject.Properties[$registryRelative]
  $registryPath = Join-Path (
    Split-Path -Parent (Split-Path -Parent $BootstrapRoot)
  ) 'configs\bridge_identity_registry.json'
  [void](Assert-LanePathWithoutReparse `
    -Path $registryPath -TrustedRoot $trustedRoot -ExpectedType Leaf)
  if (
    $null -eq $registryProperty -or
    -not (Test-Path -LiteralPath $registryPath -PathType Leaf) -or
    (Get-FileHash -LiteralPath $registryPath -Algorithm SHA256).Hash -cne
      [string]$registryProperty.Value
  ) {
    throw 'lane bridge identity registry bundle hash mismatch'
  }
}

$laneTrustedDrive = [IO.Path]::GetPathRoot(
  [IO.Path]::GetFullPath($PSScriptRoot)
)
[void](Assert-LanePathWithoutReparse `
  -Path $PSScriptRoot -TrustedRoot $laneTrustedDrive -ExpectedType Directory)
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  throw "fleet manifest is missing: $ManifestPath"
}
[void](Assert-LanePathWithoutReparse `
  -Path $ManifestPath -TrustedRoot $laneTrustedDrive -ExpectedType Leaf)
$manifestSnapshot = Read-Utf8LaneSnapshot -Path $ManifestPath
$fixedDeploymentManifest = Join-Path $PSScriptRoot 'deployment-manifest.json'
$deploymentAnchor = $null
if (Test-Path -LiteralPath $fixedDeploymentManifest -PathType Leaf) {
  [void](Assert-LanePathWithoutReparse `
    -Path $fixedDeploymentManifest `
    -TrustedRoot $laneTrustedDrive `
    -ExpectedType Leaf)
  $bundledFleetPath = Resolve-NormalizedPath -Path (
    Join-Path $PSScriptRoot 'wd-fleet.json'
  )
  if (-not (Resolve-NormalizedPath -Path $ManifestPath).Equals(
      $bundledFleetPath,
      [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw 'deployed lane launcher requires its bundled wd-fleet.json'
  }
  $expectedManifestHash = [string]$script:LaneManifestAnchor
  $deploymentSnapshot = Read-Utf8LaneSnapshot -Path $fixedDeploymentManifest
  if (
    $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
    [string]$deploymentSnapshot.Hash -cne
      $expectedManifestHash.ToUpperInvariant()
  ) {
    throw 'lane deployment manifest is not externally anchored'
  }
  $deploymentAnchor = [string]$deploymentSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
  $fleetHashProperty = $deploymentAnchor.files.PSObject.Properties['wd-fleet.json']
  $selfHashProperty = $deploymentAnchor.files.PSObject.Properties[
    'start-wd-agent.ps1'
  ]
  if (
    $null -eq $fleetHashProperty -or
    [string]$manifestSnapshot.Hash -cne [string]$fleetHashProperty.Value -or
    $null -eq $selfHashProperty -or
    (Get-FileHash `
      -LiteralPath (Join-Path $PSScriptRoot 'start-wd-agent.ps1') `
      -Algorithm SHA256).Hash -cne [string]$selfHashProperty.Value
  ) {
    throw 'loaded lane launcher or fleet manifest does not match the anchored bundle'
  }
}
$manifest = [string]$manifestSnapshot.Text |
  ConvertFrom-Json -ErrorAction Stop
if ([int]$manifest.schema_version -ne 2) {
  throw "unsupported fleet manifest schema: $($manifest.schema_version)"
}
$gitProperty = $manifest.PSObject.Properties['git_executable']
if (
  $null -eq $gitProperty -or
  [string]::IsNullOrWhiteSpace([string]$gitProperty.Value)
) {
  throw 'fleet manifest is missing git_executable'
}
$script:WdGitExecutable = Resolve-WdLaneGitApplication `
  -ConfiguredPath ([string]$gitProperty.Value)
$bundleGeneration = if ($null -ne $deploymentAnchor) {
  ([string]$deploymentAnchor.source_commit).ToLowerInvariant()
} else {
  (Invoke-CheckedGit -Worktree $PSScriptRoot -Arguments @('rev-parse', 'HEAD')).ToLowerInvariant()
}
if ($bundleGeneration -cnotmatch '^[0-9a-f]{40}$') {
  throw 'lane bundle generation must be a full lowercase Git commit'
}
if (-not $RunId) {
  $RunId = 'wd-lane-' + $Agent + '-' +
    (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
}
if ($RunId -cnotmatch '^[A-Za-z0-9._-]{1,128}$') {
  throw 'RunId must match ^[A-Za-z0-9._-]{1,128}$'
}

$matches = @($manifest.lanes | Where-Object { [string]$_.agent -ceq $Agent })
if ($matches.Count -ne 1) {
  throw "manifest must contain exactly one lane for '$Agent'; found $($matches.Count)"
}
$lane = $matches[0]

$worktree = Resolve-NormalizedPath -Path ([string]$lane.worktree)
$primaryRepo = Resolve-NormalizedPath -Path ([string]$manifest.primary_repo_root)
$expectedCommonGit = Resolve-NormalizedPath -Path ([string]$manifest.repo_common_git_dir)
$runtimeRoot = Resolve-NormalizedPath -Path ([string]$manifest.runtime_root)

if (-not $worktree.StartsWith('C:\', [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "lane worktree must be on persistent C: drive: $worktree"
}
if (-not (Test-Path -LiteralPath $worktree -PathType Container)) {
  throw "lane worktree is missing: $worktree"
}
if (-not (Test-Path -LiteralPath (Join-Path $worktree '.git'))) {
  throw "lane worktree has no .git membership file: $worktree"
}

$actualTop = Resolve-NormalizedPath -Path (
  Invoke-CheckedGit -Worktree $worktree -Arguments @('rev-parse', '--show-toplevel')
)
$actualCommonGit = Resolve-NormalizedPath -Path (
  Invoke-CheckedGit -Worktree $worktree -Arguments @(
    'rev-parse',
    '--path-format=absolute',
    '--git-common-dir'
  )
)
$actualBranch = Invoke-CheckedGit -Worktree $worktree -Arguments @(
  'branch',
  '--show-current'
)
$actualHead = Invoke-CheckedGit -Worktree $worktree -Arguments @('rev-parse', 'HEAD')

if (-not $actualTop.Equals($worktree, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "lane '$Agent' resolves to unexpected Git top-level '$actualTop'"
}
if (-not $actualCommonGit.Equals(
    $expectedCommonGit,
    [System.StringComparison]::OrdinalIgnoreCase
  )) {
  throw "lane '$Agent' is not a member of the canonical C:\Python\project2 repository"
}
$resumePolicy = [string]$lane.resume_policy
if ($resumePolicy -cnotin @('pinned', 'current_worktree')) {
  throw "lane '$Agent' has unsupported resume_policy '$resumePolicy'"
}
if ([string]::IsNullOrWhiteSpace($actualBranch)) {
  throw "lane '$Agent' cannot resume a detached HEAD"
}
if ($actualHead -cnotmatch '^[0-9a-f]{40}$') {
  throw "lane '$Agent' resolved a malformed HEAD: $actualHead"
}
$pinExact = (
  $actualBranch -ceq [string]$lane.branch -and
  $actualHead -ceq [string]$lane.head
)
if (-not $pinExact -and $resumePolicy -ceq 'pinned') {
  if ($actualBranch -cne [string]$lane.branch) {
    throw "lane '$Agent' branch mismatch: expected '$($lane.branch)', found '$actualBranch'"
  }
  throw "lane '$Agent' HEAD mismatch: expected '$($lane.head)', found '$actualHead'"
}
if (-not $pinExact) {
  Write-Warning (
    "lane '$Agent' is resuming its canonical current worktree at " +
    "$actualBranch@$actualHead instead of the deployment baseline " +
    "$($lane.branch)@$($lane.head)"
  )
}

$currentPointer = [string]$manifest.state_precedence.current_state_pointer
$deploymentManifest = Join-Path $PSScriptRoot 'deployment-manifest.json'
$sourceTreeMode = $false
if (-not (Test-Path -LiteralPath $deploymentManifest -PathType Leaf)) {
  try {
    $insideSourceTree = (
      Invoke-CheckedGit -Worktree $PSScriptRoot -Arguments @(
        'rev-parse',
        '--is-inside-work-tree'
      )
    ) -ceq 'true'
    if ($insideSourceTree) {
      $sourceCommonGit = Resolve-NormalizedPath -Path (
        Invoke-CheckedGit -Worktree $PSScriptRoot -Arguments @(
          'rev-parse',
          '--path-format=absolute',
          '--git-common-dir'
        )
      )
      $sourceTreeMode = $sourceCommonGit.Equals(
        $expectedCommonGit,
        [System.StringComparison]::OrdinalIgnoreCase
      )
    }
  } catch {
    $sourceTreeMode = $false
  }
  if (-not $sourceTreeMode) {
    throw "undeployed lane launcher is not inside canonical C:\Python\project2"
  }
}
if ($sourceTreeMode -and -not $DryRun) {
  throw 'source lane launcher supports -DryRun only; live use requires a deployed bundle'
}
$bootstrapRoot = if ($sourceTreeMode) {
  $sourceTop = Resolve-NormalizedPath -Path (
    Invoke-CheckedGit -Worktree $PSScriptRoot -Arguments @(
      'rev-parse',
      '--show-toplevel'
    )
  )
  Join-Path $sourceTop '.agent-bridge\bin'
} else {
  Join-Path $PSScriptRoot 'tools-bootstrap\.agent-bridge\bin'
}
$bootstrapRoot = Resolve-NormalizedPath -Path $bootstrapRoot
$starter = Join-Path $bootstrapRoot 'Start-AgentBridgeSession.ps1'
$writer = Join-Path $bootstrapRoot 'Write-AgentEvent.ps1'
Assert-LaneBootstrapIntegrity `
  -ScriptRoot $PSScriptRoot `
  -BootstrapRoot $bootstrapRoot
[void](Read-NonEmptyFile -Path $starter -Label "lane '$Agent' bridge starter")
[void](Read-NonEmptyFile -Path $writer -Label "lane '$Agent' bridge writer")
$targetState = $manifest.target_state
if (
  $null -eq $targetState -or
  [string]$targetState.id -cne 'wd-swarm-target-state-v1' -or
  [string]$targetState.capability_effect -cne 'none' -or
  [string]$targetState.relative_path -cne 'WD_SWARM_TARGET_STATE_V1.md' -or
  [string]$targetState.sha256 -cnotmatch '^[0-9A-F]{64}$'
) {
  throw 'fleet target-state manifest is missing or unsafe'
}
$targetStatePath = Join-Path $PSScriptRoot ([string]$targetState.relative_path)
[void](Assert-LanePathWithoutReparse `
  -Path $targetStatePath -TrustedRoot $laneTrustedDrive -ExpectedType Leaf)
if (
  (Get-FileHash -LiteralPath $targetStatePath -Algorithm SHA256).Hash -cne
    [string]$targetState.sha256
) {
  throw 'fleet target-state document hash mismatch'
}
[void](Read-NonEmptyFile -Path $targetStatePath -Label 'fleet target state')
if (
  $DryRun -and
  $sourceTreeMode -and
  -not (Test-Path -LiteralPath $currentPointer -PathType Leaf)
) {
  Write-Warning "DryRun: current reboot pointer will be generated during committed bundle deployment: $currentPointer"
} else {
  [void](Read-NonEmptyFile -Path $currentPointer -Label 'current reboot pointer')
}
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.base_state) -Label 'base reboot state')
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.roles) -Label 'fleet roles')
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.current_handoff) -Label 'current restart handoff')
[void](Read-NonEmptyFile -Path ([string]$lane.prompt) -Label "lane '$Agent' role prompt")
[void](Read-NonEmptyFile -Path ([string]$lane.handoff) -Label "lane '$Agent' handoff")

$grokMarkdown = [string]$manifest.grok_markdown
if (-not $DryRun) {
  [void](Read-NonEmptyFile -Path $grokMarkdown -Label 'current Grok model guide')
} elseif (-not (Test-Path -LiteralPath $grokMarkdown -PathType Leaf)) {
  Write-Warning "DryRun: generated Grok guide is not present yet; the fleet launcher will resolve it before a real launch: $grokMarkdown"
}

$cliName = [string]$lane.cli
$model = [string]$lane.model
$effort = [string]$lane.effort
if ([string]::IsNullOrWhiteSpace($model)) {
  throw "lane '$Agent' has no explicit model"
}
$supportedEfforts = if ($cliName -ieq 'codex.cmd') {
  @('low', 'medium', 'high', 'xhigh', 'max', 'ultra')
} else {
  @('low', 'medium', 'high', 'xhigh', 'max')
}
if ($effort -cnotin $supportedEfforts) {
  throw "lane '$Agent' has unsupported effort '$effort'"
}
$expectedRuntime = @{
  'codex-lead-1' = [pscustomobject]@{ cli = 'codex.cmd'; model = 'gpt-5.6-sol'; effort = 'ultra' }
  'claude-rco-1' = [pscustomobject]@{ cli = 'claude.cmd'; model = 'sonnet'; effort = 'max' }
  'claude-rco-2' = [pscustomobject]@{ cli = 'claude.cmd'; model = 'sonnet'; effort = 'max' }
  'fable-5' = [pscustomobject]@{ cli = 'claude.cmd'; model = 'fable'; effort = 'max' }
}[$Agent]
if (
  $null -eq $expectedRuntime -or
  $cliName -cne [string]$expectedRuntime.cli -or
  $model -cne [string]$expectedRuntime.model -or
  $effort -cne [string]$expectedRuntime.effort
) {
  throw "lane '$Agent' runtime selection differs from the supported fleet contract"
}
$cliPath = Resolve-WdLaneCliApplication -Name $cliName
$cliExecutableHash = (
  Get-FileHash -LiteralPath $cliPath -Algorithm SHA256
).Hash

$stateRule = [string]$manifest.state_precedence.rule
$startupPrompt = (
  "Read the current reboot pointer first: {0}. Then read these durable startup " +
  "files in order: {1}, {2}, {3}, {4}, {5}, {6}. " +
  "Runtime model selection is explicitly pinned to {7} at effort {8}. Any legacy model labels " +
  "in durable role, prompt, or historical files are " +
  "historical metadata only, not a pin or current runtime identity. " +
  "State precedence: {9} Then read the bridge with Read-AgentBridge.ps1 " +
  "-NoAckReceived, use Get-BridgeNextAction, reject stale acknowledgements, " +
  "and resume the existing task autonomously without inventing authority. " +
  "Use the Grok guide when Grok analysis is useful."
) -f @(
  [string]$manifest.state_precedence.current_state_pointer,
  [string]$manifest.state_precedence.base_state,
  [string]$manifest.state_precedence.roles,
  [string]$manifest.state_precedence.current_handoff,
  [string]$lane.prompt,
  [string]$lane.handoff,
  $grokMarkdown,
  $model,
  $effort,
  $stateRule
)

if (-not $HandshakeDirectory) {
  $HandshakeDirectory = Join-Path ([string]$manifest.handshake_root) $RunId
}
$handshakeDirectoryFull = Resolve-NormalizedPath -Path $HandshakeDirectory
$handshakeRootFull = Resolve-NormalizedPath -Path ([string]$manifest.handshake_root)
if (-not (
    $handshakeDirectoryFull.Equals(
      $handshakeRootFull,
      [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $handshakeDirectoryFull.StartsWith(
      ($handshakeRootFull + '\'),
      [System.StringComparison]::OrdinalIgnoreCase
    )
  )) {
  throw "handshake directory escapes the configured root: $handshakeDirectoryFull"
}

Write-Host ''
Write-Host ("WD lane: {0}" -f $Agent) -ForegroundColor Cyan
Write-Host ("  worktree: {0}" -f $worktree)
Write-Host ("  branch:   {0}" -f $actualBranch)
Write-Host ("  head:     {0}" -f $actualHead)
Write-Host ("  run_id:   {0}" -f $RunId)
Write-Host ("  cli:      {0}" -f $cliName)
Write-Host ("  model:    {0} ({1})" -f $model, $effort)
Write-Host ("  target:   {0}" -f [string]$targetState.id)

if ($DryRun) {
  Write-Host '  DRY RUN: bridge bootstrap, handshake write, and CLI launch suppressed.'
  return [pscustomobject]@{
    agent = $Agent
    run_id = $RunId
    worktree = $worktree
    branch = $actualBranch
    head = $actualHead
    cli = $cliName
    cli_executable = $cliPath
    cli_executable_sha256 = $cliExecutableHash
    model = $model
    effort = $effort
    resume_policy = $resumePolicy
    target_state_id = [string]$targetState.id
    dry_run = $true
  }
}

if ([Console]::IsInputRedirected) {
  throw "lane '$Agent' must run in an interactive Windows Terminal tab"
}

$env:AGENT_BRIDGE_AGENT = [string]$lane.agent
$env:AGENT_BRIDGE_AGENT_UUID = ([string]$lane.agent_uuid).ToLowerInvariant()
$env:AGENT_BRIDGE_RUN_ID = $RunId
$env:AGENT_BRIDGE_SESSION_ID = $RunId
$env:AGENT_BRIDGE_RUNTIME_ROOT = $runtimeRoot
$env:WD_AGENT_PROMPT_FILE = [string]$lane.prompt
$env:WD_AGENT_RESTART_HANDOFF = [string]$manifest.state_precedence.current_handoff
$env:WD_AGENT_3PACK_ROLES = [string]$manifest.state_precedence.roles
$env:WD_AGENT_PROFILE = [string]$lane.agent
$env:WD_GROK_MODEL_GUIDE = $grokMarkdown

$sessionArgs = @{
  Agent = [string]$lane.agent
  RuntimeRoot = $runtimeRoot
  RepoRoot = $worktree
  RunId = $RunId
  Role = [string]$lane.role
  AgentUuid = [string]$lane.agent_uuid
  Capabilities = @($lane.capabilities | ForEach-Object { [string]$_ })
  SkipBridgeRead = $true
  SkipWakeWatcher = $true
  PrimaryRepoRoot = $primaryRepo
}
if ([bool]$lane.require_dedicated_worktree) {
  $sessionArgs.RequireDedicatedWorktree = $true
}

Set-Location -LiteralPath $worktree
try {
  Assert-LaneBootstrapIntegrity `
    -ScriptRoot $PSScriptRoot `
    -BootstrapRoot $bootstrapRoot
  [void](Assert-LanePathWithoutReparse `
    -Path $starter -TrustedRoot $laneTrustedDrive -ExpectedType Leaf)
  $session = . $starter @sessionArgs
}
finally {
  Assert-LaneBootstrapIntegrity `
    -ScriptRoot $PSScriptRoot `
    -BootstrapRoot $bootstrapRoot
}

Assert-LaneBootstrapIntegrity `
  -ScriptRoot $PSScriptRoot `
  -BootstrapRoot $bootstrapRoot
[void](Assert-LanePathWithoutReparse `
  -Path $writer -TrustedRoot $laneTrustedDrive -ExpectedType Leaf)
$targetPayload = [ordered]@{
  target_state_id = [string]$targetState.id
  target_state_sha256 = [string]$targetState.sha256
  source_image_sha256 = [string]$targetState.source_image_sha256
  capability_effect = 'none'
  model = $model
  effort = $effort
  cli_executable = $cliPath
  cli_executable_sha256 = $cliExecutableHash
  resume_policy = $resumePolicy
  baseline_branch = [string]$lane.branch
  baseline_head = [string]$lane.head
  resumed_branch = $actualBranch
  resumed_head = $actualHead
} | ConvertTo-Json -Compress
& $writer `
  -Agent $Agent `
  -Type status `
  -TaskId ([string]$targetState.id) `
  -Status target_state_manifested `
  -Message "Manifested the shared WaggleDance target state for reboot generation $RunId; this grants no capability or authority." `
  -RunId $RunId `
  -Role ([string]$lane.role) `
  -AgentUuid ([string]$lane.agent_uuid) `
  -SessionId $RunId `
  -Capabilities @($lane.capabilities | ForEach-Object { [string]$_ }) `
  -PayloadJson $targetPayload |
  Out-Host
Assert-LaneBootstrapIntegrity `
  -ScriptRoot $PSScriptRoot `
  -BootstrapRoot $bootstrapRoot

$canaryTaskId = "wd-append-canary-$RunId"
$canaryPayload = [ordered]@{
  schema_version = 1
  generation = $bundleGeneration
  target_state_id = [string]$targetState.id
  manifest_writer = 'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'
} | ConvertTo-Json -Compress
$canaryStartedUtc = [DateTimeOffset]::UtcNow
$canaryOutput = @(
  & $writer `
    -Agent $Agent `
    -Type status `
    -TaskId $canaryTaskId `
    -Status append_canary `
    -Message "Verified the manifest-hashed canonical writer for $Agent generation $RunId." `
    -To '' `
    -RunId $RunId `
    -Role ([string]$lane.role) `
    -AgentUuid ([string]$lane.agent_uuid) `
    -SessionId $RunId `
    -Capabilities @($lane.capabilities | ForEach-Object { [string]$_ }) `
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
  [string]$canaryEvents[0].agent -cne $Agent -or
  [string]$canaryEvents[0].agent_uuid -cne [string]$lane.agent_uuid -or
  [string]$canaryEvents[0].run_id -cne $RunId -or
  [string]$canaryEvents[0].session_id -cne $RunId -or
  [string]$canaryEvents[0].task_id -cne $canaryTaskId -or
  [string]$canaryEvents[0].to -cne '' -or
  [int]$canaryEvents[0].pid -ne $PID -or
  $canaryLatencyMs -gt 5000
) {
  throw "manifest-writer append canary failed for $Agent"
}
$canaryOutput | Out-Host
Assert-LaneBootstrapIntegrity `
  -ScriptRoot $PSScriptRoot `
  -BootstrapRoot $bootstrapRoot

if (-not (Test-Path -LiteralPath $handshakeDirectoryFull -PathType Container)) {
  [void](New-Item -ItemType Directory -Path $handshakeDirectoryFull -Force)
}
$handshakePath = Join-Path $handshakeDirectoryFull ("{0}.json" -f $Agent)
$temporaryHandshake = "$handshakePath.$PID.tmp"
$handshake = [ordered]@{
  schema_version = 1
  status = 'bridge_bootstrapped'
  agent = $Agent
  agent_uuid = [string]$lane.agent_uuid
  role = [string]$lane.role
  run_id = $RunId
  session_id = $RunId
  pid = $PID
  worktree = $worktree
  branch = $actualBranch
  head = $actualHead
  runtime_root = $runtimeRoot
  cli = $cliName
  model_selection = 'explicit'
  model = $model
  effort = $effort
  cli_executable = $cliPath
  cli_executable_sha256 = $cliExecutableHash
  resume_policy = $resumePolicy
  baseline_branch = [string]$lane.branch
  baseline_head = [string]$lane.head
  target_state_id = [string]$targetState.id
  target_state_sha256 = [string]$targetState.sha256
  target_state_manifested = $true
  append_canary = $true
  append_canary_task_id = $canaryTaskId
  append_canary_event_utc = [string]$canaryEvents[0].ts_utc
  append_canary_latency_ms = $canaryLatencyMs
  bundle_generation = $bundleGeneration
  created_at_utc = (Get-Date).ToUniversalTime().ToString('o')
}
try {
  $handshake |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $temporaryHandshake -Encoding UTF8
  Move-Item -LiteralPath $temporaryHandshake -Destination $handshakePath -Force
} finally {
  if (Test-Path -LiteralPath $temporaryHandshake -PathType Leaf) {
    Remove-Item -LiteralPath $temporaryHandshake -Force -ErrorAction SilentlyContinue
  }
}
Write-Host ("  handshake: {0}" -f $handshakePath)

$launchArguments = @()
if ($cliName -ieq 'claude.cmd') {
  $launchArguments += @(
    '--model', $model,
    '--effort', $effort,
    '--dangerously-skip-permissions'
  )
} elseif ($cliName -ieq 'codex.cmd') {
  $launchArguments += @(
    '--model', $model,
    '-c', ('model_reasoning_effort="{0}"' -f $effort)
  )
} else {
  throw "lane '$Agent' uses unsupported CLI '$cliName'"
}
$launchArguments += $startupPrompt

$finalCliPath = Resolve-WdLaneCliApplication -Name $cliName
if (
  -not $finalCliPath.Equals(
    $cliPath,
    [StringComparison]::OrdinalIgnoreCase
  ) -or
  (Get-FileHash -LiteralPath $finalCliPath -Algorithm SHA256).Hash -cne
    $cliExecutableHash
) {
  throw "lane '$Agent' CLI application changed after its handshake"
}

$previousPreference = $ErrorActionPreference
try {
  $ErrorActionPreference = 'Continue'
  & $cliPath @launchArguments
  $cliExitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $previousPreference
}
if ($null -ne $cliExitCode -and $cliExitCode -ne 0) {
  throw "lane '$Agent' CLI exited with code $cliExitCode"
}

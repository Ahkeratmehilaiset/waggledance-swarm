#requires -Version 5.1
<#
.SYNOPSIS
  Fail-closed, one-command reboot restore for the WaggleDance agent fleet.

.DESCRIPTION
  Preflights the entire pinned fleet before the first mutation. A real run
  updates Codex and Claude Code once, resolves the current Grok model, ensures
  the supervisor-managed Tools consumer, and launches missing interactive lanes.
  DryRun performs validation and prints the update/launch plan without updating,
  writing handshake files, or starting fleet processes.
#>
[CmdletBinding()]
param(
  [string] $ManifestPath = '',
  [string] $RunId = '',
  [ValidateRange(10, 300)]
  [int] $HandshakeTimeoutSeconds = 90,
  [switch] $SkipCliUpdate,
  [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ManifestPath) {
  $ManifestPath = Join-Path $PSScriptRoot 'wd-fleet.json'
}

function Resolve-NormalizedPath {
  param([Parameter(Mandatory)] [string] $Path)
  return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
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

function Invoke-CheckedGit {
  param(
    [Parameter(Mandatory)] [string] $Worktree,
    [Parameter(Mandatory)] [string[]] $Arguments
  )
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = @(& git -C $Worktree @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($exitCode -ne 0) {
    throw "git -C '$Worktree' $($Arguments -join ' ') failed ($exitCode): $($output -join ' ')"
  }
  return (($output -join "`n").Trim())
}

function Invoke-CheckedNative {
  param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [string[]] $Arguments,
    [Parameter(Mandatory)] [string] $Label
  )
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = @(& $Path @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  foreach ($line in $output) {
    Write-Host ("    {0}" -f [string]$line)
  }
  if ($exitCode -ne 0) {
    throw "$Label failed with exit code $exitCode"
  }
  return (($output -join "`n").Trim())
}

function Resolve-ApplicationPath {
  param(
    [Parameter(Mandatory)] [string] $Name,
    [string] $PreferredPath = ''
  )
  $commands = @(
    Get-Command $Name `
      -CommandType Application `
      -All `
      -ErrorAction SilentlyContinue
  )
  if ($commands.Count -eq 0) {
    throw "$Name is not installed or not on PATH"
  }
  $paths = @(
    $commands |
      ForEach-Object {
        if ($_.Path) { [string]$_.Path } else { [string]$_.Source }
      } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )
  $selected = ''
  if ($PreferredPath) {
    $preferredFull = Resolve-NormalizedPath -Path $PreferredPath
    $selected = @(
      $paths | Where-Object {
        (Resolve-NormalizedPath -Path $_).Equals(
          $preferredFull,
          [System.StringComparison]::OrdinalIgnoreCase
        )
      }
    ) | Select-Object -First 1
  }
  if (-not $selected) {
    $selected = @($paths | Select-Object -First 1)
  }
  $selected = [string]$selected
  if (
    [string]::IsNullOrWhiteSpace($selected) -or
    -not (Test-Path -LiteralPath $selected -PathType Leaf)
  ) {
    throw "$Name resolved to a missing application path: $selected"
  }
  return (Resolve-NormalizedPath -Path $selected)
}

function Test-ContainsApplySwitch {
  param([AllowEmptyString()] [string] $Text)
  return $Text -match (
    '(?i)(?:^|[\s"''])-(?:a|ap|app|appl|apply)' +
    '(?=[:\s"'']|$)'
  )
}

function Test-DirectLegacyDriverAction {
  param(
    [AllowEmptyString()] [string] $Execute,
    [AllowEmptyString()] [string] $Arguments,
    [Parameter(Mandatory)] [string] $ExpectedScript
  )
  $stablePowerShell = Join-Path $env:SystemRoot (
    'System32\WindowsPowerShell\v1.0\powershell.exe'
  )
  if (
    -not $Execute.Equals(
      'powershell.exe',
      [System.StringComparison]::OrdinalIgnoreCase
    ) -and
    -not $Execute.Equals(
      $stablePowerShell,
      [System.StringComparison]::OrdinalIgnoreCase
    )
  ) {
    return $false
  }
  $escapedScript = [Regex]::Escape($ExpectedScript)
  $safeArguments = (
    '^(?i:-NoProfile\s+-ExecutionPolicy\s+Bypass\s+-File\s+' +
    '(?:"{0}"|{0})\s+-Loop\s+-PollSeconds\s+120)\s*$'
  ) -f $escapedScript
  return $Arguments -match $safeArguments
}

function Get-OptionalScheduledTask {
  param([Parameter(Mandatory)] [string] $TaskName)
  try {
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
  } catch {
    if (
      [string]$_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*' -or
      $_.CategoryInfo.Category -eq [Management.Automation.ErrorCategory]::ObjectNotFound
    ) {
      return $null
    }
    throw
  }
}

function Get-AllProcessSnapshots {
  return @(
    Get-CimInstance Win32_Process -ErrorAction Stop |
      Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.Name -match '^(powershell|pwsh)\.exe$'
      }
  )
}

function Get-LaneProcesses {
  param(
    [Parameter(Mandatory)] $Lane,
    [Parameter(Mandatory)] [object[]] $Processes
  )
  $newLauncher = 'start-wd-agent.ps1'
  $agentArgumentPattern = '(?i)(?:^|\s)-Agent\s+["'']?' +
    [regex]::Escape([string]$Lane.agent) + '(?:["'']?)(?:\s|$)'
  return @(
    $Processes | Where-Object {
      $commandLine = [string]$_.CommandLine
      $newMatch = (
        $commandLine.IndexOf(
          $newLauncher,
          [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0 -and
        $commandLine -match $agentArgumentPattern
      )
      $legacyMatch = $false
      foreach ($marker in @($Lane.legacy_process_markers)) {
        if ($commandLine.IndexOf(
            [string]$marker,
            [System.StringComparison]::OrdinalIgnoreCase
          ) -ge 0) {
          $legacyMatch = $true
          break
        }
      }
      $newMatch -or $legacyMatch
    }
  )
}

function Get-ToolsProcesses {
  param(
    [Parameter(Mandatory)] $ToolsConfig,
    [Parameter(Mandatory)] [object[]] $Processes
  )
  $agentPattern = '(?i)(?:^|\s)-Agent\s+["'']?' +
    [regex]::Escape([string]$ToolsConfig.agent) + '(?:["'']?)(?:\s|$)'
  return @(
    $Processes | Where-Object {
      $commandLine = [string]$_.CommandLine
      $markerMatch = $false
      foreach ($marker in @($ToolsConfig.process_markers)) {
        if ($commandLine.IndexOf(
            [string]$marker,
            [System.StringComparison]::OrdinalIgnoreCase
          ) -ge 0) {
          $markerMatch = $true
          break
        }
      }
      $markerMatch -and (
        $commandLine -match $agentPattern -or
        $commandLine.IndexOf(
          'start-wd-tools-consumer.ps1',
          [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0
      )
    }
  )
}

function Assert-DeployedBundle {
  param([Parameter(Mandatory)] $Manifest)

  $deploymentManifestPath = Join-Path $PSScriptRoot (
    [string]$Manifest.deployment.manifest_file
  )
  if (-not (Test-Path -LiteralPath $deploymentManifestPath -PathType Leaf)) {
    $insideSource = $false
    try {
      $insideSource = (
        Invoke-CheckedGit -Worktree $PSScriptRoot -Arguments @(
          'rev-parse',
          '--is-inside-work-tree'
        )
      ) -eq 'true'
    } catch {
      $insideSource = $false
    }
    if (-not $insideSource) {
      throw "deployed reboot bundle has no deployment manifest: $deploymentManifestPath"
    }
    $sourceCommonGit = Resolve-NormalizedPath -Path (
      Invoke-CheckedGit -Worktree $PSScriptRoot -Arguments @(
        'rev-parse',
        '--path-format=absolute',
        '--git-common-dir'
      )
    )
    $expectedSourceCommonGit = Resolve-NormalizedPath -Path (
      [string]$Manifest.repo_common_git_dir
    )
    if (-not $sourceCommonGit.Equals(
        $expectedSourceCommonGit,
        [System.StringComparison]::OrdinalIgnoreCase
      )) {
      throw 'source-tree reboot rehearsal is not inside canonical C:\Python\project2'
    }
    Write-Host '  bundle: source-tree mode (deployment manifest not required)'
    return 'source'
  }

  $deployment = (Get-Content -LiteralPath $deploymentManifestPath -Raw) |
    ConvertFrom-Json -ErrorAction Stop
  if ([int]$deployment.schema_version -ne 1) {
    throw "unsupported deployment manifest schema: $($deployment.schema_version)"
  }
  $fileProperties = @($deployment.files.PSObject.Properties)
  if ($fileProperties.Count -eq 0) {
    throw "deployment manifest has no file hashes: $deploymentManifestPath"
  }

  $listed = @{}
  foreach ($property in $fileProperties) {
    $relativeName = [string]$property.Name
    if (
      [System.IO.Path]::IsPathRooted($relativeName) -or
      $relativeName -match '(^|[\\/])\.\.([\\/]|$)'
    ) {
      throw "deployment manifest contains unsafe relative path: $relativeName"
    }
    $candidate = Resolve-NormalizedPath -Path (Join-Path $PSScriptRoot $relativeName)
    $bundleRoot = Resolve-NormalizedPath -Path $PSScriptRoot
    if (-not (
        $candidate.Equals($bundleRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidate.StartsWith(
          ($bundleRoot + '\'),
          [System.StringComparison]::OrdinalIgnoreCase
        )
      )) {
      throw "deployment manifest path escapes the bundle: $relativeName"
    }
    [void](Read-NonEmptyFile -Path $candidate -Label 'deployed bundle file')
    $expectedHash = ([string]$property.Value).ToUpperInvariant()
    $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($expectedHash -cne $actualHash) {
      throw "deployed bundle hash mismatch: $relativeName"
    }
    $listed[$relativeName.ToLowerInvariant()] = $true
  }
  foreach ($requiredName in @($Manifest.deployment.required_bundle_files)) {
    if (-not $listed.ContainsKey(([string]$requiredName).ToLowerInvariant())) {
      throw "deployment manifest does not cover required file: $requiredName"
    }
  }
  Write-Host ("  bundle: verified {0} deployed file hash(es)" -f $fileProperties.Count)
  return 'deployed'
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  throw "fleet manifest is missing: $ManifestPath"
}
$manifest = (Get-Content -LiteralPath $ManifestPath -Raw -ErrorAction Stop) |
  ConvertFrom-Json -ErrorAction Stop
if ([int]$manifest.schema_version -ne 2) {
  throw "unsupported fleet manifest schema: $($manifest.schema_version)"
}
if (@($manifest.lanes).Count -ne 4) {
  throw "fleet manifest must pin exactly four interactive lanes"
}

Write-Host ''
Write-Host '=== WaggleDance reboot preflight ===' -ForegroundColor Cyan
$bundleMode = Assert-DeployedBundle -Manifest $manifest

[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.base_state) -Label 'base reboot state')
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.roles) -Label 'fleet roles')
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.current_handoff) -Label 'current restart handoff')
$currentPointer = [string]$manifest.state_precedence.current_state_pointer
if (
  $DryRun -and
  $bundleMode -ceq 'source' -and
  -not (Test-Path -LiteralPath $currentPointer -PathType Leaf)
) {
  Write-Warning "DryRun: current reboot pointer will be generated during committed bundle deployment: $currentPointer"
} else {
  [void](Read-NonEmptyFile -Path $currentPointer -Label 'current reboot pointer')
}

$resolver = Join-Path $PSScriptRoot 'Resolve-WdGrokModel.ps1'
$agentLauncher = Join-Path $PSScriptRoot 'start-wd-agent.ps1'
[void](Read-NonEmptyFile -Path $resolver -Label 'Grok model resolver')
[void](Read-NonEmptyFile -Path $agentLauncher -Label 'per-lane launcher')

$expectedCommonGit = Resolve-NormalizedPath -Path ([string]$manifest.repo_common_git_dir)
$processes = Get-AllProcessSnapshots
$laneStates = @()
foreach ($lane in @($manifest.lanes)) {
  $worktree = Resolve-NormalizedPath -Path ([string]$lane.worktree)
  if (-not $worktree.StartsWith('C:\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "lane '$($lane.agent)' worktree is not on persistent C: drive: $worktree"
  }
  if (-not (Test-Path -LiteralPath $worktree -PathType Container)) {
    throw "lane '$($lane.agent)' worktree is missing: $worktree"
  }
  $actualTop = Resolve-NormalizedPath -Path (
    Invoke-CheckedGit -Worktree $worktree -Arguments @('rev-parse', '--show-toplevel')
  )
  $actualCommon = Resolve-NormalizedPath -Path (
    Invoke-CheckedGit -Worktree $worktree -Arguments @(
      'rev-parse',
      '--path-format=absolute',
      '--git-common-dir'
    )
  )
  $actualBranch = Invoke-CheckedGit -Worktree $worktree -Arguments @('branch', '--show-current')
  $actualHead = Invoke-CheckedGit -Worktree $worktree -Arguments @('rev-parse', 'HEAD')
  if (-not $actualTop.Equals($worktree, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "lane '$($lane.agent)' has unexpected Git top-level: $actualTop"
  }
  if (-not $actualCommon.Equals($expectedCommonGit, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "lane '$($lane.agent)' is not a canonical project2 worktree"
  }
  if ($actualBranch -cne [string]$lane.branch) {
    throw "lane '$($lane.agent)' branch mismatch: expected '$($lane.branch)', found '$actualBranch'"
  }
  if ($actualHead -cne [string]$lane.head) {
    throw "lane '$($lane.agent)' HEAD mismatch: expected '$($lane.head)', found '$actualHead'"
  }
  [void](Read-NonEmptyFile -Path (Join-Path $worktree '.agent-bridge\bin\Start-AgentBridgeSession.ps1') -Label "lane '$($lane.agent)' bridge starter")
  [void](Read-NonEmptyFile -Path ([string]$lane.prompt) -Label "lane '$($lane.agent)' role prompt")
  [void](Read-NonEmptyFile -Path ([string]$lane.handoff) -Label "lane '$($lane.agent)' handoff")

  $live = @(Get-LaneProcesses -Lane $lane -Processes $processes)
  if ($live.Count -gt 1) {
    throw "duplicate live lane '$($lane.agent)' PID(s): $(@($live.ProcessId) -join ',')"
  }
  $laneStates += [pscustomobject]@{ lane = $lane; live = $live }
  Write-Host ("  lane {0}: branch/head exact; live launchers={1}" -f $lane.agent, $live.Count)
}

$codexPath = Resolve-ApplicationPath -Name 'codex.cmd'
$claudePath = Resolve-ApplicationPath -Name 'claude.cmd'
$preferredWtPath = if ($env:LOCALAPPDATA) {
  Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wt.exe'
} else {
  ''
}
$wtPath = Resolve-ApplicationPath -Name 'wt.exe' -PreferredPath $preferredWtPath
Write-Host '  Codex version probe:'
$codexVersion = Invoke-CheckedNative -Path $codexPath -Arguments @('--version') -Label 'codex version probe'
Write-Host '  Codex update command probe:'
[void](Invoke-CheckedNative -Path $codexPath -Arguments @('update', '--help') -Label 'codex update help')
Write-Host '  Claude Code version probe:'
$claudeVersion = Invoke-CheckedNative -Path $claudePath -Arguments @('--version') -Label 'claude version probe'
Write-Host '  Claude Code update command probe:'
[void](Invoke-CheckedNative -Path $claudePath -Arguments @('update', '--help') -Label 'claude update help')

$containment = $manifest.merge_driver_containment
$standingTask = Get-ScheduledTask -TaskName ([string]$containment.standing_task) -ErrorAction Stop
if ([bool]$standingTask.Settings.Enabled -ne [bool]$containment.required_enabled) {
  throw "merge-driver containment violated: '$($containment.standing_task)' enabled=$($standingTask.Settings.Enabled)"
}
if ([string]$standingTask.State -cne [string]$containment.required_state) {
  throw "merge-driver containment violated: expected state '$($containment.required_state)', found '$($standingTask.State)'"
}
$liveApplyDrivers = @(
  $processes | Where-Object {
    $commandLine = [string]$_.CommandLine
    $commandLine.IndexOf(
      'Invoke-BridgeMergeDriver.ps1',
      [System.StringComparison]::OrdinalIgnoreCase
    ) -ge 0 -and
    (Test-ContainsApplySwitch $commandLine)
  }
)
if ($liveApplyDrivers.Count -gt 0) {
  throw "merge-driver containment violated: live Apply PID(s) $(@($liveApplyDrivers.ProcessId) -join ',')"
}

$legacyTask = Get-OptionalScheduledTask -TaskName ([string]$containment.legacy_task)
if ($legacyTask -and [bool]$legacyTask.Settings.Enabled) {
  foreach ($action in @($legacyTask.Actions)) {
    if (-not (Test-DirectLegacyDriverAction `
        -Execute ([string]$action.Execute) `
        -Arguments ([string]$action.Arguments) `
        -ExpectedScript ([string]$containment.legacy_script)
      )) {
      throw "legacy merge-driver task is enabled without a proven non-Apply action"
    }
  }
}

$toolsConfig = $manifest.tools_supervisor
$supervisorTask = Get-ScheduledTask -TaskName ([string]$toolsConfig.task_name) -ErrorAction Stop
if (-not [bool]$supervisorTask.Settings.Enabled) {
  throw "Tools supervisor task is disabled: $($toolsConfig.task_name)"
}
[void](Read-NonEmptyFile -Path ([string]$toolsConfig.supervisor_script) -Label 'Tools supervisor wrapper')
if (@($supervisorTask.Actions).Count -ne 1) {
  throw 'Tools supervisor must have exactly one action'
}
$expectedSupervisorExecutable = Join-Path $env:SystemRoot (
  'System32\WindowsPowerShell\v1.0\powershell.exe'
)
$expectedSupervisorArguments = (
  '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass ' +
  '-File "{0}" -Apply' -f [string]$toolsConfig.supervisor_script
)
foreach ($action in @($supervisorTask.Actions)) {
  if (
    -not ([string]$action.Execute).Equals(
      $expectedSupervisorExecutable,
      [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    [string]$action.Arguments -cne $expectedSupervisorArguments -or
    [string]$action.WorkingDirectory -cne 'C:\Python'
  ) {
    throw 'Tools supervisor action is not the exact stable registered tuple'
  }
}

[void](Read-NonEmptyFile -Path ([string]$toolsConfig.snapshot_path) -Label 'Tools supervisor snapshot')
$snapshot = (Get-Content -LiteralPath ([string]$toolsConfig.snapshot_path) -Raw) |
  ConvertFrom-Json -ErrorAction Stop
if ([string]$snapshot.schema -cne [string]$toolsConfig.snapshot_schema) {
  throw "Tools supervisor snapshot schema mismatch: $($snapshot.schema)"
}
$toolsSnapshot = $snapshot.tools_consumer
if (
  [string]$toolsSnapshot.agent -cne [string]$toolsConfig.agent -or
  [string]$toolsSnapshot.agent_uuid -cne [string]$toolsConfig.agent_uuid -or
  -not ([string]$toolsSnapshot.worktree).Equals(
    [string]$toolsConfig.worktree,
    [System.StringComparison]::OrdinalIgnoreCase
  )
) {
  throw 'Tools supervisor snapshot has the wrong agent, UUID, or worktree'
}
if (
  [string]$toolsSnapshot.launcher_script -cne [string]$toolsConfig.launcher_script -or
  [string]$toolsSnapshot.config_path -cne [string]$toolsConfig.config_path
) {
  throw 'Tools supervisor snapshot has an unexpected launcher or config path'
}
if ($toolsSnapshot.PSObject.Properties['executable']) {
  throw 'Tools supervisor snapshot must not pin an executable path'
}
[void](Read-NonEmptyFile -Path ([string]$toolsConfig.launcher_script) -Label 'Tools consumer wrapper')
[void](Read-NonEmptyFile -Path ([string]$toolsConfig.config_path) -Label 'Tools consumer config')
$bundleToolsConfig = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
if (
  (Get-FileHash -LiteralPath ([string]$toolsConfig.config_path) -Algorithm SHA256).Hash -cne
  (Get-FileHash -LiteralPath $bundleToolsConfig -Algorithm SHA256).Hash
) {
  throw 'Tools consumer config differs from the committed deployed bundle'
}
$toolsLive = @(Get-ToolsProcesses -ToolsConfig $toolsConfig -Processes $processes)
if ($toolsLive.Count -gt 1) {
  throw "duplicate supervisor-managed Tools consumers PID(s): $(@($toolsLive.ProcessId) -join ',')"
}
Write-Host ("  Tools consumer live count: {0}; supervisor and deliberate driver HOLD are exact" -f $toolsLive.Count)

Write-Host '  Grok model viability probe:'
$grokPreflight = @(
  & $resolver -DryRun -OutputDirectory ([string]$manifest.grok_output_directory)
)
$grokPreflightObjects = @(
  $grokPreflight | Where-Object {
    $_ -is [psobject] -and $_.PSObject.Properties['Model']
  }
)
if ($grokPreflightObjects.Count -eq 0) {
  throw 'Grok preflight returned no verified model record'
}
Write-Host ("    model: {0}" -f [string]$grokPreflightObjects[-1].Model)

if (-not $RunId) {
  $RunId = 'wd-reboot-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
}
if ($RunId -cnotmatch '^[A-Za-z0-9._-]{1,128}$') {
  throw 'RunId must match ^[A-Za-z0-9._-]{1,128}$'
}
$handshakeDirectory = Join-Path ([string]$manifest.handshake_root) $RunId
if (Test-Path -LiteralPath $handshakeDirectory) {
  throw "refusing to reuse an existing reboot handshake generation: $handshakeDirectory"
}
Write-Host '=== whole-fleet preflight passed ===' -ForegroundColor Green

Write-Host ''
Write-Host 'Update and launch plan:' -ForegroundColor Cyan
Write-Host ("  Codex: {0}" -f $(if ($SkipCliUpdate) { 'explicitly skipped' } else { 'codex update (once)' }))
Write-Host ("  Claude Code: {0}" -f $(if ($SkipCliUpdate) { 'explicitly skipped' } else { 'claude update (once)' }))
Write-Host '  Grok: resolve authenticated CLI provider default and write exact high-effort usage'
foreach ($state in $laneStates) {
  Write-Host ("  {0}: {1}" -f $state.lane.agent, $(if (@($state.live).Count -eq 1) { 'idempotent skip (already live)' } else { 'launch in WT' }))
}
Write-Host ("  codex-tools-1: {0}" -f $(if ($toolsLive.Count -eq 1) { 'supervisor-managed and live' } else { 'ask WD-Supervisor to restore' }))

if ($DryRun) {
  Write-Host ''
  Write-Host 'DRY RUN: no updates, file writes, task starts, WT tabs, or agent processes were started.' -ForegroundColor Yellow
  return
}

$mutex = [System.Threading.Mutex]::new(
  $false,
  'Global\WaggleDanceFleetRebootControlV2'
)
$mutexAcquired = $false
try {
  $mutexAcquired = $mutex.WaitOne(0)
  if (-not $mutexAcquired) {
    throw 'another WaggleDance fleet reboot-control run is already active'
  }

  if (-not $SkipCliUpdate) {
    Write-Host ''
    Write-Host 'Updating Codex CLI once...' -ForegroundColor Cyan
    [void](Invoke-CheckedNative -Path $codexPath -Arguments @('update') -Label 'codex update')
    Write-Host 'Updating Claude Code once...' -ForegroundColor Cyan
    [void](Invoke-CheckedNative -Path $claudePath -Arguments @('update') -Label 'claude update')
  }
  $codexAfterPath = Resolve-ApplicationPath -Name 'codex.cmd'
  $claudeAfterPath = Resolve-ApplicationPath -Name 'claude.cmd'
  Write-Host 'Verifying CLI versions after the update phase...' -ForegroundColor Cyan
  $codexAfterVersion = Invoke-CheckedNative `
    -Path $codexAfterPath `
    -Arguments @('--version') `
    -Label 'codex post-update version probe'
  $claudeAfterVersion = Invoke-CheckedNative `
    -Path $claudeAfterPath `
    -Arguments @('--version') `
    -Label 'claude post-update version probe'
  $cliVersionPath = 'C:\Python\WD_CLI_VERSIONS_CURRENT.json'
  $cliVersionTemporary = "$cliVersionPath.$PID.tmp"
  $cliVersionRecord = [ordered]@{
    schema_version = 1
    verified_at_utc = [DateTime]::UtcNow.ToString('o')
    update_status = $(if ($SkipCliUpdate) { 'operator_skipped' } else { 'completed' })
    codex = [ordered]@{
      before = $codexVersion
      after = $codexAfterVersion
      executable = $codexAfterPath
      update_command = 'codex update'
    }
    claude_code = [ordered]@{
      before = $claudeVersion
      after = $claudeAfterVersion
      executable = $claudeAfterPath
      update_command = 'claude update'
    }
  }
  try {
    $cliVersionRecord |
      ConvertTo-Json -Depth 6 |
      Set-Content -LiteralPath $cliVersionTemporary -Encoding UTF8
    Move-Item -LiteralPath $cliVersionTemporary -Destination $cliVersionPath -Force
  } finally {
    if (Test-Path -LiteralPath $cliVersionTemporary -PathType Leaf) {
      Remove-Item -LiteralPath $cliVersionTemporary -Force -ErrorAction SilentlyContinue
    }
  }

  Write-Host 'Resolving the current Grok model...' -ForegroundColor Cyan
  $grokResult = & $resolver -OutputDirectory ([string]$manifest.grok_output_directory)
  [void](Read-NonEmptyFile -Path ([string]$manifest.grok_markdown) -Label 'generated Grok model guide')
  if ($grokResult) {
    $grokObjects = @($grokResult | Where-Object { $_ -is [psobject] })
    if ($grokObjects.Count -gt 0 -and $grokObjects[-1].PSObject.Properties['Model']) {
      Write-Host ("  Grok model: {0}" -f [string]$grokObjects[-1].Model)
    }
  }

  if ($toolsLive.Count -eq 0) {
    Write-Host 'Restoring supervisor-managed Tools consumer...' -ForegroundColor Cyan
    Start-ScheduledTask -TaskName ([string]$toolsConfig.task_name)
    $toolsDeadline = (Get-Date).AddSeconds([int]$toolsConfig.wait_seconds)
    do {
      Start-Sleep -Milliseconds 500
      $toolsNow = @(Get-ToolsProcesses -ToolsConfig $toolsConfig -Processes (Get-AllProcessSnapshots))
    } while ($toolsNow.Count -eq 0 -and (Get-Date) -lt $toolsDeadline)
    if ($toolsNow.Count -ne 1) {
      throw "Tools supervisor did not establish exactly one consumer; live count=$($toolsNow.Count)"
    }
  }

  [void](New-Item -ItemType Directory -Path $handshakeDirectory -Force)
  $launchStartedUtc = [DateTimeOffset]::UtcNow
  $launched = @()
  foreach ($state in $laneStates) {
    if (@($state.live).Count -eq 1) {
      Write-Host ("Skipping already-live lane {0}" -f $state.lane.agent)
      continue
    }
    $wtArguments = @(
      '-w', 'new',
      'new-tab',
      '--title', [string]$state.lane.agent,
      '--suppressApplicationTitle',
      '-d', [string]$state.lane.worktree,
      'powershell.exe',
      '-NoProfile',
      '-ExecutionPolicy', 'Bypass',
      '-File', $agentLauncher,
      '-ManifestPath', $ManifestPath,
      '-Agent', [string]$state.lane.agent,
      '-RunId', $RunId,
      '-HandshakeDirectory', $handshakeDirectory
    )
    Write-Host ("Launching {0}..." -f $state.lane.agent) -ForegroundColor Cyan
    [void](Start-Process -FilePath $wtPath -ArgumentList $wtArguments -PassThru)
    $launched += [string]$state.lane.agent
  }

  if ($launched.Count -gt 0) {
    $deadline = (Get-Date).AddSeconds($HandshakeTimeoutSeconds)
    $pending = @($launched)
    do {
      $pending = @($pending | Where-Object {
        -not (Test-Path -LiteralPath (
          Join-Path $handshakeDirectory ("{0}.json" -f $_)
        ) -PathType Leaf)
      })
      if ($pending.Count -gt 0) {
        Start-Sleep -Milliseconds 250
      }
    } while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($pending.Count -gt 0) {
      throw "bridge bootstrap handshake timed out for: $($pending -join ', ')"
    }
    foreach ($launchedAgent in $launched) {
      $lane = @(
        $manifest.lanes |
          Where-Object { [string]$_.agent -ceq [string]$launchedAgent }
      )[0]
      $handshakePath = Join-Path $handshakeDirectory ("{0}.json" -f $launchedAgent)
      $handshake = Get-Content -LiteralPath $handshakePath -Raw |
        ConvertFrom-Json -ErrorAction Stop
      if (
        [int]$handshake.schema_version -ne 1 -or
        [string]$handshake.status -cne 'bridge_bootstrapped' -or
        [string]$handshake.agent -cne [string]$lane.agent -or
        [string]$handshake.agent_uuid -cne [string]$lane.agent_uuid -or
        [string]$handshake.role -cne [string]$lane.role -or
        [string]$handshake.run_id -cne $RunId -or
        [string]$handshake.session_id -cne $RunId -or
        [string]$handshake.branch -cne [string]$lane.branch -or
        [string]$handshake.head -cne [string]$lane.head -or
        -not ([string]$handshake.worktree).Equals(
          [string]$lane.worktree,
          [System.StringComparison]::OrdinalIgnoreCase
        )
      ) {
        throw "bridge bootstrap handshake identity mismatch for $launchedAgent"
      }
      $createdUtc = [DateTimeOffset]::MinValue
      if (
        -not [DateTimeOffset]::TryParse(
          [string]$handshake.created_at_utc,
          [Globalization.CultureInfo]::InvariantCulture,
          [Globalization.DateTimeStyles]::AssumeUniversal,
          [ref]$createdUtc
        ) -or
        $createdUtc.ToUniversalTime() -lt $launchStartedUtc.AddSeconds(-5)
      ) {
        throw "bridge bootstrap handshake is stale or malformed for $launchedAgent"
      }
      $handshakePid = 0
      if (
        -not [int]::TryParse([string]$handshake.pid, [ref]$handshakePid) -or
        $handshakePid -le 0 -or
        $null -eq (Get-Process -Id $handshakePid -ErrorAction SilentlyContinue)
      ) {
        throw "bridge bootstrap handshake process is not alive for $launchedAgent"
      }
    }
  }

  Write-Host ''
  Write-Host ("Fleet restore complete; run_id={0}" -f $RunId) -ForegroundColor Green
  Write-Host ("  interactive lanes launched: {0}" -f $(if ($launched.Count) { $launched -join ', ' } else { 'none (all already live)' }))
  Write-Host '  Tools: supervisor-managed'
  Write-Host '  Merge driver: deliberate Disabled/HOLD containment preserved'
  Write-Host ("  CLI versions: Codex {0} -> {1}; Claude {2} -> {3}" -f $codexVersion, $codexAfterVersion, $claudeVersion, $claudeAfterVersion)
} finally {
  if ($mutexAcquired) {
    [void]$mutex.ReleaseMutex()
  }
  $mutex.Dispose()
}

#requires -Version 5.1
<#
.SYNOPSIS
  Starts one integrity-pinned WaggleDance interactive or managed bridge lane.

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
  [string] $ExternalSessionsPath = '',
  [string] $ExternalSessionsHash = '',
  [switch] $DryRun,
  [switch] $CheckManagedAdmission,
  [switch] $RecoverInteractive,
  [switch] $RetireManagedAttempt,
  [string] $ReviewedJournalDigest = '',
  [string] $RetirementReason = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($CheckManagedAdmission -and -not $DryRun) {
  throw 'CheckManagedAdmission requires -DryRun'
}
if ($RecoverInteractive -and $RetireManagedAttempt) {
  throw 'RecoverInteractive and RetireManagedAttempt are mutually exclusive'
}
if ($CheckManagedAdmission -and ($RecoverInteractive -or $RetireManagedAttempt)) {
  throw 'manual Lead recovery cannot be combined with managed admission probing'
}
if (-not $RetireManagedAttempt -and ($ReviewedJournalDigest -or $RetirementReason)) {
  throw 'retirement digest and reason require -RetireManagedAttempt'
}
if ($RetireManagedAttempt -and (
    $ReviewedJournalDigest -cnotmatch '^[0-9A-Fa-f]{64}$' -or
    [string]::IsNullOrWhiteSpace($RetirementReason) -or
    $RetirementReason.Length -gt 512 -or
    $RetirementReason -match '[\x00-\x1F\x7F]')) {
  throw 'RetireManagedAttempt requires a 64-hex reviewed digest and a 1-512 character reason without control characters'
}
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
  $separator = [IO.Path]::DirectorySeparatorChar
  $comparison = if ($separator -eq '\') {
    [StringComparison]::OrdinalIgnoreCase
  } else { [StringComparison]::Ordinal }
  $candidate = [IO.Path]::GetFullPath($Path)
  if (-not $candidate.Equals([IO.Path]::GetPathRoot($candidate), $comparison)) {
    $candidate = $candidate.TrimEnd($separator)
  }
  $rootCandidate = [IO.Path]::GetFullPath($TrustedRoot)
  $root = if ($rootCandidate.Equals(
      [IO.Path]::GetPathRoot($rootCandidate),
      $comparison
    )) { $rootCandidate } else { $rootCandidate.TrimEnd($separator) }
  $rootPrefix = $root.TrimEnd($separator) + $separator
  if (
    -not $candidate.Equals($root, $comparison) -and
    -not $candidate.StartsWith(
      $rootPrefix,
      $comparison
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
  $relative = $candidate.Substring($root.Length).TrimStart($separator)
  $current = $root
  foreach ($segment in @($relative.Split([char[]]@($separator), [StringSplitOptions]::RemoveEmptyEntries))) {
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

function Get-WdLaneTurnMode {
  param([Parameter(Mandatory)] [object] $Lane)

  $property = $Lane.PSObject.Properties['turn_mode']
  if ($null -eq $property) { return 'interactive' }
  $mode = [string]$property.Value
  if ($mode -cnotin @('interactive', 'managed')) {
    throw "unsupported lane turn_mode '$mode'"
  }
  return $mode
}

function Get-WdLaneConversationSurface {
  param([Parameter(Mandatory)] [object] $Lane)

  $property = $Lane.PSObject.Properties['conversation_surface']
  if ($null -eq $property) { return 'none' }
  $surface = [string]$property.Value
  if ($surface -cnotin @('none', 'local_window')) {
    throw "unsupported lane conversation_surface '$surface'"
  }
  if ($surface -ceq 'local_window' -and (
      [string]$Lane.agent -cne 'codex-lead-1' -or
      (Get-WdLaneTurnMode -Lane $Lane) -cne 'managed')) {
    throw 'local conversation control requires the managed Codex Lead lane'
  }
  return $surface
}

function Get-WdLaneConversationPermissions {
  param([Parameter(Mandatory)] [object] $Lane)

  $property = $Lane.PSObject.Properties['conversation_permissions']
  if ($null -eq $property) {
    return @{ NetworkAccess = $false; AdditionalWritableRoots = @(); CodexPermissionPosture = 'workspace_write' }
  }
  $policy = $property.Value
  if ($null -eq $policy -or $policy -isnot [pscustomobject] -or
      @($policy.PSObject.Properties.Name | Where-Object {
        $_ -cnotin @('posture', 'network_access', 'additional_writable_roots')
      }).Count -gt 0 -or
      $null -eq $policy.PSObject.Properties['network_access'] -or
      $policy.network_access -isnot [bool] -or
      $null -eq $policy.PSObject.Properties['additional_writable_roots'] -or
      $policy.additional_writable_roots -isnot [array]) {
    throw 'conversation permissions must explicitly contain a boolean network_access and array additional_writable_roots'
  }
  foreach ($root in @($policy.additional_writable_roots)) {
    if ($root -isnot [string] -or [string]::IsNullOrWhiteSpace($root)) {
      throw 'conversation writable roots must be nonempty strings'
    }
  }
  $postureProperty = $policy.PSObject.Properties['posture']
  $posture = if ($null -eq $postureProperty) { 'workspace_write' } else { [string]$postureProperty.Value }
  if ($posture -cnotin @('workspace_write', 'existing_interactive')) {
    throw 'unsupported conversation permission posture'
  }
  if ($posture -ceq 'existing_interactive' -and (
      $null -eq $Lane.PSObject.Properties['agent'] -or [string]$Lane.agent -cne 'codex-lead-1' -or
      -not $policy.network_access -or @($policy.additional_writable_roots).Count -ne 0)) {
    throw 'existing_interactive is an explicit Lead-only full-access compatibility posture, not scoped writable roots'
  }
  # The backend validates existence, C-drive containment and every path component
  # for reparse points before starting the native process. Policy bytes are pinned
  # by the deployment manifest; filesystem access is not task/claim authority.
  return @{ NetworkAccess = $policy.network_access; CodexPermissionPosture = $posture
    AdditionalWritableRoots = @($policy.additional_writable_roots) }
}

function Get-WdCodexSecurityFingerprint {
  param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
  $normalized = $Text.Replace("`r`n", "`n")
  # This deliberately is not a TOML parser. Multiline strings could contain
  # apparent table headers; refuse ambiguous syntax rather than omit policy.
  if ($normalized.Contains("`r") -or
      $normalized.Contains(([string][char]34) * 3) -or
      $normalized.Contains(([string][char]39) * 3)) {
    throw 'Ambiguous Codex configuration requires explicit security-baseline review'
  }
  $securityText = [Text.StringBuilder]::new()
  $ignoreUiTable = $false
  foreach ($line in [regex]::Matches($normalized, '[^\n]*(?:\n|$)')) {
    if ($line.Length -eq 0) { continue }
    $trimmed = $line.Value.Trim()
    if ($trimmed.StartsWith('[')) {
      # Only these exact canonical table headers are cosmetic. Any other
      # header, including child/quoted/array tables, ends the exclusion.
      $ignoreUiTable = $trimmed -ceq '[notice]' -or $trimmed -ceq '[tui.model_availability_nux]'
    }
    if (-not $ignoreUiTable) { [void]$securityText.Append($line.Value) }
  }
  $sha = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($securityText.ToString())))).Replace('-', '') }
  finally { $sha.Dispose() }
}

function Assert-WdLeadInteractivePostureBaseline {
  param([Parameter(Mandatory)] [object] $Lane,
    [Parameter(Mandatory)] [string] $Worktree,
    [Parameter(Mandatory)] [string] $UserConfigPath)

  $property = $Lane.PSObject.Properties['conversation_config_baseline']
  if ($null -eq $property -or $null -eq $property.Value) {
    throw 'Lead compatibility requires a reviewed user-config baseline'
  }
  $baseline = $property.Value
  $path = [IO.Path]::GetFullPath($UserConfigPath)
  if (-not $path.Equals([string]$baseline.path, [StringComparison]::OrdinalIgnoreCase) -or
      [string]$baseline.security_sha256 -cnotmatch '^[0-9A-F]{64}$') {
    throw 'Lead user-config location differs from its reviewed baseline'
  }
  [void](Assert-LanePathWithoutReparse -Path $path -TrustedRoot ([IO.Path]::GetPathRoot($path)) -ExpectedType Leaf)
  $snapshot = Read-Utf8LaneSnapshot -Path $path
  $securityHash = Get-WdCodexSecurityFingerprint -Text ([string]$snapshot.Text)
  if ($securityHash -cne [string]$baseline.security_sha256) {
    throw 'Codex user configuration changed; review the permission baseline before managed Lead startup'
  }
  # Deliberately accept only the reviewed simple top-level declarations. This is
  # not a general TOML parser or a fallback for a profile/layered configuration.
  $top = ([string]$snapshot.Text -split '(?m)^\s*\[')[0]
  foreach ($pair in @(@('approval_policy','never'), @('sandbox_mode','danger-full-access'))) {
    $keyMatches = [regex]::Matches($top, ('(?m)^\s*' + $pair[0] + '\s*=\s*["'']' + $pair[1] + '["'']\s*(?:#[^\r\n]*)?\r?$'))
    if ($keyMatches.Count -ne 1) { throw 'Reviewed Codex configuration no longer states full-access/never at top level' }
  }
  $directory = [IO.DirectoryInfo]::new([IO.Path]::GetFullPath($Worktree))
  while ($null -ne $directory) {
    if (Test-Path -LiteralPath (Join-Path $directory.FullName '.codex\config.toml')) {
      throw 'Unreviewed project Codex configuration blocks full-access compatibility; do not override operator tightening'
    }
    $directory = $directory.Parent
  }
  return [pscustomobject]@{ path=$path; sha256=[string]$snapshot.Hash
    security_sha256=$securityHash
    approval_policy='never'; sandbox_mode='danger-full-access' }
}

function Read-WdLaneTurnRunnerSnapshot {
  param(
    [Parameter(Mandatory)] [string] $ScriptRoot,
    [ValidateSet('Invoke-WdLaneTurnLoop.ps1', 'Invoke-WdCodexConversationLoop.ps1', 'Show-WdOperatorConversation.ps1')]
    [string] $FileName = 'Invoke-WdLaneTurnLoop.ps1',
    [AllowNull()] [object] $DeploymentAnchor = $null,
    [switch] $SourceTreeMode
  )

  $runnerPath = Join-Path $ScriptRoot $FileName
  [void](Assert-LanePathWithoutReparse `
    -Path $runnerPath `
    -TrustedRoot ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($ScriptRoot))) `
    -ExpectedType Leaf)
  $snapshot = Read-Utf8LaneSnapshot -Path $runnerPath
  if ($null -eq $DeploymentAnchor) {
    if (-not $SourceTreeMode) {
      throw 'lane turn runner requires an anchored deployment manifest'
    }
  } else {
    $pin = $DeploymentAnchor.files.PSObject.Properties[$FileName]
    if ($null -eq $pin -or [string]$snapshot.Hash -cne [string]$pin.Value) {
      throw 'lane turn runner bundle hash mismatch'
    }
  }
  return $snapshot
}

function Assert-WdLaneLaunchAvailable {
  param(
    [Parameter(Mandatory)] [object] $Lane,
    [object[]] $KnownLanes = @(),
    [int] $CurrentPid = $PID,
    [object[]] $ExternalSessions = @()
  )

  $agentPattern = '(?i)(?:^|\s)-Agent\s+["'']?' +
    [regex]::Escape([string]$Lane.agent) + '["'']?(?=\s|$)'
  $filePattern = '(?i)(?:^|\s)-File\s+(?:"(?<path>[^"]+)"|''(?<path>[^'']+)''|(?<path>\S+))(?=\s|$)'
  $agentCapturePattern = '(?i)(?:^|\s)-Agent\s+["'']?(?<agent>[a-z][a-z0-9_-]{1,32})["'']?(?=\s|$)'
  $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  $byPid = @{}
  $launcherOwners = @{}
  $knownAgents = @('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
  foreach ($process in $processes) {
    $processIdValue = [int]$process.ProcessId
    if ($byPid.ContainsKey($processIdValue)) { throw 'cannot prove lane ownership: duplicate PID in process snapshot' }
    $byPid[$processIdValue] = $process
  }
  foreach ($process in $processes) {
    if ([int]$process.ProcessId -eq $CurrentPid) { continue }
    if ([string]$process.Name -notmatch '^(powershell|pwsh)\.exe$') { continue }
    if ([string]::IsNullOrWhiteSpace([string]$process.CommandLine)) {
      throw 'cannot prove lane ownership: PowerShell command line is unavailable'
    }
    $fileMatch = [regex]::Match([string]$process.CommandLine, $filePattern)
    if (-not $fileMatch.Success) { continue }
    # These are Win32 command lines even when a readonly test runs on POSIX.
    $leaf = [IO.Path]::GetFileName(($fileMatch.Groups['path'].Value -split '\\')[-1])
    $sameLane = (
      $leaf -ieq 'start-wd-agent.ps1' -and
      [string]$process.CommandLine -match $agentPattern
    ) -or @($Lane.legacy_process_markers) -icontains $leaf
    if ($sameLane) {
      throw "lane '$($Lane.agent)' already has a live launcher (PID $($process.ProcessId)); leave its session running"
    }
    $ownerAgent = ''
    $agentMatches = @([regex]::Matches([string]$process.CommandLine, $agentCapturePattern))
    if ($leaf -ieq 'start-wd-agent.ps1' -and $agentMatches.Count -eq 1) {
      $candidateAgent = $agentMatches[0].Groups['agent'].Value.ToLowerInvariant()
      if ($candidateAgent -cin $knownAgents) { $ownerAgent = $candidateAgent }
    }
    foreach ($knownLane in @($KnownLanes) + @($Lane)) {
      if (@($knownLane.legacy_process_markers) -icontains $leaf) {
        if ($ownerAgent -and $ownerAgent -cne [string]$knownLane.agent) { throw 'ambiguous legacy launcher identity' }
        $ownerAgent = [string]$knownLane.agent
      }
    }
    if ($leaf -iin @('start-wd-tools-consumer.ps1','Invoke-WdToolsCodex.ps1')) {
      $ownerAgent = 'codex-tools-1'
    } elseif ($leaf -ieq 'Start-AgentBridgeConsumerLoop.ps1' -and $agentMatches.Count -eq 1 -and
      $agentMatches[0].Groups['agent'].Value -ceq 'codex-tools-1') {
      $ownerAgent = 'codex-tools-1'
    }
    if ($ownerAgent) { $launcherOwners[[int]$process.ProcessId] = $ownerAgent }
  }
  # Updaters can rename an executable while its existing native process lives.
  # Recognize only the installed CLI names and their numeric old-file suffix.
  foreach ($native in @($processes | Where-Object { [string]$_.Name -imatch '^(codex|claude)\.exe(?:\.old\.[0-9]+)?$' })) {
    $node = $native
    $visited = @{}
    $attributed = $false
    for ($depth = 0; $depth -lt 64; $depth++) {
      $nodePid = [int]$node.ProcessId
      if ($visited.ContainsKey($nodePid)) { break }
      $visited[$nodePid] = $true
      $parentProperty = $node.PSObject.Properties['ParentProcessId']
      if ($null -eq $parentProperty -or -not $byPid.ContainsKey([int]$parentProperty.Value)) { break }
      $parent = $byPid[[int]$parentProperty.Value]
      $childStart = $node.PSObject.Properties['CreationDate']
      $parentStart = $parent.PSObject.Properties['CreationDate']
      if ($null -eq $childStart -or $null -eq $parentStart -or $null -eq $childStart.Value -or $null -eq $parentStart.Value) { break }
      try {
        if ([DateTimeOffset]$parentStart.Value -gt [DateTimeOffset]$childStart.Value) { break }
      } catch { break }
      if ($launcherOwners.ContainsKey([int]$parent.ProcessId)) {
        if ([string]$launcherOwners[[int]$parent.ProcessId] -ceq [string]$Lane.agent) {
          throw "lane '$($Lane.agent)' already owns native CLI PID $($native.ProcessId); leave its session running"
        }
        $attributed = $true
        break
      }
      $node = $parent
    }
    if (-not $attributed) {
      # Explicit operator attribution is scoped to this exact process lifetime.
      # A PID alone, another executable, or a known same-lane ancestor never
      # qualifies. The snapshot is hash-pinned by the fleet invocation.
      $external = @($ExternalSessions | Where-Object {
        [int]$_.pid -eq [int]$native.ProcessId -and
        [string]$_.name -ceq [string]$native.Name -and
        [string]$_.command_line -ceq [string]$native.CommandLine -and
        [string]$_.executable_path -ceq [string]$native.ExecutablePath -and
        ([DateTimeOffset]$_.process_start_utc).UtcTicks -eq
          ([DateTimeOffset]$native.CreationDate).UtcTicks
      })
      if ($external.Count -eq 1) { continue }
      throw "cannot prove lane availability: unmarked native $($native.Name) PID $($native.ProcessId) has no verified launcher ancestry; leave it running"
    }
  }
}

function Read-WdExternalSessions {
  param([string] $Path, [string] $ExpectedHash)
  if (-not $Path -and -not $ExpectedHash) { return }
  if (-not [IO.Path]::IsPathRooted($Path) -or $ExpectedHash -cnotmatch '^[A-Fa-f0-9]{64}$') {
    throw 'external sessions require an absolute snapshot path and SHA256'
  }
  [void](Assert-LanePathWithoutReparse -Path $Path -TrustedRoot ([IO.Path]::GetPathRoot($Path)) -ExpectedType Leaf)
  if ((Get-Item -LiteralPath $Path).Length -gt 32768) { throw 'external session snapshot too large' }
  $snapshot = Read-Utf8LaneSnapshot -Path $Path
  if ($snapshot.Hash -cne $ExpectedHash.ToUpperInvariant()) { throw 'external session snapshot hash mismatch' }
  $jsonParameters = @{ InputObject = $snapshot.Text; ErrorAction = 'Stop' }
  if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
    $jsonParameters.DateKind = 'String'
  }
  $record = ConvertFrom-Json @jsonParameters
  if ($record.schema -cne 'wd.external-agent-sessions.v1' -or
      ([DateTimeOffset]$record.expires_at_utc) -le [DateTimeOffset]::UtcNow -or
      ([DateTimeOffset]$record.expires_at_utc) -gt [DateTimeOffset]::UtcNow.AddHours(24)) {
    throw 'external session snapshot invalid or expired'
  }
  $seen = @{}
  foreach ($entry in @($record.processes)) {
    if ([int]$entry.pid -le 0 -or $seen.ContainsKey([int]$entry.pid) -or
        [string]$entry.name -cnotin @('codex.exe','claude.exe') -or
        [string]::IsNullOrWhiteSpace([string]$entry.command_line) -or
        -not [IO.Path]::IsPathRooted([string]$entry.executable_path) -or
        [string]$entry.process_start_utc -notmatch '(Z|[+-]\d{2}:\d{2})$') {
      throw 'invalid or duplicate external process identity'
    }
    [void][DateTimeOffset]::Parse([string]$entry.process_start_utc)
    $seen[[int]$entry.pid] = $true
    $entry
  }
}

function Get-WdManagedAttemptInventory {
  param(
    [Parameter(Mandatory)] [string] $PointerPath,
    [Parameter(Mandatory)] [string] $JournalPath
  )

  $pointer = [IO.Path]::GetFullPath($PointerPath)
  $journal = [IO.Path]::GetFullPath($JournalPath).TrimEnd('\')
  [void](Assert-LanePathWithoutReparse -Path $pointer `
    -TrustedRoot ([IO.Path]::GetPathRoot($pointer)) -ExpectedType Leaf)
  [void](Assert-LanePathWithoutReparse -Path $journal `
    -TrustedRoot ([IO.Path]::GetPathRoot($journal)) -ExpectedType Directory)

  $files = [Collections.ArrayList]::new()
  $directories = [Collections.Generic.Stack[string]]::new()
  $directories.Push($journal)
  $directoryCount = 0
  while ($directories.Count -gt 0) {
    $directoryPath = $directories.Pop()
    [void](Assert-LanePathWithoutReparse -Path $directoryPath `
      -TrustedRoot ([IO.Path]::GetPathRoot($journal)) -ExpectedType Directory)
    $directoryCount++
    if ($directoryCount -gt 4096) {
      throw 'managed attempt journal exceeds the directory traversal bound'
    }
    foreach ($child in @(Get-ChildItem -LiteralPath $directoryPath -Force -ErrorAction Stop)) {
      if ($child.PSIsContainer) {
        # Validate before adding the child to the traversal stack. A recursive
        # provider enumeration could otherwise cross a junction before refusal.
        [void](Assert-LanePathWithoutReparse -Path $child.FullName `
          -TrustedRoot ([IO.Path]::GetPathRoot($journal)) -ExpectedType Directory)
        $directories.Push([string]$child.FullName)
      } else {
        [void]$files.Add($child)
      }
    }
  }
  if ($files.Count -lt 1 -or $files.Count -gt 4096) {
    throw 'managed attempt journal must contain 1-4096 files'
  }

  $records = @{}
  $totalBytes = [int64]0
  $inputs = @([pscustomobject]@{
      LogicalPath = 'runtime-owner-pointer.json'; FullName = $pointer
    })
  foreach ($file in $files) {
    [void](Assert-LanePathWithoutReparse -Path $file.FullName `
      -TrustedRoot ([IO.Path]::GetPathRoot($journal)) -ExpectedType Leaf)
    $relative = $file.FullName.Substring($journal.Length).TrimStart('\').Replace('\', '/')
    if ([string]::IsNullOrWhiteSpace($relative) -or $relative -match '[\x00-\x1F\x7F]') {
      throw 'managed attempt journal contains an unsafe relative path'
    }
    $inputs += [pscustomobject]@{ LogicalPath = "journal/$relative"; FullName = $file.FullName }
  }
  foreach ($input in $inputs) {
    $length = [int64](Get-Item -LiteralPath ([string]$input.FullName) -Force -ErrorAction Stop).Length
    if ($length -lt 0 -or $length -gt (67108864 - $totalBytes)) {
      throw 'managed attempt evidence exceeds 64 MiB'
    }
    $bytes = [IO.File]::ReadAllBytes([string]$input.FullName)
    if ($bytes.LongLength -ne $length) {
      throw 'managed attempt evidence changed while it was being read'
    }
    $totalBytes += $bytes.LongLength
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '') }
    finally { $sha.Dispose() }
    if ($records.ContainsKey([string]$input.LogicalPath)) {
      throw 'managed attempt journal contains duplicate logical paths'
    }
    $records[[string]$input.LogicalPath] = [pscustomobject]@{
      path = [string]$input.LogicalPath
      length = [int64]$bytes.LongLength
      sha256 = $hash
    }
  }
  $paths = [string[]]@($records.Keys)
  [Array]::Sort($paths, [StringComparer]::Ordinal)
  $canonical = [Text.StringBuilder]::new('wd.managed-attempt-review.v1' + "`n")
  $entries = @()
  foreach ($path in $paths) {
    $entry = $records[$path]
    [void]$canonical.Append($entry.path).Append("`t").Append($entry.length).Append("`t").Append($entry.sha256).Append("`n")
    $entries += $entry
  }
  $digestAlgorithm = [Security.Cryptography.SHA256]::Create()
  try {
    $digest = ([BitConverter]::ToString($digestAlgorithm.ComputeHash(
      [Text.Encoding]::UTF8.GetBytes($canonical.ToString())
    ))).Replace('-', '')
  } finally { $digestAlgorithm.Dispose() }
  return [pscustomobject]@{
    schema = 'wd.managed-attempt-review.v1'
    digest = $digest
    total_bytes = $totalBytes
    entries = @($entries)
  }
}

function Assert-WdManagedOwnerInactive {
  param(
    [Parameter(Mandatory)] [object] $Owner,
    [Parameter(Mandatory)] [AllowNull()] [AllowEmptyCollection()] [object[]] $ProcessSnapshot
  )

  $byPid = @{}
  foreach ($process in @($ProcessSnapshot | Where-Object { $null -ne $_ })) {
    $processId = [int]$process.ProcessId
    if ($processId -eq 0) { continue }
    if ($processId -lt 0 -or $byPid.ContainsKey($processId)) {
      throw 'cannot prove old managed owner inactivity from the process snapshot'
    }
    $byPid[$processId] = $process
  }
  foreach ($binding in @(
      [pscustomobject]@{ PidName='pid'; StartName='process_start_utc'; Label='managed owner' },
      [pscustomobject]@{ PidName='child_pid'; StartName='native_process_start_utc'; Label='managed native child' }
    )) {
    $pidProperty = $Owner.PSObject.Properties[[string]$binding.PidName]
    if ($null -eq $pidProperty -or $null -eq $pidProperty.Value -or [int]$pidProperty.Value -le 0) {
      if ([string]$binding.PidName -ceq 'pid') { throw 'managed owner PID is missing' }
      continue
    }
    $boundPid = [int]$pidProperty.Value
    if (-not $byPid.ContainsKey($boundPid)) { continue }
    $startProperty = $Owner.PSObject.Properties[[string]$binding.StartName]
    $processStart = $byPid[$boundPid].PSObject.Properties['CreationDate']
    if ($null -eq $startProperty -or [string]::IsNullOrWhiteSpace([string]$startProperty.Value) -or
        $null -eq $processStart -or $null -eq $processStart.Value) {
      throw "cannot disambiguate live or reused $($binding.Label) PID $boundPid"
    }
    try {
      if ($startProperty.Value -is [DateTime] -or $startProperty.Value -is [DateTimeOffset]) {
        $recordedStart = ([DateTimeOffset]$startProperty.Value).ToUniversalTime()
      } else {
        $recordedStart = [DateTimeOffset]::ParseExact(
          [string]$startProperty.Value, 'o', [Globalization.CultureInfo]::InvariantCulture
        ).ToUniversalTime()
      }
      $observedStart = ([DateTimeOffset]$processStart.Value).ToUniversalTime()
    } catch {
      throw "cannot parse $($binding.Label) creation time for PID $boundPid"
    }
    if ($recordedStart.Ticks -eq $observedStart.Ticks) {
      throw "$($binding.Label) PID $boundPid is still live"
    }
  }
}

function Get-WdManagedAttemptEvidence {
  param(
    [Parameter(Mandatory)] [string] $Agent,
    [Parameter(Mandatory)] [string] $Worktree,
    [Parameter(Mandatory)] [string] $RuntimeRoot,
    [AllowNull()] [AllowEmptyCollection()] [object[]] $ProcessSnapshot
  )

  if ($Agent -cne 'codex-lead-1') {
    throw 'manual managed-attempt recovery is Lead-only'
  }
  $worktreeFull = [IO.Path]::GetFullPath($Worktree).TrimEnd('\')
  $runtimeFull = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
  [void](Assert-LanePathWithoutReparse -Path $worktreeFull `
    -TrustedRoot ([IO.Path]::GetPathRoot($worktreeFull)) -ExpectedType Directory)
  [void](Assert-LanePathWithoutReparse -Path $runtimeFull `
    -TrustedRoot ([IO.Path]::GetPathRoot($runtimeFull)) -ExpectedType Directory)
  $pointer = Join-Path $runtimeFull ".wd-turn-$Agent.owner.json"
  $journal = Join-Path $worktreeFull '.codex-audit\wd-turn-loop'
  [void](Assert-LanePathWithoutReparse -Path $pointer `
    -TrustedRoot ([IO.Path]::GetPathRoot($runtimeFull)) -ExpectedType Leaf)
  [void](Assert-LanePathWithoutReparse -Path $journal `
    -TrustedRoot ([IO.Path]::GetPathRoot($worktreeFull)) -ExpectedType Directory)
  $pointerSnapshot = Read-Utf8LaneSnapshot -Path $pointer
  if ([Text.Encoding]::UTF8.GetByteCount([string]$pointerSnapshot.Text) -gt 32768) {
    throw 'managed owner pointer exceeds 32 KiB'
  }
  $jsonCommand = Get-Command ConvertFrom-Json -ErrorAction Stop
  $owner = if ($jsonCommand.Parameters.ContainsKey('DateKind')) {
    ConvertFrom-Json -InputObject ([string]$pointerSnapshot.Text) -DateKind String -ErrorAction Stop
  } else {
    ConvertFrom-Json -InputObject ([string]$pointerSnapshot.Text) -ErrorAction Stop
  }
  if ([string]$owner.schema -cne 'wd.lane-turn-owner.v1' -or
      [string]$owner.agent -cne $Agent -or
      [string]$owner.session_id -cnotmatch '^[A-Za-z0-9._:-]{1,128}$' -or
      [string]$owner.generation -cnotmatch '^[0-9a-f]{40}$' -or
      -not ([string]$owner.worktree).Equals($worktreeFull, [StringComparison]::OrdinalIgnoreCase) -or
      -not ([string]$owner.journal_root).Equals($journal, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'managed owner identity, worktree, journal or generation is invalid'
  }
  $journalOwner = Join-Path $journal 'owner.json'
  [void](Assert-LanePathWithoutReparse -Path $journalOwner `
    -TrustedRoot ([IO.Path]::GetPathRoot($worktreeFull)) -ExpectedType Leaf)
  if ((Read-Utf8LaneSnapshot -Path $journalOwner).Hash -cne [string]$pointerSnapshot.Hash) {
    throw 'runtime and journal owner records differ; manual recovery cannot choose one'
  }
  $pendingFiles = @(Get-ChildItem -LiteralPath $journal -Filter '*.pending' -File -Force)
  if ($pendingFiles.Count -ne 1 -or
      [string]$owner.pending_path -cnotmatch 'turn-[0-9a-f]{32}\.pending$') {
    throw 'manual recovery requires exactly one owner-bound unresolved pending record'
  }
  $pendingPath = [IO.Path]::GetFullPath([string]$owner.pending_path)
  if (-not $pendingPath.Equals($pendingFiles[0].FullName, [StringComparison]::OrdinalIgnoreCase) -or
      -not (Split-Path -Parent $pendingPath).Equals($journal, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'managed pending record is outside or differs from the owner journal'
  }
  $processes = if ($PSBoundParameters.ContainsKey('ProcessSnapshot')) {
    @($ProcessSnapshot | Where-Object { $null -ne $_ })
  } else { @(Get-CimInstance Win32_Process -ErrorAction Stop) }
  Assert-WdManagedOwnerInactive -Owner $owner -ProcessSnapshot $processes
  $inventory = Get-WdManagedAttemptInventory -PointerPath $pointer -JournalPath $journal
  return [pscustomobject]@{
    schema = [string]$inventory.schema
    agent = $Agent
    worktree = $worktreeFull
    runtime_root = $runtimeFull
    pointer_path = $pointer
    journal_path = $journal
    digest = [string]$inventory.digest
    total_bytes = [int64]$inventory.total_bytes
    entries = @($inventory.entries)
    pending_count = $pendingFiles.Count
    pending_path = $pendingPath
    owner = $owner
  }
}

function Enter-WdManagedAttemptLease {
  param(
    [Parameter(Mandatory)] [string] $RuntimeRoot,
    [Parameter(Mandatory)] [string] $Agent
  )

  $runtimeFull = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
  $lockPath = Join-Path $runtimeFull ".wd-turn-$Agent.lock"
  [void](Assert-LanePathWithoutReparse -Path $lockPath `
    -TrustedRoot ([IO.Path]::GetPathRoot($runtimeFull)) -ExpectedType Leaf)
  try {
    return [IO.File]::Open($lockPath, [IO.FileMode]::Open,
      [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
  } catch [IO.IOException] {
    throw "managed lane lease is held or unavailable; leave the existing owner untouched: $($_.Exception.Message)"
  }
}

function Assert-WdTrustedInteractiveBoundary {
  param(
    [Parameter(Mandatory)] [object] $BoundaryProcess,
    [Parameter(Mandatory)] [object] $InvokerProcess
  )

  if ([string]$BoundaryProcess.Name -cne 'WindowsTerminal.exe' -or
      $null -eq $BoundaryProcess.PSObject.Properties['ExecutablePath'] -or
      [string]::IsNullOrWhiteSpace([string]$BoundaryProcess.ExecutablePath)) {
    throw 'incomplete ancestry did not terminate at a verified Windows Terminal'
  }
  $terminalPath = [IO.Path]::GetFullPath([string]$BoundaryProcess.ExecutablePath)
  if ($terminalPath -cnotmatch '(?i)^C:\\Program Files\\WindowsApps\\Microsoft\.WindowsTerminal_[^\\]+_x64__8wekyb3d8bbwe\\WindowsTerminal\.exe$') {
    throw 'interactive boundary is not the installed Microsoft Windows Terminal package'
  }
  [void](Assert-LanePathWithoutReparse -Path $terminalPath `
    -TrustedRoot ([IO.Path]::GetPathRoot($terminalPath)) -ExpectedType Leaf)
  $signature = Get-AuthenticodeSignature -LiteralPath $terminalPath -ErrorAction Stop
  if ([string]$signature.Status -cne 'Valid' -or
      $null -eq $signature.SignerCertificate -or
      [string]$signature.SignerCertificate.Subject -cnotmatch '(?:^|,\s*)O=Microsoft Corporation(?:,|$)') {
    throw 'interactive boundary does not have a valid Microsoft signature'
  }
  $boundarySession = $BoundaryProcess.PSObject.Properties['SessionId']
  $invokerSession = $InvokerProcess.PSObject.Properties['SessionId']
  if ($null -eq $boundarySession -or $null -eq $invokerSession -or
      [int]$boundarySession.Value -ne [int]$invokerSession.Value) {
    throw 'interactive boundary and launcher are not in the same session'
  }
  $boundaryOwner = Invoke-CimMethod -InputObject $BoundaryProcess -MethodName GetOwnerSid -ErrorAction Stop
  $invokerOwner = Invoke-CimMethod -InputObject $InvokerProcess -MethodName GetOwnerSid -ErrorAction Stop
  if ([uint32]$boundaryOwner.ReturnValue -ne 0 -or [uint32]$invokerOwner.ReturnValue -ne 0 -or
      [string]::IsNullOrWhiteSpace([string]$boundaryOwner.Sid) -or
      [string]$boundaryOwner.Sid -cne [string]$invokerOwner.Sid) {
    throw 'interactive boundary and launcher do not have the same verified owner SID'
  }
}

function Assert-WdOperatorInvocationLineage {
  param(
    [int] $CurrentPid = $PID,
    [AllowNull()] [AllowEmptyCollection()] [object[]] $ProcessSnapshot
  )

  $processes = if ($PSBoundParameters.ContainsKey('ProcessSnapshot')) {
    @($ProcessSnapshot | Where-Object { $null -ne $_ })
  } else { @(Get-CimInstance Win32_Process -ErrorAction Stop) }
  $byPid = @{}
  foreach ($process in $processes) {
    $processId = [int]$process.ProcessId
    if ($processId -eq 0) { continue }
    if ($processId -lt 0 -or $byPid.ContainsKey($processId)) {
      throw 'cannot prove operator invocation lineage from the process snapshot'
    }
    $byPid[$processId] = $process
  }
  if (-not $byPid.ContainsKey($CurrentPid)) {
    throw 'current launcher is absent from the process snapshot'
  }
  $invoker = $byPid[$CurrentPid]
  $child = $invoker
  if ([string]$child.CommandLine -imatch '(?:^|\s)-NonInteractive(?:\s|$)') {
    throw 'manual recovery refuses a non-interactive PowerShell host'
  }
  $visited = @{}
  for ($depth = 0; $depth -lt 64; $depth++) {
    $childPid = [int]$child.ProcessId
    if ($visited.ContainsKey($childPid)) { throw 'operator invocation ancestry contains a PID cycle' }
    $visited[$childPid] = $true
    $parentProperty = $child.PSObject.Properties['ParentProcessId']
    if ($null -eq $parentProperty -or [int]$parentProperty.Value -le 0) {
      Assert-WdTrustedInteractiveBoundary -BoundaryProcess $child -InvokerProcess $invoker
      return
    }
    $parentPid = [int]$parentProperty.Value
    if (-not $byPid.ContainsKey($parentPid)) {
      Assert-WdTrustedInteractiveBoundary -BoundaryProcess $child -InvokerProcess $invoker
      return
    }
    $parent = $byPid[$parentPid]
    $childStartProperty = $child.PSObject.Properties['CreationDate']
    $parentStartProperty = $parent.PSObject.Properties['CreationDate']
    if ($null -eq $childStartProperty -or $null -eq $parentStartProperty -or
        $null -eq $childStartProperty.Value -or $null -eq $parentStartProperty.Value) {
      throw 'operator invocation ancestry has unknown creation time'
    }
    try {
      $childStart = ([DateTimeOffset]$childStartProperty.Value).ToUniversalTime()
      $parentStart = ([DateTimeOffset]$parentStartProperty.Value).ToUniversalTime()
    } catch { throw 'operator invocation ancestry has an invalid creation time' }
    if ($parentStart -gt $childStart) {
      throw 'operator invocation ancestry is inconsistent with process creation times'
    }
    $parentName = [string]$parent.Name
    if ($parentName -imatch '^(codex|claude)\.exe(?:\.old\.[0-9]+)?$') {
      throw 'manual recovery cannot be invoked by a model-owned native process'
    }
    if ($parentName -imatch '^(powershell|pwsh)\.exe$') {
      $commandLine = [string]$parent.CommandLine
      if ([string]::IsNullOrWhiteSpace($commandLine)) {
        throw 'operator invocation PowerShell ancestry is ambiguous'
      }
      $fileMatch = [regex]::Match($commandLine,
        '(?i)(?:^|\s)-File\s+(?:"(?<path>[^"]+)"|''(?<path>[^'']+)''|(?<path>\S+))(?=\s|$)')
      $launcherLeaf = if ($fileMatch.Success) {
        [IO.Path]::GetFileName(($fileMatch.Groups['path'].Value -split '\\')[-1])
      } else { '' }
      if ($launcherLeaf -iin @('start-wd-agent.ps1', 'start-wd-codex-lead.ps1') -and
          $commandLine -imatch '(?:^|\s)-Agent\s+["'']?codex-lead-1["'']?(?:\s|$)') {
        throw 'manual recovery cannot be invoked by another Lead launcher'
      }
    }
    $child = $parent
  }
  throw 'operator invocation ancestry exceeds the verification bound'
}

function Invoke-WdManagedAttemptRetirement {
  param(
    [Parameter(Mandatory)] [object] $Evidence,
    [Parameter(Mandatory)] [string] $ReviewedJournalDigest,
    [Parameter(Mandatory)] [string] $Reason,
    [switch] $DryRun
  )

  if ($ReviewedJournalDigest -cnotmatch '^[0-9A-Fa-f]{64}$' -or
      [string]::IsNullOrWhiteSpace($Reason) -or $Reason.Length -gt 512 -or
      $Reason -match '[\x00-\x1F\x7F]') {
    throw 'retirement requires a 64-hex digest and bounded printable reason'
  }
  $reviewed = $ReviewedJournalDigest.ToUpperInvariant()
  $current = Get-WdManagedAttemptInventory `
    -PointerPath ([string]$Evidence.pointer_path) `
    -JournalPath ([string]$Evidence.journal_path)
  if ([string]$Evidence.digest -cne $reviewed -or [string]$current.digest -cne $reviewed) {
    throw 'managed attempt evidence changed or does not match the reviewed digest'
  }
  $auditRoot = Join-Path ([string]$Evidence.worktree) '.codex-audit'
  [void](Assert-LanePathWithoutReparse -Path $auditRoot `
    -TrustedRoot ([IO.Path]::GetPathRoot([string]$Evidence.worktree)) -ExpectedType Directory)
  $archiveRoot = Join-Path $auditRoot 'wd-retired-conversations'
  $archivePath = Join-Path $archiveRoot $reviewed.ToLowerInvariant()
  $archiveJournal = Join-Path $archivePath 'journal'
  $manifestPath = Join-Path $archivePath 'retirement-manifest.json'
  $archivedPointer = Join-Path $archivePath 'runtime-owner-pointer.json'
  if (Test-Path -LiteralPath $archivePath) {
    throw "retirement archive already exists or is partial: $archivePath"
  }
  $result = [pscustomobject]@{
    status = if ([bool]$DryRun) { 'retirement_plan_verified' } else { 'managed_attempt_retired' }
    dry_run = [bool]$DryRun
    reviewed_digest = $reviewed
    archive_path = $archivePath
    manifest_path = $manifestPath
    external_effects_unknown = $true
    task_completion_verified = $false
  }
  if ([bool]$DryRun) { return $result }

  $archiveRootCreated = $false
  $archiveCreated = $false
  $journalMoved = $false
  $manifestCreated = $false
  $temporaryManifest = ''
  try {
    if (-not (Test-Path -LiteralPath $archiveRoot -PathType Container)) {
      [void](New-Item -ItemType Directory -Path $archiveRoot)
      $archiveRootCreated = $true
    }
    [void](Assert-LanePathWithoutReparse -Path $archiveRoot `
      -TrustedRoot ([IO.Path]::GetPathRoot([string]$Evidence.worktree)) -ExpectedType Directory)
    [void](New-Item -ItemType Directory -Path $archivePath)
    $archiveCreated = $true
    [void](Assert-LanePathWithoutReparse -Path $archivePath `
      -TrustedRoot ([IO.Path]::GetPathRoot([string]$Evidence.worktree)) -ExpectedType Directory)
    Move-Item -LiteralPath ([string]$Evidence.journal_path) -Destination $archiveJournal
    $journalMoved = $true
    $archivedInventory = Get-WdManagedAttemptInventory `
      -PointerPath ([string]$Evidence.pointer_path) -JournalPath $archiveJournal
    if ([string]$archivedInventory.digest -cne $reviewed) {
      throw 'archived journal bytes differ from the reviewed evidence'
    }
    $retirementManifest = [ordered]@{
      schema = 'wd.managed-attempt-retirement.v1'
      disposition = 'operator_abandoned_uncertain_attempt'
      agent = [string]$Evidence.agent
      reviewed_digest = $reviewed
      retirement_reason = $Reason
      retired_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
      prior_session_id = [string]$Evidence.owner.session_id
      prior_generation = [string]$Evidence.owner.generation
      prior_status = [string]$Evidence.owner.status
      original_journal_path = [string]$Evidence.journal_path
      archive_path = $archivePath
      external_effects_unknown = $true
      task_completion_verified = $false
      replay_performed = $false
      bridge_claims_affected = $false
      operator_handoff_required = $true
      inventory = @($archivedInventory.entries)
    }
    $temporaryManifest = "$manifestPath.$PID.tmp"
    $manifestBytes = [Text.Encoding]::UTF8.GetBytes(
      ($retirementManifest | ConvertTo-Json -Depth 8)
    )
    $stream = [IO.File]::Open($temporaryManifest, [IO.FileMode]::CreateNew,
      [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($manifestBytes, 0, $manifestBytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    Move-Item -LiteralPath $temporaryManifest -Destination $manifestPath
    $manifestCreated = $true
    $verifiedManifest = [IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json -ErrorAction Stop
    if ([string]$verifiedManifest.schema -cne 'wd.managed-attempt-retirement.v1' -or
        [string]$verifiedManifest.reviewed_digest -cne $reviewed -or
        $verifiedManifest.external_effects_unknown -isnot [bool] -or
        $verifiedManifest.external_effects_unknown -ne $true -or
        $verifiedManifest.task_completion_verified -isnot [bool] -or
        $verifiedManifest.task_completion_verified -ne $false) {
      throw 'retirement manifest failed verification'
    }
    $finalInventory = Get-WdManagedAttemptInventory `
      -PointerPath ([string]$Evidence.pointer_path) -JournalPath $archiveJournal
    if ([string]$finalInventory.digest -cne $reviewed) {
      throw 'managed attempt evidence changed before pointer retirement'
    }
    $result | Add-Member -NotePropertyName manifest_sha256 `
      -NotePropertyValue ((Read-Utf8LaneSnapshot -Path $manifestPath).Hash)
    # This is deliberately the final filesystem mutation. Until this atomic
    # same-volume move succeeds, the original runtime pointer keeps managed
    # startup blocked. Nothing writes or verifies on disk after it succeeds.
    Move-Item -LiteralPath ([string]$Evidence.pointer_path) -Destination $archivedPointer
    return $result
  } catch {
    $failure = $_
    if ($temporaryManifest -and (Test-Path -LiteralPath $temporaryManifest -PathType Leaf)) {
      Remove-Item -LiteralPath $temporaryManifest -Force -ErrorAction SilentlyContinue
    }
    if ($manifestCreated -and (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
      Remove-Item -LiteralPath $manifestPath -Force -ErrorAction SilentlyContinue
    }
    if ($journalMoved -and -not (Test-Path -LiteralPath ([string]$Evidence.journal_path)) -and
        (Test-Path -LiteralPath $archiveJournal -PathType Container)) {
      try {
        $rollbackInventory = Get-WdManagedAttemptInventory `
          -PointerPath ([string]$Evidence.pointer_path) -JournalPath $archiveJournal
        if ([string]$rollbackInventory.digest -cne $reviewed) {
          throw 'archived bytes changed; refusing to contaminate the original journal during rollback'
        }
        Move-Item -LiteralPath $archiveJournal -Destination ([string]$Evidence.journal_path)
      }
      catch { throw "retirement failed and journal rollback also failed; runtime pointer remains blocking: $($failure.Exception.Message); $($_.Exception.Message)" }
    }
    if ($archiveCreated -and (Test-Path -LiteralPath $archivePath -PathType Container)) {
      Remove-Item -LiteralPath $archivePath -ErrorAction SilentlyContinue
    }
    if ($archiveRootCreated -and (Test-Path -LiteralPath $archiveRoot -PathType Container)) {
      Remove-Item -LiteralPath $archiveRoot -ErrorAction SilentlyContinue
    }
    throw $failure
  }
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
  foreach ($requiredLeaf in @(
      'Drain-AcceptedBridgeQueue.ps1',
      'Restore-BridgeSpool.ps1',
      'Start-AgentBridgeSession.ps1',
      'Write-AgentEvent.ps1'
    )) {
    if (-not $expectedFiles.ContainsKey($requiredLeaf.ToLowerInvariant())) {
      throw "lane bootstrap manifest is missing required helper: $requiredLeaf"
    }
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
$externalSessions = @(Read-WdExternalSessions -Path $ExternalSessionsPath -ExpectedHash $ExternalSessionsHash)
$turnMode = Get-WdLaneTurnMode -Lane $lane
$conversationSurface = Get-WdLaneConversationSurface -Lane $lane
$conversationPermissions = Get-WdLaneConversationPermissions -Lane $lane

$worktree = Resolve-NormalizedPath -Path ([string]$lane.worktree)
$primaryRepo = Resolve-NormalizedPath -Path ([string]$manifest.primary_repo_root)
$expectedCommonGit = Resolve-NormalizedPath -Path ([string]$manifest.repo_common_git_dir)
$runtimeRoot = Resolve-NormalizedPath -Path ([string]$manifest.runtime_root)
$conversationConfigBaseline = $null
$codexUserConfigPath = ''
if ($conversationSurface -ceq 'local_window' -and
    $conversationPermissions.CodexPermissionPosture -ceq 'existing_interactive') {
  $codexUserConfigPath = if ($env:CODEX_HOME) {
    Join-Path ([IO.Path]::GetFullPath($env:CODEX_HOME)) 'config.toml'
  } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex\config.toml' }
  $conversationConfigBaseline = Assert-WdLeadInteractivePostureBaseline `
    -Lane $lane -Worktree $worktree -UserConfigPath $codexUserConfigPath
}
$manualLeadAction = $RecoverInteractive -or $RetireManagedAttempt
if ($manualLeadAction -and (
    $Agent -cne 'codex-lead-1' -or
    $turnMode -cne 'managed' -or
    $conversationSurface -cne 'local_window' -or
    $conversationPermissions.CodexPermissionPosture -cne 'existing_interactive' -or
    $null -eq $conversationConfigBaseline)) {
  throw 'manual recovery requires the original reviewed managed/local-window/full-access Lead configuration'
}
$launchTurnMode = if ($RecoverInteractive) { 'interactive' } else { $turnMode }
$launchConversationSurface = if ($RecoverInteractive) { 'none' } else { $conversationSurface }

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
$drainer = Join-Path $bootstrapRoot 'Drain-AcceptedBridgeQueue.ps1'
$replayer = Join-Path $bootstrapRoot 'Restore-BridgeSpool.ps1'
$starter = Join-Path $bootstrapRoot 'Start-AgentBridgeSession.ps1'
$writer = Join-Path $bootstrapRoot 'Write-AgentEvent.ps1'
Assert-LaneBootstrapIntegrity `
  -ScriptRoot $PSScriptRoot `
  -BootstrapRoot $bootstrapRoot
[void](Read-NonEmptyFile -Path $drainer -Label "lane '$Agent' accepted queue drainer")
[void](Read-NonEmptyFile -Path $replayer -Label "lane '$Agent' targeted replayer")
[void](Read-NonEmptyFile -Path $starter -Label "lane '$Agent' bridge starter")
[void](Read-NonEmptyFile -Path $writer -Label "lane '$Agent' bridge writer")

# Pinned bridge communication-code package: verified against the anchored
# deployment manifest before any CLI launch. Only WD_BRIDGE_* discovery
# variables are exported; Python isolation stays inside the invocation
# wrapper per call. The task worktree remains the Git cwd.
$bridgeCodeContextScript = Join-Path $PSScriptRoot 'BridgeCodeContext.ps1'
$bridgeCodeDefinitionPath = Join-Path $PSScriptRoot 'bridge-code-files.json'
$bridgeCodeContext = $null
if ($sourceTreeMode) {
  $bridgeCodeContext = [pscustomobject]@{
    schema = 'wd.bridge-code-context.v1'
    mode = 'source_tree_rehearsal_without_pinned_package'
  }
} else {
  foreach ($bridgeCodeInput in @(
      @{ Name = 'BridgeCodeContext.ps1'; Path = $bridgeCodeContextScript },
      @{ Name = 'bridge-code-files.json'; Path = $bridgeCodeDefinitionPath },
      @{ Name = 'Invoke-WdBridgePython.ps1'; Path = (Join-Path $PSScriptRoot 'Invoke-WdBridgePython.ps1') }
    )) {
    [void](Assert-LanePathWithoutReparse `
      -Path $bridgeCodeInput.Path -TrustedRoot $laneTrustedDrive -ExpectedType Leaf)
    $bridgeCodeProperty = $deploymentAnchor.files.PSObject.Properties[$bridgeCodeInput.Name]
    if (
      $null -eq $bridgeCodeProperty -or
      (Get-FileHash -LiteralPath $bridgeCodeInput.Path -Algorithm SHA256).Hash -cne
        ([string]$bridgeCodeProperty.Value).ToUpperInvariant()
    ) {
      throw "pinned bridge code input is not covered by the anchored bundle: $($bridgeCodeInput.Name)"
    }
  }
  . $bridgeCodeContextScript
  $bridgePythonProperty = $manifest.PSObject.Properties['bridge_python']
  if (
    $null -eq $bridgePythonProperty -or
    [string]::IsNullOrWhiteSpace([string]$bridgePythonProperty.Value.executable)
  ) {
    throw 'fleet manifest is missing bridge_python.executable'
  }
  $bridgeCodeContext = Initialize-WdBridgeCodeContext `
    -BundleRoot $PSScriptRoot `
    -Deployment $deploymentAnchor `
    -DefinitionPath $bridgeCodeDefinitionPath `
    -PythonExecutable ([string]$bridgePythonProperty.Value.executable) `
    -Generation $bundleGeneration `
    -RuntimeRoot $runtimeRoot `
    -SkipImportSmoke:$DryRun
}
$targetState = $manifest.target_state
if (
  $null -eq $targetState -or
  [string]$targetState.id -cne 'wd-swarm-target-state-v1' -or
  [string]$targetState.capability_effect -cne 'none' -or
  [string]$targetState.relative_path -cne 'WD_SWARM_TARGET_STATE_V1.md' -or
  [string]$targetState.sha256 -cnotmatch '^[0-9A-F]{64}$' -or
  [string]$targetState.image_relative_path -cne 'WaggleDanceSwarmAi.png' -or
  [string]$targetState.image_sha256 -cnotmatch '^[0-9A-F]{64}$' -or
  [string]$targetState.image_sha256 -cne [string]$targetState.source_image_sha256 -or
  [string]$targetState.presentation -cne
    'multimodal_initial_turn_once_per_lane_session'
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
$targetImagePath = Join-Path $PSScriptRoot (
  [string]$targetState.image_relative_path
)
[void](Assert-LanePathWithoutReparse `
  -Path $targetImagePath -TrustedRoot $laneTrustedDrive -ExpectedType Leaf)
if (
  (Get-FileHash -LiteralPath $targetImagePath -Algorithm SHA256).Hash -cne
    [string]$targetState.image_sha256
) {
  throw 'fleet target-state image hash mismatch'
}
$targetImageLength = (Get-Item -LiteralPath $targetImagePath -Force).Length
if ($targetImageLength -lt 1 -or $targetImageLength -gt 10MB) {
  throw 'fleet target-state image size is unsafe'
}
$parallelPolicy = $manifest.parallel_policy
if (
  $null -eq $parallelPolicy -or
  [string]$parallelPolicy.id -cne 'wd-swarm-parallel-policy-v1' -or
  [string]$parallelPolicy.capability_effect -cne 'none' -or
  [string]$parallelPolicy.relative_path -cne 'WD_SWARM_PARALLEL_POLICY_V1.md' -or
  [string]$parallelPolicy.sha256 -cnotmatch '^[0-9A-F]{64}$'
) {
  throw 'fleet parallel-policy manifest is missing or unsafe'
}
$parallelPolicyPath = Join-Path $PSScriptRoot (
  [string]$parallelPolicy.relative_path
)
$laneStateWriter = Join-Path $PSScriptRoot 'Write-WdLaneCurrentState.ps1'
foreach ($requiredBundleInput in @($parallelPolicyPath, $laneStateWriter)) {
  [void](Assert-LanePathWithoutReparse `
    -Path $requiredBundleInput `
    -TrustedRoot $laneTrustedDrive `
    -ExpectedType Leaf)
  [void](Read-NonEmptyFile `
    -Path $requiredBundleInput `
    -Label 'lane compact-state bootstrap input')
}
if (
  (Get-FileHash -LiteralPath $parallelPolicyPath -Algorithm SHA256).Hash -cne
    [string]$parallelPolicy.sha256
) {
  throw 'fleet parallel-policy document hash mismatch'
}
if (
  $DryRun -and
  $sourceTreeMode -and
  -not (Test-Path -LiteralPath $currentPointer -PathType Leaf)
) {
  Write-Warning "DryRun: current reboot pointer will be generated during committed bundle deployment: $currentPointer"
} else {
  [void](Read-NonEmptyFile -Path $currentPointer -Label 'current reboot pointer')
}
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.roles) -Label 'fleet roles')
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.current_handoff) -Label 'current restart handoff')
[void](Read-NonEmptyFile -Path ([string]$manifest.state_precedence.gpu_guide) -Label 'local GPU guide')
[void](Read-NonEmptyFile -Path ([string]$lane.prompt) -Label "lane '$Agent' role prompt")
[void](Read-NonEmptyFile -Path ([string]$lane.handoff) -Label "lane '$Agent' handoff")

$laneStateDirectory = Join-Path $worktree '.codex-audit'
$laneCurrentStatePath = Join-Path $laneStateDirectory 'wd-current-state.json'
$laneCurrentStateStatus = 'absent'
if (Test-Path -LiteralPath $laneStateDirectory) {
  [void](Assert-LanePathWithoutReparse `
    -Path $laneStateDirectory `
    -TrustedRoot $laneTrustedDrive `
    -ExpectedType Directory)
}
if (Test-Path -LiteralPath $laneCurrentStatePath -PathType Leaf) {
  [void](Assert-LanePathWithoutReparse `
    -Path $laneCurrentStatePath `
    -TrustedRoot $laneTrustedDrive `
    -ExpectedType Leaf)
  try {
    $laneStateBytes = [IO.File]::ReadAllBytes($laneCurrentStatePath)
    if ($laneStateBytes.Length -gt 32768) {
      throw 'compact state exceeds 32 KiB'
    }
    $laneCurrentState = [Text.Encoding]::UTF8.GetString($laneStateBytes) |
      ConvertFrom-Json -ErrorAction Stop
    if (
      [string]$laneCurrentState.schema -cne 'wd.lane-current.v1' -or
      [string]$laneCurrentState.agent -cne $Agent -or
      -not ([string]$laneCurrentState.worktree).Equals(
        $worktree,
        [StringComparison]::OrdinalIgnoreCase
      ) -or
      [string]$laneCurrentState.branch -cne $actualBranch -or
      [string]$laneCurrentState.head -cnotmatch '^[0-9a-f]{40}$' -or
      [string]::IsNullOrWhiteSpace([string]$laneCurrentState.task_id) -or
      [string]$laneCurrentState.status -cnotmatch '^[a-z][a-z0-9_-]{0,63}$' -or
      [string]::IsNullOrWhiteSpace([string]$laneCurrentState.next_action)
    ) {
      throw 'compact state identity or required fields do not match this lane'
    }
    $laneCurrentStateStatus = if (
      [string]$laneCurrentState.head -ceq $actualHead
    ) { 'current' } else { 'head-moved-fallback-required' }
  }
  catch {
    $laneCurrentStateStatus = 'invalid-fallback-required'
    Write-Warning (
      "lane '$Agent' compact state is not current: $($_.Exception.Message); " +
      'bridge and Markdown handoffs will be the recovery fallback'
    )
  }
}

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
$turnRunnerHash = ''
$conversationCodeHashes = @{}
$verifiedConversationCode = @{}
if ($turnMode -ceq 'managed') {
  $turnRunnerSnapshot = Read-WdLaneTurnRunnerSnapshot `
    -ScriptRoot $PSScriptRoot `
    -DeploymentAnchor $deploymentAnchor `
    -SourceTreeMode:$sourceTreeMode
  $turnRunnerHash = [string]$turnRunnerSnapshot.Hash
  if ($conversationSurface -ceq 'local_window') {
    foreach ($name in @('Invoke-WdCodexConversationLoop.ps1', 'Show-WdOperatorConversation.ps1')) {
      $snapshot = Read-WdLaneTurnRunnerSnapshot -ScriptRoot $PSScriptRoot `
        -FileName $name -DeploymentAnchor $deploymentAnchor -SourceTreeMode:$sourceTreeMode
      $conversationCodeHashes[$name] = [string]$snapshot.Hash
      $verifiedConversationCode[$name] = [string]$snapshot.Text
    }
  }
}
$manualAttemptEvidence = $null
$manualAttemptLease = $null
if ($manualLeadAction) {
  try {
    $manualAttemptLease = Enter-WdManagedAttemptLease `
      -RuntimeRoot $runtimeRoot -Agent $Agent
    Assert-WdLaneLaunchAvailable -Lane $lane -KnownLanes @($manifest.lanes) -ExternalSessions $externalSessions
    $manualAttemptEvidence = Get-WdManagedAttemptEvidence `
      -Agent $Agent -Worktree $worktree -RuntimeRoot $runtimeRoot
    if (-not $DryRun) {
      if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
        throw 'actual manual recovery requires an interactive operator console; -NonInteractive and redirected input are refused'
      }
      Assert-WdOperatorInvocationLineage
    }
    if ($RetireManagedAttempt) {
      if ([string]$manualAttemptEvidence.digest -cne $ReviewedJournalDigest.ToUpperInvariant()) {
        throw 'reviewed journal digest does not match the lease-protected managed attempt'
      }
      if (-not $DryRun) {
        $digestPrefix = $ReviewedJournalDigest.ToUpperInvariant().Substring(0, 12)
        Write-Warning 'Retirement abandons an uncertain attempt; it does not prove external effects were undone or release bridge claims.'
        $confirmation = Read-Host "Type the reviewed digest prefix $digestPrefix to retire this attempt"
        if ([string]$confirmation -cne $digestPrefix) {
          throw 'managed attempt retirement confirmation did not match the reviewed digest prefix'
        }
        Assert-WdLaneLaunchAvailable -Lane $lane -KnownLanes @($manifest.lanes) -ExternalSessions $externalSessions
        Assert-WdOperatorInvocationLineage
        [void](Assert-WdLeadInteractivePostureBaseline `
          -Lane $lane -Worktree $worktree -UserConfigPath $codexUserConfigPath)
        $confirmedEvidence = Get-WdManagedAttemptEvidence `
          -Agent $Agent -Worktree $worktree -RuntimeRoot $runtimeRoot
        if ([string]$confirmedEvidence.digest -cne [string]$manualAttemptEvidence.digest) {
          throw 'managed attempt evidence changed during operator confirmation'
        }
        $manualAttemptEvidence = $confirmedEvidence
      }
      $retirement = Invoke-WdManagedAttemptRetirement `
        -Evidence $manualAttemptEvidence `
        -ReviewedJournalDigest $ReviewedJournalDigest `
        -Reason $RetirementReason `
        -DryRun:$DryRun
      Write-Host ("  retirement archive: {0}" -f $retirement.archive_path)
      Write-Host ("  retirement manifest: {0}" -f $retirement.manifest_path)
      Write-Host ("  reviewed digest:    {0}" -f $retirement.reviewed_digest)
      Write-Host ("  retirement reason:  {0}" -f $RetirementReason)
      Write-Warning 'Start a genuinely new managed Lead thread PAUSED, then paste the printed manifest path, digest and reason before directing further work.'
      return $retirement
    }
  } catch {
    if ($null -ne $manualAttemptLease) {
      $manualAttemptLease.Dispose()
      $manualAttemptLease = $null
    }
    throw
  } finally {
    if ($RetireManagedAttempt -and $null -ne $manualAttemptLease) {
      $manualAttemptLease.Dispose()
      $manualAttemptLease = $null
    }
  }
}
$targetImageDelivery = if ($cliName -ieq 'codex.cmd') {
  'codex_cli_initial_image'
} else {
  'claude_initial_read_visual'
}

$stateRule = [string]$manifest.state_precedence.rule
$visualBootstrapPrompt = if ($cliName -ieq 'claude.cmd') {
  (
    "FIRST use the Read tool once on the exact PNG $targetImagePath so it is " +
    'received as visual content before any bridge read or work. The image is the ' +
    'primary north-star; do not replace it with a prose interpretation. It is ' +
    'direction, not evidence of current capability, and grants no authority. '
  )
} else {
  (
    'FIRST receive the attached PNG once as the primary north-star; do not ' +
    'replace it with a prose interpretation. It is direction, not evidence of ' +
    'current capability, and grants no authority. '
  )
}
$startupPrompt = (
  $visualBootstrapPrompt +
  "Read the current reboot pointer: {0}. Then read the compact lane state " +
  "{1} (launcher status: {2}), the fleet roles {3}, lane prompt {4}, and parallel " +
  "policy {5}. Read the live bridge next action and current claims before acting. " +
  "Use the fleet handoff {6} and lane Markdown handoff {7} only if compact state is " +
  "absent, inconsistent, or a named historical fact is needed; do not load the dated " +
  "snapshot by default. Optional guides are {8} and {9}. Runtime model selection is " +
  "explicitly pinned to {10} at effort {11}. Any legacy model labels " +
  "in durable role, prompt, or historical files are " +
  "historical metadata only, not a pin or current runtime identity. " +
  "State precedence: {12} Read the bridge with Read-AgentBridge.ps1 " +
  "-NoAckReceived, use Get-BridgeNextAction, reject stale acknowledgements, " +
  "resume the existing task autonomously without inventing authority, and update " +
  "compact state with {13} after each bounded slice. Follow the parallel policy: " +
  "claim file-disjoint work and never wait silently when another eligible slice exists."
) -f @(
  [string]$manifest.state_precedence.current_state_pointer,
  $laneCurrentStatePath,
  $laneCurrentStateStatus,
  [string]$manifest.state_precedence.roles,
  [string]$lane.prompt,
  $parallelPolicyPath,
  [string]$manifest.state_precedence.current_handoff,
  [string]$lane.handoff,
  $grokMarkdown,
  [string]$manifest.state_precedence.gpu_guide,
  $model,
  $effort,
  $stateRule,
  $laneStateWriter
)
if ($cliName -ieq 'claude.cmd' -and $turnMode -ceq 'interactive') {
  $startupPrompt += (
    ' These startup instructions supersede only legacy self-pacing requirements in external role, lane prompt, and handoff files; ' +
    'role permissions, task scope, claims, and merge authority remain unchanged. ' +
    " This is Claude lane $Agent. On the first turn use CronList, keep exactly " +
    "one lane-specific session-only recurring five-minute CronCreate backstop, " +
    "delete duplicates with CronDelete, and recreate it after every restart. Its " +
    "prompt must re-read compact state and bridge next action for $Agent. Use CronList " +
    'to preserve an existing pending one-shot on no-op cron, Monitor, and dynamic-loop turns. ' +
    'Do not rearm merely because a no-op turn ran. The absolute deadline must be the scheduler-confirmed target, not an estimate. ' +
    'Call ScheduleWakeup only when no valid pending one-shot remains. If a missing wake must be rebuilt, use ' +
    "the remaining time to its confirmed deadline, never a fresh fixed delay. A due " +
    'deadline means resume the bounded turn now. After a one-shot has fired and its bounded slice has run, ' +
    'choose a new future deadline from the next eligible action or backstop; this is not recovery of a missing pending wake. ' +
    'Record the new scheduler-confirmed target, not the expired deadline. The session-only cron is a ' +
    "missed-wakeup backstop, not permission to duplicate or steal a claim."
  )
}
if ($turnMode -ceq 'managed') {
  $startupPrompt += (
    ' These startup instructions supersede only legacy self-pacing requirements in external role, lane prompt, and handoff files; ' +
    'role permissions, task scope, claims, and merge authority remain unchanged. ' +
    ' This lane uses one launcher-owned turn loop. ' +
    'Do not invoke /loop or native Cron scheduling, even if a legacy prompt requests it. ' +
    'Do not create CronCreate or ScheduleWakeup jobs or another polling process. ' +
    'At the end of each bounded turn, persist compact state with the pinned writer. ' +
    'The launcher owns bridge wake consumption and the recurring backstop.'
  )
  if ($conversationSurface -ceq 'local_window') {
    $startupPrompt += (
      ' The operator conversation window steers this same Lead thread during active work and may interrupt it. ' +
      'Treat direct user messages as instructions under the existing authority and task boundaries. ' +
      'Keep each peer agent in its own session; coordinate through the bridge instead of merging their contexts. ' +
      'Reply in the conversation as well as publishing any required bridge task evidence. '
    )
  }
}
$startupPrompt += (
  ' Grok is an on-demand advisory helper for the lead, not a continuously running lane. ' +
  'Its shared persistent budget permits at most one attempted consultation per 60 minutes, including failed attempts. ' +
  'Use C:\Python\Invoke-WdGrok.ps1 -Status to read its previous task/report and next eligible time. ' +
  'Only the lead requests a consultation with -PromptPath <evidence-request.md> -TaskId <task-id>. ' +
  'Never use the legacy Invoke-Grok scripts, bare Grok commands, automatic research jobs or a bypass of the shared budget. ' +
  'Its previous report and current saved lead state are context, not new authority. ' +
  ' Bridge helpers are pinned for this session: invoke Get-BridgeNextAction.ps1, ' +
  'Read-AgentBridge.ps1, Claim-AgentTask.ps1, Release-AgentTask.ps1 and Write-AgentEvent.ps1 ' +
  'from $env:WD_BRIDGE_BIN, and run packaged bridge Python tools only through ' +
  '$env:WD_BRIDGE_PYTHON_WRAPPER (for example & $env:WD_BRIDGE_PYTHON_WRAPPER ' +
  'tools/bridge_next_action.py --agent ' + $Agent + ' --json). Never use worktree-relative ' +
  '.agent-bridge\bin copies or a bare python for bridge tools. Git, build and test commands ' +
  'keep this worktree as their cwd; the pinned code root is not a task repository.'
)
if ($RecoverInteractive) {
  $startupPrompt = $visualBootstrapPrompt + (
    'OPERATOR-EXPLICIT READ-ONLY INSPECTION SESSION. Do not resume task work, ' +
    'run mutating tools, replay a command, publish completion, change compact state, ' +
    'release or transfer a bridge claim, or treat this session as a checkpoint. ' +
    'Automation is disabled. First inspect and explain only the unresolved managed ' +
    "owner pointer $($manualAttemptEvidence.pointer_path) and pending record " +
    "$($manualAttemptEvidence.pending_path), whose lease-protected review digest is " +
    "$($manualAttemptEvidence.digest). External effects remain unknown. Report the " +
    'facts to the operator and wait for an explicit next instruction. Retirement, if ' +
    'chosen, must be performed later by the separate guarded launcher command after ' +
    'this CLI exits; this conversation cannot self-retire the attempt.'
  )
}
$continuationPrompt = $startupPrompt.Substring($visualBootstrapPrompt.Length)

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
Write-Host ("  mode:     {0} (configured: {1})" -f $launchTurnMode, $turnMode)
Write-Host ("  control:  {0} (configured: {1})" -f $launchConversationSurface, $conversationSurface)
Write-Host ("  target:   {0}" -f [string]$targetState.id)
Write-Host ("  visual:   {0} ({1})" -f $targetImagePath, $targetImageDelivery)
if ($RecoverInteractive) {
  Write-Host ("  unresolved pointer: {0}" -f $manualAttemptEvidence.pointer_path)
  Write-Host ("  pending record:      {0}" -f $manualAttemptEvidence.pending_path)
  Write-Host ("  review digest:       {0}" -f $manualAttemptEvidence.digest)
  Write-Warning 'This is read-only inspection of an uncertain attempt, not replay, completion, or rollback of external effects.'
}

if ($DryRun) {
  if ($CheckManagedAdmission -and $turnMode -ceq 'managed') {
    Assert-WdLaneLaunchAvailable -Lane $lane -KnownLanes @($manifest.lanes) -ExternalSessions $externalSessions
  }
  Write-Host '  DRY RUN: bridge bootstrap, handshake write, and CLI launch suppressed.'
  try {
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
      turn_mode = $launchTurnMode
      configured_turn_mode = $turnMode
      turn_runner_sha256 = $turnRunnerHash
      conversation_surface = $launchConversationSurface
      configured_conversation_surface = $conversationSurface
      conversation_permission_posture = $conversationPermissions.CodexPermissionPosture
      conversation_config_baseline = $conversationConfigBaseline
      conversation_code_sha256 = $conversationCodeHashes
      resume_policy = $resumePolicy
      target_state_id = [string]$targetState.id
      target_state_image_path = $targetImagePath
      target_state_image_sha256 = [string]$targetState.image_sha256
      target_state_image_delivery = $targetImageDelivery
      target_state_image_initial_turn_only = $true
      parallel_policy_id = [string]$parallelPolicy.id
      compact_state_path = $laneCurrentStatePath
      compact_state_status = $laneCurrentStateStatus
      bridge_code_context = $bridgeCodeContext
      interactive_recovery = [bool]$RecoverInteractive
      managed_attempt_digest = if ($null -ne $manualAttemptEvidence) { [string]$manualAttemptEvidence.digest } else { '' }
      managed_attempt_pointer = if ($null -ne $manualAttemptEvidence) { [string]$manualAttemptEvidence.pointer_path } else { '' }
      managed_attempt_pending_count = if ($null -ne $manualAttemptEvidence) { [int]$manualAttemptEvidence.pending_count } else { 0 }
      dry_run = $true
    }
  } finally {
    if ($null -ne $manualAttemptLease) {
      $manualAttemptLease.Dispose()
      $manualAttemptLease = $null
    }
  }
}

try {
if ($turnMode -ceq 'managed') {
  Assert-WdLaneLaunchAvailable -Lane $lane -KnownLanes @($manifest.lanes) -ExternalSessions $externalSessions
}
if ($launchTurnMode -ceq 'interactive' -and [Console]::IsInputRedirected) {
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
$env:WD_AGENT_CURRENT_STATE = $laneCurrentStatePath
$env:WD_AGENT_CURRENT_STATE_WRITER = $laneStateWriter
$env:WD_SWARM_PARALLEL_POLICY = $parallelPolicyPath
$env:WD_SWARM_TARGET_IMAGE = $targetImagePath

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
  target_state_image_path = $targetImagePath
  target_state_image_sha256 = [string]$targetState.image_sha256
  target_state_image_delivery = $targetImageDelivery
  target_state_image_initial_turn_only = $true
  capability_effect = 'none'
  model = $model
  effort = $effort
  turn_mode = $launchTurnMode
  configured_turn_mode = $turnMode
  turn_runner_sha256 = $turnRunnerHash
  conversation_surface = $launchConversationSurface
  configured_conversation_surface = $conversationSurface
  interactive_recovery = [bool]$RecoverInteractive
  conversation_permission_posture = $conversationPermissions.CodexPermissionPosture
  conversation_config_baseline = $conversationConfigBaseline
  conversation_code_sha256 = $conversationCodeHashes
  cli_executable = $cliPath
  cli_executable_sha256 = $cliExecutableHash
  resume_policy = $resumePolicy
  baseline_branch = [string]$lane.branch
  baseline_head = [string]$lane.head
  resumed_branch = $actualBranch
  resumed_head = $actualHead
} | ConvertTo-Json -Compress
$targetOutput = @(
  & $writer `
    -Agent $Agent `
    -Type status `
    -TaskId ([string]$targetState.id) `
    -Status target_state_manifested `
    -Message "Prepared the exact visual WaggleDance target for the initial model turn in reboot generation $RunId; this grants no capability or authority." `
    -RunId $RunId `
    -Role ([string]$lane.role) `
    -AgentUuid ([string]$lane.agent_uuid) `
    -SessionId $RunId `
    -Capabilities @($lane.capabilities | ForEach-Object { [string]$_ }) `
    -PayloadJson $targetPayload
)
$targetEvents = @($targetOutput | Where-Object {
  $_ -is [psobject] -and [string]$_.status -ceq 'target_state_manifested'
})
$targetDelivery = $null
if ($targetEvents.Count -eq 1) {
  $targetDeliveryProperty = $targetEvents[0].PSObject.Properties['_bridge_delivery']
  if ($null -ne $targetDeliveryProperty) {
    $targetDelivery = $targetDeliveryProperty.Value
  }
}
if (
  $targetEvents.Count -ne 1 -or
  $null -eq $targetDelivery -or
  [string]$targetDelivery.delivery_status -cne 'canonical' -or
  $targetDelivery.canonical_durable -isnot [bool] -or
  $targetDelivery.canonical_durable -ne $true
) {
  throw "target-state manifest event was not canonically durable for $Agent"
}
$targetOutput | Out-Host
Assert-LaneBootstrapIntegrity `
  -ScriptRoot $PSScriptRoot `
  -BootstrapRoot $bootstrapRoot

$canaryTaskId = "wd-append-canary-$RunId"
$canaryPayload = [ordered]@{
  schema_version = 1
  generation = $bundleGeneration
  target_state_id = [string]$targetState.id
  manifest_writer = 'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'
  audit_phase = 'canonical_append_probe'
  success_requires_canonical_delivery = $true
} | ConvertTo-Json -Compress
$canaryStartedUtc = [DateTimeOffset]::UtcNow
$canaryOutput = @(
  & $writer `
    -Agent $Agent `
    -Type status `
    -TaskId $canaryTaskId `
    -Status append_canary `
    -Message "Canonical append canary attempted with the manifest-hashed writer for $Agent generation $RunId; success requires its canonical delivery receipt." `
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
$canaryDelivery = $null
if ($canaryEvents.Count -eq 1) {
  $canaryDeliveryProperty = $canaryEvents[0].PSObject.Properties['_bridge_delivery']
  if ($null -ne $canaryDeliveryProperty) {
    $canaryDelivery = $canaryDeliveryProperty.Value
  }
}
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
  $null -eq $canaryDelivery -or
  [string]$canaryDelivery.delivery_status -cne 'canonical' -or
  $canaryDelivery.canonical_durable -isnot [bool] -or
  $canaryDelivery.canonical_durable -ne $true -or
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
  turn_mode = $launchTurnMode
  configured_turn_mode = $turnMode
  turn_runner_sha256 = $turnRunnerHash
  conversation_surface = $launchConversationSurface
  configured_conversation_surface = $conversationSurface
  interactive_recovery = [bool]$RecoverInteractive
  managed_attempt_digest = if ($null -ne $manualAttemptEvidence) { [string]$manualAttemptEvidence.digest } else { '' }
  managed_attempt_pointer = if ($null -ne $manualAttemptEvidence) { [string]$manualAttemptEvidence.pointer_path } else { '' }
  conversation_permission_posture = $conversationPermissions.CodexPermissionPosture
  conversation_config_baseline = $conversationConfigBaseline
  conversation_code_sha256 = $conversationCodeHashes
  cli_executable = $cliPath
  cli_executable_sha256 = $cliExecutableHash
  resume_policy = $resumePolicy
  baseline_branch = [string]$lane.branch
  baseline_head = [string]$lane.head
  target_state_id = [string]$targetState.id
  target_state_sha256 = [string]$targetState.sha256
  target_state_image_path = $targetImagePath
  target_state_image_sha256 = [string]$targetState.image_sha256
  target_state_image_delivery = $targetImageDelivery
  target_state_image_initial_turn_only = $true
  target_state_manifested = $true
  append_canary = $true
  append_canary_task_id = $canaryTaskId
  append_canary_event_utc = [string]$canaryEvents[0].ts_utc
  append_canary_latency_ms = $canaryLatencyMs
  bundle_generation = $bundleGeneration
  bridge_code_root = [string]$bridgeCodeContext.code_root
  bridge_bin = [string]$bridgeCodeContext.bridge_bin
  bridge_python_wrapper = [string]$bridgeCodeContext.python_wrapper
  bridge_python = [string]$bridgeCodeContext.python_executable
  bridge_python_sha256 = [string]$bridgeCodeContext.python_executable_sha256
  bridge_code_package_sha256 = [string]$bridgeCodeContext.definition_sha256
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

if ($launchTurnMode -ceq 'managed') {
  Assert-WdLaneLaunchAvailable -Lane $lane -KnownLanes @($manifest.lanes) -ExternalSessions $externalSessions
  if ($null -ne $conversationConfigBaseline) {
    [void](Assert-WdLeadInteractivePostureBaseline `
      -Lane $lane -Worktree $worktree -UserConfigPath $codexUserConfigPath)
  }
  $turnRunnerSnapshot = Read-WdLaneTurnRunnerSnapshot `
    -ScriptRoot $PSScriptRoot -DeploymentAnchor $deploymentAnchor
  if ([string]$turnRunnerSnapshot.Hash -cne $turnRunnerHash) {
    throw 'lane turn runner changed after its handshake'
  }
  if ($conversationSurface -ceq 'local_window') {
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') {
      throw 'Lead conversation window requires an STA PowerShell host; restart the launcher with powershell -STA'
    }
    foreach ($name in @('Invoke-WdCodexConversationLoop.ps1', 'Show-WdOperatorConversation.ps1')) {
      $snapshot = Read-WdLaneTurnRunnerSnapshot -ScriptRoot $PSScriptRoot `
        -FileName $name -DeploymentAnchor $deploymentAnchor
      if ([string]$snapshot.Hash -cne [string]$conversationCodeHashes[$name]) {
        throw "lane conversation code changed after its handshake: $name"
      }
      $verifiedConversationCode[$name] = [string]$snapshot.Text
    }
  }
  $backend = if ($cliName -ieq 'codex.cmd') { 'codex' } else { 'claude' }
  $managedTurnParameters = @{
    Agent = $Agent; Backend = $backend; CliPath = $cliPath
    Model = $model; Effort = $effort; Worktree = $worktree
    RuntimeRoot = $runtimeRoot; SessionId = $RunId; Generation = $bundleGeneration
    CompactStatePath = $laneCurrentStatePath; StartupPrompt = $startupPrompt
    ContinuationPrompt = $continuationPrompt; ImagePath = $targetImagePath
    Forever = $true; ShowLifecycle = $true
  }
  if ($conversationSurface -ceq 'local_window') {
    # A Lead operator turn has an explicit one-hour wall-clock bound. This is
    # not inherited by peer managed modes or by the manual inspection CLI.
    $managedTurnParameters['TurnTimeoutSeconds'] = 3600
    $managedTurnParameters['NetworkAccess'] = $conversationPermissions.NetworkAccess
    $managedTurnParameters['AdditionalWritableRoots'] = @($conversationPermissions.AdditionalWritableRoots)
    $managedTurnParameters['CodexPermissionPosture'] = $conversationPermissions.CodexPermissionPosture
  }
  if ($backend -ceq 'claude') {
    # This opt-in mode reuses the already approved interactive Claude posture;
    # it does not grant role, task, claim, or merge authority.
    $managedTurnParameters['ClaudePermissionPosture'] = 'existing_interactive'
  }
  # Isolate the runner's script parameter defaults from the launcher variables.
  # Execute exactly the bytes verified above; do not reopen the script by path.
  $turnResult = & {
    param($VerifiedRunnerText, $LaneTurnParameters, $VerifiedConversationCode = $null)
    . ([scriptblock]::Create([string]$VerifiedRunnerText))
    if ($null -ne $VerifiedConversationCode -and $VerifiedConversationCode.Count -eq 2) {
      . ([scriptblock]::Create([string]$VerifiedConversationCode['Show-WdOperatorConversation.ps1']))
      . ([scriptblock]::Create([string]$VerifiedConversationCode['Invoke-WdCodexConversationLoop.ps1']))
      Invoke-WdCodexConversationLoop @LaneTurnParameters
    } else {
      Invoke-WdLaneTurnLoop @LaneTurnParameters
    }
  } ([string]$turnRunnerSnapshot.Text) $managedTurnParameters $verifiedConversationCode
  $turnResult | Out-Host
  if ([string]$turnResult.status -cne 'stopped') {
    throw "lane '$Agent' managed turn loop stopped with status '$($turnResult.status)'"
  }
  return
}

if ($RecoverInteractive) {
  Assert-WdLaneLaunchAvailable -Lane $lane -KnownLanes @($manifest.lanes) -ExternalSessions $externalSessions
  Assert-WdOperatorInvocationLineage
  [void](Assert-WdLeadInteractivePostureBaseline `
    -Lane $lane -Worktree $worktree -UserConfigPath $codexUserConfigPath)
  $finalRecoveryEvidence = Get-WdManagedAttemptEvidence `
    -Agent $Agent -Worktree $worktree -RuntimeRoot $runtimeRoot
  if ([string]$finalRecoveryEvidence.digest -cne [string]$manualAttemptEvidence.digest) {
    throw 'managed attempt evidence changed before interactive recovery launch'
  }
}
$launchArguments = @()
if ($cliName -ieq 'claude.cmd') {
  $launchArguments += @(
    '--model', $model,
    '--effort', $effort,
    '--dangerously-skip-permissions',
    '--name', $Agent
  )
} elseif ($cliName -ieq 'codex.cmd') {
  $launchArguments += @(
    '--model', $model,
    '-c', ('model_reasoning_effort="{0}"' -f $effort),
    '--image', $targetImagePath
  )
} else {
  throw "lane '$Agent' uses unsupported CLI '$cliName'"
}
$launchArguments += $startupPrompt

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
} finally {
  if ($null -ne $manualAttemptLease) {
    $manualAttemptLease.Dispose()
    $manualAttemptLease = $null
  }
}

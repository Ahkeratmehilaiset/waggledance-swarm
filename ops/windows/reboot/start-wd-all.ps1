#requires -Version 5.1
<#
.SYNOPSIS
  Fail-closed, one-command reboot restore for the WaggleDance agent fleet.

.DESCRIPTION
  Preflights the entire pinned fleet before the first mutation. An Apply run
  updates Codex and Claude Code once, resolves the current Grok model, ensures
  the supervisor-managed Tools consumer, and launches missing interactive lanes.
  DryRun performs validation and prints the update/launch plan without updating,
  writing handshake files, or starting fleet processes. With neither mode
  switch, the launcher defaults to DryRun; mutation always requires -Apply.
#>
[CmdletBinding()]
param(
  [string] $ManifestPath = '',
  [string] $RunId = '',
  [ValidateRange(10, 300)]
  [int] $HandshakeTimeoutSeconds = 90,
  [switch] $SkipCliUpdate,
  [switch] $Apply,
  [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:WdGitExecutable = ''

function Resolve-WdLauncherMode {
  param(
    [bool] $ApplyRequested,
    [bool] $DryRunRequested
  )

  if ($ApplyRequested -and $DryRunRequested) {
    throw 'Apply and DryRun are mutually exclusive'
  }
  if ($ApplyRequested) { return 'Apply' }
  return 'DryRun'
}

function Assert-WdLauncherBundleMode {
  param(
    [Parameter(Mandatory)] [string] $BundleMode,
    [Parameter(Mandatory)] [string] $LauncherMode
  )

  if ($BundleMode -notin @('source', 'deployed')) {
    throw "unsupported reboot bundle mode: $BundleMode"
  }
  if ($LauncherMode -notin @('Apply', 'DryRun')) {
    throw "unsupported reboot launcher mode: $LauncherMode"
  }
  if ($BundleMode -ceq 'source' -and $LauncherMode -cne 'DryRun') {
    throw 'source-tree reboot rehearsal requires -DryRun'
  }
}

function Get-WdSupervisorInvocationPlan {
  param(
    [Parameter(Mandatory)] [string] $BundleMode,
    [Parameter(Mandatory)] [string] $SourceScript,
    [Parameter(Mandatory)] [string] $SourceConfig,
    [Parameter(Mandatory)] [string] $DeployedScript
  )

  if ($BundleMode -ceq 'source') {
    return [pscustomobject]@{
      preflight_script = $SourceScript
      preflight_arguments = @('-ConfigPath', $SourceConfig)
      apply_script = ''
      apply_arguments = @()
      verify_script = ''
      verify_arguments = @()
    }
  }
  if ($BundleMode -cne 'deployed') {
    throw "unsupported reboot bundle mode: $BundleMode"
  }
  return [pscustomobject]@{
    preflight_script = $DeployedScript
    preflight_arguments = @()
    apply_script = $DeployedScript
    apply_arguments = @('-Apply')
    verify_script = $DeployedScript
    verify_arguments = @()
  }
}

$modeWasDefaulted = -not $Apply -and -not $DryRun
$launcherMode = Resolve-WdLauncherMode `
  -ApplyRequested ([bool]$Apply) `
  -DryRunRequested ([bool]$DryRun)
$Apply = $launcherMode -ceq 'Apply'
$DryRun = $launcherMode -ceq 'DryRun'
if ($modeWasDefaulted) {
  Write-Warning 'No mode switch supplied; defaulting to byte-inert DryRun. Use -Apply to mutate.'
}
$bundleManifestAnchor = ''
if (-not $ManifestPath) {
  $ManifestPath = Join-Path $PSScriptRoot 'wd-fleet.json'
}

function Resolve-NormalizedPath {
  param([Parameter(Mandatory)] [string] $Path)
  return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Enter-WdFleetRebootMutex {
  param([Parameter(Mandatory)] [Threading.Mutex] $Mutex)

  try {
    return [bool]$Mutex.WaitOne(0)
  }
  catch [Threading.AbandonedMutexException] {
    # The terminating owner relinquished the mutex. The caller now owns it and
    # must release it in the normal finally path. This is expected after an
    # abrupt shutdown and must not prevent the fail-closed recovery preflight.
    return $true
  }
}

function Read-Utf8FleetSnapshot {
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

function Assert-WdFleetPathWithoutReparse {
  param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [string] $TrustedRoot,
    [ValidateSet('Directory', 'Leaf', 'Any')] [string] $ExpectedType,
    [switch] $AllowMissing
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
    throw "fleet safety path escaped its trusted root: $candidate"
  }
  if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "fleet safety path root is missing: $root"
  }
  $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "fleet safety path root is a reparse point: $root"
  }
  $relative = $candidate.Substring($root.Length).TrimStart('\')
  $current = $root
  $missing = $false
  $currentItem = $rootItem
  foreach ($segment in @($relative -split '\\')) {
    if (-not $segment) { continue }
    if (
      ($currentItem.Attributes -band [IO.FileAttributes]::Directory) -eq 0
    ) {
      throw "fleet safety path ancestor is not a directory: $current"
    }
    $next = Join-Path $current $segment
    try {
      $nextItem = Get-Item -LiteralPath $next -Force -ErrorAction Stop
    } catch {
      # Test-Path and Get-Item can both report a dangling reparse point as
      # missing.  Enumerate the already-validated parent only on that path so
      # the link object itself is still inspected.  Existing protected paths
      # such as WindowsApps remain usable without directory-list permission.
      $matches = @(
        Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop |
          Where-Object {
            ([string]$_.Name).Equals(
              $segment,
              [StringComparison]::OrdinalIgnoreCase
            )
          }
      )
      if ($matches.Count -eq 0) {
        $missing = $true
        $current = $next
        break
      }
      if ($matches.Count -ne 1) {
        throw "fleet safety path component is ambiguous: $next"
      }
      if (($matches[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "fleet safety path component is a reparse point: $next"
      }
      throw "fleet safety path component could not be inspected: $next"
    }
    $currentItem = $nextItem
    $current = [IO.Path]::GetFullPath([string]$currentItem.FullName)
    if (($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "fleet safety path component is a reparse point: $current"
    }
  }
  if ($missing) {
    if ($AllowMissing) { return $candidate }
    throw "fleet safety path component is missing: $current"
  }
  if ($ExpectedType -ceq 'Directory' -and
      ($currentItem.Attributes -band [IO.FileAttributes]::Directory) -eq 0) {
    throw "fleet safety path is not a directory: $candidate"
  }
  if ($ExpectedType -ceq 'Leaf' -and
      ($currentItem.Attributes -band [IO.FileAttributes]::Directory) -ne 0) {
    throw "fleet safety path is not a file: $candidate"
  }
  return $candidate
}

function Initialize-WdFleetFileIdentityNative {
  if ('WaggleDance.FleetFileIdentityNative' -as [type]) { return }
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace WaggleDance {
  [StructLayout(LayoutKind.Sequential)]
  public struct FleetByHandleFileInformation {
    public uint FileAttributes;
    public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
    public uint VolumeSerialNumber;
    public uint FileSizeHigh;
    public uint FileSizeLow;
    public uint NumberOfLinks;
    public uint FileIndexHigh;
    public uint FileIndexLow;
  }

  public static class FleetFileIdentityNative {
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetFileInformationByHandle(
      IntPtr fileHandle,
      out FleetByHandleFileInformation fileInformation
    );
  }
}
'@
}

function Get-WdFleetOpenFileIdentity {
  param([Parameter(Mandatory)] [IO.FileStream] $Stream)

  Initialize-WdFleetFileIdentityNative
  $information = New-Object WaggleDance.FleetByHandleFileInformation
  if (-not [WaggleDance.FleetFileIdentityNative]::GetFileInformationByHandle(
      $Stream.SafeFileHandle.DangerousGetHandle(),
      [ref]$information
    )) {
    $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    $nativeError = New-Object ComponentModel.Win32Exception($nativeCode)
    throw "GetFileInformationByHandle failed: $nativeCode ($($nativeError.Message))"
  }
  return ('windows-file-id-v1:{0:x8}:{1:x8}:{2:x8}' -f
      ([uint32]$information.VolumeSerialNumber),
      ([uint32]$information.FileIndexHigh),
      ([uint32]$information.FileIndexLow))
}

function Get-WdFleetOpenPrefixHash {
  param(
    [Parameter(Mandatory)] [IO.FileStream] $Stream,
    [Parameter(Mandatory)] [int64] $Length
  )

  if ($Length -lt 0 -or $Stream.Length -lt $Length) {
    throw 'canonical bridge prefix length is invalid'
  }
  $Stream.Position = 0
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $buffer = New-Object byte[] 1048576
    [int64]$remaining = $Length
    while ($remaining -gt 0) {
      $requested = [int][Math]::Min([int64]$buffer.Length, $remaining)
      $read = [int]$Stream.Read($buffer, 0, $requested)
      if ($read -le 0) {
        throw 'canonical bridge prefix ended before its frozen length'
      }
      [void]$sha.TransformBlock($buffer, 0, $read, $buffer, 0)
      $remaining -= $read
    }
    $empty = New-Object byte[] 0
    [void]$sha.TransformFinalBlock($empty, 0, 0)
    return ([BitConverter]::ToString($sha.Hash)).Replace('-', '')
  } finally {
    $sha.Dispose()
  }
}

function Get-WdBridgePrefixSnapshot {
  param([Parameter(Mandatory)] [string] $Path)

  $stream = New-Object IO.FileStream(
    $Path,
    [IO.FileMode]::Open,
    [IO.FileAccess]::Read,
    [IO.FileShare]::ReadWrite
  )
  try {
    [int64]$length = $stream.Length
    return [pscustomobject]@{
      path = [IO.Path]::GetFullPath($Path)
      file_identity = Get-WdFleetOpenFileIdentity -Stream $stream
      prefix_length = $length
      prefix_sha256 = Get-WdFleetOpenPrefixHash -Stream $stream -Length $length
    }
  } finally {
    $stream.Dispose()
  }
}

function Assert-WdBridgePrefixPreserved {
  param([Parameter(Mandatory)] $Baseline)

  $stream = New-Object IO.FileStream(
    ([string]$Baseline.path),
    [IO.FileMode]::Open,
    [IO.FileAccess]::Read,
    [IO.FileShare]::ReadWrite
  )
  try {
    $identity = Get-WdFleetOpenFileIdentity -Stream $stream
    if ([string]$identity -cne [string]$Baseline.file_identity) {
      throw 'canonical bridge file identity changed during fleet restore'
    }
    if ($stream.Length -lt [int64]$Baseline.prefix_length) {
      throw 'canonical bridge was truncated during fleet restore'
    }
    $hash = Get-WdFleetOpenPrefixHash `
      -Stream $stream `
      -Length ([int64]$Baseline.prefix_length)
    if ([string]$hash -cne [string]$Baseline.prefix_sha256) {
      throw 'canonical bridge prefix changed during fleet restore'
    }
  } finally {
    $stream.Dispose()
  }
}

function Get-WdSpoolInventory {
  param([Parameter(Mandatory)] [string] $Path)

  $items = @(
    Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop |
      Sort-Object -Property Name
  )
  $rows = New-Object 'System.Collections.Generic.List[object]'
  foreach ($item in $items) {
    if ($item.PSIsContainer) {
      throw "bridge spool contains an unexpected directory: $($item.FullName)"
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "bridge spool contains a reparse point: $($item.FullName)"
    }
    if (([string]$item.Name).EndsWith(
        '.pending',
        [StringComparison]::OrdinalIgnoreCase
      )) {
      throw "bridge spool contains a pending WAL: $($item.FullName)"
    }
    $before = Get-Item -LiteralPath $item.FullName -Force -ErrorAction Stop
    $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
    $after = Get-Item -LiteralPath $item.FullName -Force -ErrorAction Stop
    if (
      [int64]$before.Length -ne [int64]$after.Length -or
      $before.LastWriteTimeUtc -ne $after.LastWriteTimeUtc
    ) {
      throw "bridge spool file changed while inventorying: $($item.FullName)"
    }
    [void]$rows.Add([pscustomobject]@{
      name = [string]$item.Name
      length = [int64]$after.Length
      sha256 = [string]$hash
    })
  }
  return $rows.ToArray()
}

function Assert-WdSpoolInventoryExact {
  param(
    [Parameter(Mandatory)] [string] $Path,
    [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $Baseline
  )

  $current = @(Get-WdSpoolInventory -Path $Path)
  if ($current.Count -ne $Baseline.Count) {
    throw 'bridge spool file count changed during fleet restore'
  }
  for ($index = 0; $index -lt $Baseline.Count; $index++) {
    if (
      [string]$current[$index].name -cne [string]$Baseline[$index].name -or
      [int64]$current[$index].length -ne [int64]$Baseline[$index].length -or
      [string]$current[$index].sha256 -cne [string]$Baseline[$index].sha256
    ) {
      throw "bridge spool inventory changed at index $index"
    }
  }
}

function New-WdBridgeSafetyBaseline {
  param(
    [Parameter(Mandatory)] [string] $RuntimeRoot,
    [Parameter(Mandatory)] [string] $SnapshotRuntimeRoot,
    [Parameter(Mandatory)] [string] $RecoveryStateRoot,
    [Parameter(Mandatory)] [string] $ToolsConflictPath,
    [Parameter(Mandatory)] [string] $WatcherConflictRoot
  )

  $runtime = Resolve-NormalizedPath -Path $RuntimeRoot
  if (-not $runtime.Equals(
      (Resolve-NormalizedPath -Path $SnapshotRuntimeRoot),
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw 'fleet and supervisor runtime roots differ'
  }
  $trustedDrive = [IO.Path]::GetPathRoot($runtime)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $runtime -TrustedRoot $trustedDrive -ExpectedType Directory)
  $canonical = Join-Path $runtime 'shared\events.jsonl'
  $spool = Join-Path $runtime 'spool'
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $canonical -TrustedRoot $runtime -ExpectedType Leaf)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $spool -TrustedRoot $runtime -ExpectedType Directory)

  $recovery = Resolve-NormalizedPath -Path $RecoveryStateRoot
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $recovery `
    -TrustedRoot ([IO.Path]::GetPathRoot($recovery)) `
    -ExpectedType Directory)
  $toolsConflict = Resolve-NormalizedPath -Path $ToolsConflictPath
  $watcherConflicts = Resolve-NormalizedPath -Path $WatcherConflictRoot
  foreach ($candidate in @($toolsConflict, $watcherConflicts)) {
    if (-not $candidate.StartsWith(
        ($recovery + '\'),
        [StringComparison]::OrdinalIgnoreCase
      )) {
      throw "fleet conflict path escapes the recovery root: $candidate"
    }
  }
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $toolsConflict `
    -TrustedRoot $recovery `
    -ExpectedType Any `
    -AllowMissing)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $watcherConflicts `
    -TrustedRoot $recovery `
    -ExpectedType Directory `
    -AllowMissing)

  $baseline = [pscustomobject]@{
    runtime_root = $runtime
    recovery_root = $recovery
    canonical = Get-WdBridgePrefixSnapshot -Path $canonical
    spool_path = $spool
    spool = @(Get-WdSpoolInventory -Path $spool)
    tools_conflict_path = $toolsConflict
    watcher_conflict_root = $watcherConflicts
  }
  Assert-WdBridgeSafetyBaseline -Baseline $baseline
  return $baseline
}

function Assert-WdBridgeSafetyBaseline {
  param([Parameter(Mandatory)] $Baseline)

  $runtime = [string]$Baseline.runtime_root
  $recovery = [string]$Baseline.recovery_root
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $runtime `
    -TrustedRoot ([IO.Path]::GetPathRoot($runtime)) `
    -ExpectedType Directory)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path ([string]$Baseline.canonical.path) `
    -TrustedRoot $runtime `
    -ExpectedType Leaf)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path ([string]$Baseline.spool_path) `
    -TrustedRoot $runtime `
    -ExpectedType Directory)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path $recovery `
    -TrustedRoot ([IO.Path]::GetPathRoot($recovery)) `
    -ExpectedType Directory)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path ([string]$Baseline.tools_conflict_path) `
    -TrustedRoot $recovery `
    -ExpectedType Any `
    -AllowMissing)
  [void](Assert-WdFleetPathWithoutReparse `
    -Path ([string]$Baseline.watcher_conflict_root) `
    -TrustedRoot $recovery `
    -ExpectedType Directory `
    -AllowMissing)
  if (Test-Path -LiteralPath ([string]$Baseline.tools_conflict_path)) {
    throw (
      'Tools replacement conflict appeared during fleet restore: ' +
      [string]$Baseline.tools_conflict_path
    )
  }
  $watcherRoot = [string]$Baseline.watcher_conflict_root
  if (Test-Path -LiteralPath $watcherRoot) {
    $children = @(
      Get-ChildItem -LiteralPath $watcherRoot -Force -ErrorAction Stop
    )
    if ($children.Count -gt 0) {
      throw "watcher replacement conflict evidence appeared: $watcherRoot"
    }
  }
  Assert-WdBridgePrefixPreserved -Baseline $Baseline.canonical
  Assert-WdSpoolInventoryExact `
    -Path ([string]$Baseline.spool_path) `
    -Baseline @($Baseline.spool)
}

function ConvertTo-UtcDateTimeOffset {
  param(
    [Parameter(Mandatory)] $Value,
    [Parameter(Mandatory)] [string] $Label
  )

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
  $styles = (
    [Globalization.DateTimeStyles]::AssumeUniversal -bor
    [Globalization.DateTimeStyles]::AdjustToUniversal
  )
  if (-not [DateTimeOffset]::TryParse(
      [string]$Value,
      [Globalization.CultureInfo]::InvariantCulture,
      $styles,
      [ref]$parsed
    )) {
    throw "$Label is not a valid timestamp"
  }
  return $parsed.ToUniversalTime()
}

function Test-WdJsonBooleanTrue {
  param(
    [Parameter(Mandatory)] $Object,
    [Parameter(Mandatory)] [string] $Name
  )

  $property = $Object.PSObject.Properties[$Name]
  return (
    $null -ne $property -and
    $property.Value -is [bool] -and
    $property.Value -ceq $true
  )
}

function Test-WdJsonIntegerRange {
  param(
    [Parameter(Mandatory)] $Object,
    [Parameter(Mandatory)] [string] $Name,
    [Parameter(Mandatory)] [int64] $Minimum,
    [Parameter(Mandatory)] [int64] $Maximum
  )

  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) { return $false }
  $value = $property.Value
  if ($value -isnot [int] -and $value -isnot [int64]) { return $false }
  $number = [int64]$value
  return $number -ge $Minimum -and $number -le $Maximum
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

function Resolve-WdFleetGitApplication {
  param([Parameter(Mandatory)] [string] $ConfiguredPath)

  if (-not [IO.Path]::IsPathRooted($ConfiguredPath)) {
    throw 'fleet Git executable path must be absolute'
  }
  $candidate = [IO.Path]::GetFullPath($ConfiguredPath)
  if ([IO.Path]::GetExtension($candidate) -cne '.exe') {
    throw 'fleet Git executable must be an .exe application'
  }
  $command = Get-Command `
    -Name $candidate `
    -CommandType Application `
    -ErrorAction Stop
  $source = [IO.Path]::GetFullPath([string]$command.Source)
  if (-not $source.Equals($candidate, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'fleet Git command is not the configured application'
  }
  [void](Assert-WdFleetPathWithoutReparse `
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
  $gitPath = Resolve-WdFleetGitApplication -ConfiguredPath $GitExecutable
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

function Resolve-WdNpmNativeApplication {
  param([Parameter(Mandatory)] [ValidateSet('codex.cmd', 'claude.cmd')] [string] $Name)

  $roamingRoot = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ApplicationData
  )
  if ([string]::IsNullOrWhiteSpace($roamingRoot)) {
    throw 'roaming application-data root is unavailable'
  }
  $relative = if ($Name -ceq 'codex.cmd') {
    'npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
  } else {
    'npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe'
  }
  $candidate = [IO.Path]::GetFullPath((Join-Path $roamingRoot $relative))
  [void](Assert-WdFleetPathWithoutReparse `
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
    throw "$Name did not resolve to its trusted npm-native application"
  }
  return $candidate
}

function Resolve-WdWindowsTerminalApplication {
  $packages = @(
    Appx\Get-AppxPackage `
      -Name Microsoft.WindowsTerminal `
      -ErrorAction Stop |
      Where-Object {
        [string]$_.Name -ceq 'Microsoft.WindowsTerminal' -and
        [string]$_.PackageFamilyName -ceq
          'Microsoft.WindowsTerminal_8wekyb3d8bbwe' -and
        [string]$_.PublisherId -ceq '8wekyb3d8bbwe' -and
        [string]$_.Status -ceq 'Ok' -and
        [string]$_.SignatureKind -ceq 'Store'
      }
  )
  if ($packages.Count -ne 1) {
    throw 'expected exactly one healthy Store Windows Terminal package'
  }
  $programFiles = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ProgramFiles
  )
  $windowsAppsRoot = [IO.Path]::GetFullPath(
    (Join-Path $programFiles 'WindowsApps')
  ).TrimEnd('\')
  $installRoot = [IO.Path]::GetFullPath(
    [string]$packages[0].InstallLocation
  ).TrimEnd('\')
  if (-not $installRoot.StartsWith(
      $windowsAppsRoot + '\',
      [StringComparison]::OrdinalIgnoreCase
    )) {
    throw 'Windows Terminal package escaped the protected WindowsApps root'
  }
  $candidate = [IO.Path]::GetFullPath((Join-Path $installRoot 'wt.exe'))
  [void](Assert-WdFleetPathWithoutReparse `
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
    throw 'Windows Terminal did not resolve to the trusted Store application'
  }
  return $candidate
}

function Resolve-ApplicationPath {
  param([Parameter(Mandatory)] [string] $Name)

  if ($Name -cin @('codex.cmd', 'claude.cmd')) {
    return Resolve-WdNpmNativeApplication -Name $Name
  }
  if ($Name -ceq 'wt.exe') {
    return Resolve-WdWindowsTerminalApplication
  }
  throw "unsupported trusted reboot application: $Name"
}

function Test-ContainsApplySwitch {
  param([AllowEmptyString()] [string] $Text)
  return $Text -match (
    '(?i)(?:^|[\s"''])-(?:a|ap|app|appl|apply)' +
    '(?=[:\s"'']|$)'
  )
}

function Test-NamedCommandLineArgument {
  param(
    [AllowEmptyString()] [string] $CommandLine,
    [Parameter(Mandatory)] [string] $Name,
    [Parameter(Mandatory)] [string] $Value
  )

  if ([string]::IsNullOrWhiteSpace($CommandLine)) {
    return $false
  }
  $pattern = '(?i)(?:^|\s)-{0}\s+(?:"{1}"|{1})(?:\s|$)' -f
    [regex]::Escape($Name),
    [regex]::Escape($Value)
  return $CommandLine -match $pattern
}

function Test-DirectLegacyDriverAction {
  param(
    [AllowEmptyString()] [string] $Execute,
    [AllowEmptyString()] [string] $Arguments,
    [Parameter(Mandatory)] [string] $ExpectedScript
  )
  $stablePowerShell = [IO.Path]::Combine(
    [Environment]::SystemDirectory,
    'WindowsPowerShell',
    'v1.0',
    'powershell.exe'
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
    return Get-ScheduledTask `
      -TaskName $TaskName `
      -TaskPath '\' `
      -ErrorAction Stop
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

function Test-WdSupervisorTaskActionExact {
  param(
    [Parameter(Mandatory)] $Task,
    [Parameter(Mandatory)] [string] $ExpectedExecutable,
    [Parameter(Mandatory)] [string] $ExpectedArguments,
    [Parameter(Mandatory)] [string] $ExpectedWorkingDirectory
  )

  $actions = @($Task.Actions)
  if ($actions.Count -ne 1) {
    return $false
  }
  $action = $actions[0]
  return (
    ([string]$action.Execute).Equals(
      $ExpectedExecutable,
      [System.StringComparison]::OrdinalIgnoreCase
    ) -and
    [string]$action.Arguments -ceq $ExpectedArguments -and
    [string]$action.WorkingDirectory -ceq $ExpectedWorkingDirectory
  )
}

function Get-WdSingleScheduledTask {
  param([Parameter(Mandatory)] [string] $TaskName)

  $tasks = @(
    Get-ScheduledTask `
      -TaskName $TaskName `
      -TaskPath '\' `
      -ErrorAction Stop
  )
  if ($tasks.Count -ne 1 -or [string]$tasks[0].TaskPath -cne '\') {
    throw "$TaskName must resolve to exactly one root-path scheduled task"
  }
  return $tasks[0]
}

function Get-WdAccountSid {
  param([Parameter(Mandatory)] [string] $AccountName)

  try {
    $account = New-Object Security.Principal.NTAccount($AccountName)
    return [string]$account.Translate(
      [Security.Principal.SecurityIdentifier]
    ).Value
  } catch {
    throw "scheduled-task principal cannot be resolved to a SID: $AccountName"
  }
}

function Test-WdSupervisorTaskEnvelopeExact {
  param(
    [Parameter(Mandatory)] $Task,
    [Parameter(Mandatory)] [string] $ExpectedExecutable,
    [Parameter(Mandatory)] [string] $ExpectedArguments,
    [Parameter(Mandatory)] [string] $ExpectedWorkingDirectory,
    [Parameter(Mandatory)] [string] $ExpectedPrincipalSid,
    [Parameter(Mandatory)] [string] $ExpectedStartBoundary
  )

  if (-not (Test-WdSupervisorTaskActionExact `
      -Task $Task `
      -ExpectedExecutable $ExpectedExecutable `
      -ExpectedArguments $ExpectedArguments `
      -ExpectedWorkingDirectory $ExpectedWorkingDirectory
    )) {
    return $false
  }
  if (
    [string]$Task.TaskPath -cne '\' -or
    (Get-WdAccountSid -AccountName ([string]$Task.Principal.UserId)) -cne
      $ExpectedPrincipalSid -or
    [string]$Task.Principal.LogonType -cne 'Interactive' -or
    [string]$Task.Principal.RunLevel -cne 'Limited' -or
    [string]$Task.Settings.MultipleInstances -cne 'IgnoreNew' -or
    -not [bool]$Task.Settings.AllowDemandStart -or
    -not [bool]$Task.Settings.StartWhenAvailable -or
    -not [bool]$Task.Settings.Hidden -or
    [string]$Task.Settings.ExecutionTimeLimit -cne 'PT5M'
  ) {
    return $false
  }
  $triggers = @($Task.Triggers)
  if ($triggers.Count -ne 1) {
    return $false
  }
  $trigger = $triggers[0]
  return (
    [string]$trigger.CimClass.CimClassName -ceq 'MSFT_TaskTimeTrigger' -and
    [bool]$trigger.Enabled -and
    [string]$trigger.StartBoundary -ceq $ExpectedStartBoundary -and
    [string]$trigger.Repetition.Interval -ceq 'PT30M' -and
    [string]$trigger.Repetition.Duration -ceq 'P3650D' -and
    [bool]$trigger.Repetition.StopAtDurationEnd
  )
}

function Get-WdSupervisorTaskActivationPlan {
  param([Parameter(Mandatory)] $Task)

  $enabled = [bool]$Task.Settings.Enabled
  $state = [string]$Task.State
  if ($enabled -and $state -ceq 'Disabled') {
    throw 'WD-Supervisor task reports enabled=true with Disabled state'
  }
  if (-not $enabled -and $state -cne 'Disabled') {
    throw "WD-Supervisor task reports enabled=false with unexpected state '$state'"
  }
  return [pscustomobject]@{
    initially_enabled = $enabled
    enable_after_restore = -not $enabled
    summary = if ($enabled) {
      "already enabled; state=$state"
    } else {
      'held Disabled; Apply will enable only after verified fleet restore'
    }
  }
}

function Set-WdSupervisorTaskHeld {
  param(
    [Parameter(Mandatory)] [string] $TaskName,
    [Parameter(Mandatory)] [string] $ExpectedExecutable,
    [Parameter(Mandatory)] [string] $ExpectedArguments,
    [Parameter(Mandatory)] [string] $ExpectedWorkingDirectory,
    [Parameter(Mandatory)] [string] $ExpectedPrincipalSid,
    [Parameter(Mandatory)] [string] $ExpectedStartBoundary
  )

  $taskBeforeDisable = Get-WdSingleScheduledTask -TaskName $TaskName
  $mustStopActiveInstance = [string]$taskBeforeDisable.State -in @(
    'Running',
    'Queued',
    'Unknown'
  )
  [void](Disable-ScheduledTask `
      -TaskName $TaskName `
      -TaskPath '\' `
      -ErrorAction Stop)
  $task = Get-WdSingleScheduledTask -TaskName $TaskName
  if ([string]$task.State -in @('Running', 'Queued', 'Unknown')) {
    $mustStopActiveInstance = $true
  }
  if ($mustStopActiveInstance) {
    [void](Stop-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath '\' `
        -ErrorAction Stop)
  }
  $deadline = (Get-Date).AddSeconds(30)
  do {
    $task = Get-WdSingleScheduledTask -TaskName $TaskName
    if (
      -not [bool]$task.Settings.Enabled -and
      [string]$task.State -ceq 'Disabled'
    ) {
      break
    }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)
  if (
    [bool]$task.Settings.Enabled -or
    [string]$task.State -cne 'Disabled' -or
    -not (Test-WdSupervisorTaskEnvelopeExact `
      -Task $task `
      -ExpectedExecutable $ExpectedExecutable `
      -ExpectedArguments $ExpectedArguments `
      -ExpectedWorkingDirectory $ExpectedWorkingDirectory `
      -ExpectedPrincipalSid $ExpectedPrincipalSid `
      -ExpectedStartBoundary $ExpectedStartBoundary)
  ) {
    throw 'WD-Supervisor could not be returned to exact Disabled containment'
  }
}

function Enable-WdSupervisorTaskAfterRestore {
  param(
    [Parameter(Mandatory)] [string] $TaskName,
    [Parameter(Mandatory)] [string] $ExpectedExecutable,
    [Parameter(Mandatory)] [string] $ExpectedArguments,
    [Parameter(Mandatory)] [string] $ExpectedWorkingDirectory,
    [Parameter(Mandatory)] [string] $ExpectedPrincipalSid,
    [Parameter(Mandatory)] [string] $ExpectedStartBoundary,
    [ValidateRange(10, 300)] [int] $WaitSeconds = 120
  )

  $changed = $false
  try {
    $task = Get-WdSingleScheduledTask -TaskName $TaskName
    if (-not (Test-WdSupervisorTaskEnvelopeExact `
        -Task $task `
        -ExpectedExecutable $ExpectedExecutable `
        -ExpectedArguments $ExpectedArguments `
        -ExpectedWorkingDirectory $ExpectedWorkingDirectory `
        -ExpectedPrincipalSid $ExpectedPrincipalSid `
        -ExpectedStartBoundary $ExpectedStartBoundary
      )) {
      throw 'Tools supervisor action changed before activation'
    }
    $plan = Get-WdSupervisorTaskActivationPlan -Task $task
    $changed = [bool]$plan.enable_after_restore
    $healthStartedUtc = [DateTime]::UtcNow
    if ($changed) {
      [void](Enable-ScheduledTask `
          -TaskName $TaskName `
          -TaskPath '\' `
          -ErrorAction Stop)
    }

    $providerDeadline = (Get-Date).AddSeconds(10)
    do {
      $verified = Get-WdSingleScheduledTask -TaskName $TaskName
      if (
        [bool]$verified.Settings.Enabled -and
        [string]$verified.State -cne 'Disabled'
      ) {
        break
      }
      Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $providerDeadline)
    if (
      -not [bool]$verified.Settings.Enabled -or
      [string]$verified.State -ceq 'Disabled' -or
      -not (Test-WdSupervisorTaskEnvelopeExact `
        -Task $verified `
        -ExpectedExecutable $ExpectedExecutable `
        -ExpectedArguments $ExpectedArguments `
        -ExpectedWorkingDirectory $ExpectedWorkingDirectory `
        -ExpectedPrincipalSid $ExpectedPrincipalSid `
        -ExpectedStartBoundary $ExpectedStartBoundary)
    ) {
      throw 'Tools supervisor activation verification failed'
    }

    # Demand-start the exact task once even if StartWhenAvailable already
    # started it. MultipleInstances=IgnoreNew keeps the race idempotent, and a
    # fresh LastRunTime/result proves the registered scheduler path itself.
    [void](Start-ScheduledTask `
        -TaskName $TaskName `
        -TaskPath '\' `
        -ErrorAction Stop)
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
      Start-Sleep -Milliseconds 250
      $verified = Get-WdSingleScheduledTask -TaskName $TaskName
      $taskInfo = Get-ScheduledTaskInfo `
        -TaskName $TaskName `
        -TaskPath '\' `
        -ErrorAction Stop
      $lastRunUtc = ([DateTime]$taskInfo.LastRunTime).ToUniversalTime()
      $terminal = [string]$verified.State -ceq 'Ready'
      $freshRun = $lastRunUtc -ge $healthStartedUtc.AddSeconds(-2)
    } while ((-not $terminal -or -not $freshRun) -and (Get-Date) -lt $deadline)
    if (
      -not $terminal -or
      -not $freshRun -or
      [int64]$taskInfo.LastTaskResult -ne 0 -or
      -not [bool]$verified.Settings.Enabled -or
      [string]$verified.State -cne 'Ready' -or
      -not (Test-WdSupervisorTaskEnvelopeExact `
        -Task $verified `
        -ExpectedExecutable $ExpectedExecutable `
        -ExpectedArguments $ExpectedArguments `
        -ExpectedWorkingDirectory $ExpectedWorkingDirectory `
        -ExpectedPrincipalSid $ExpectedPrincipalSid `
        -ExpectedStartBoundary $ExpectedStartBoundary)
    ) {
      throw (
        'Tools supervisor scheduled-path health proof failed: ' +
        "state=$([string]$verified.State) result=$([string]$taskInfo.LastTaskResult)"
      )
    }
    return [pscustomobject]@{
      changed = $changed
      state = [string]$verified.State
      last_run_utc = $lastRunUtc.ToString('o')
      last_task_result = [int64]$taskInfo.LastTaskResult
    }
  } catch {
    $activationFailure = $_
    try {
      Set-WdSupervisorTaskHeld `
        -TaskName $TaskName `
        -ExpectedExecutable $ExpectedExecutable `
        -ExpectedArguments $ExpectedArguments `
        -ExpectedWorkingDirectory $ExpectedWorkingDirectory `
        -ExpectedPrincipalSid $ExpectedPrincipalSid `
        -ExpectedStartBoundary $ExpectedStartBoundary
    } catch {
      throw (
        "{0}; WD-Supervisor fail-closed containment also failed: {1}" -f
          $activationFailure.Exception.Message,
          $_.Exception.Message
      )
    }
    throw $activationFailure
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

function Test-WdProcessIdentitySetExact {
  param(
    [AllowEmptyCollection()] [object[]] $Expected,
    [AllowEmptyCollection()] [object[]] $Actual
  )

  $expectedKeys = @(
    @($Expected) |
      ForEach-Object {
        '{0}|{1}|{2}' -f
          [int]$_.ProcessId,
          [string]$_.CreationDate,
          [string]$_.CommandLine
      } |
      Sort-Object
  )
  $actualKeys = @(
    @($Actual) |
      ForEach-Object {
        '{0}|{1}|{2}' -f
          [int]$_.ProcessId,
          [string]$_.CreationDate,
          [string]$_.CommandLine
      } |
      Sort-Object
  )
  if ($expectedKeys.Count -ne $actualKeys.Count) { return $false }
  for ($index = 0; $index -lt $expectedKeys.Count; $index++) {
    if ([string]$expectedKeys[$index] -cne [string]$actualKeys[$index]) {
      return $false
    }
  }
  return $true
}

function Resolve-LanePinState {
  param(
    [Parameter(Mandatory)] $Lane,
    [Parameter(Mandatory)] [string] $PrimaryRepoRoot,
    [Parameter(Mandatory)] [string] $ActualBranch,
    [Parameter(Mandatory)] [string] $ActualHead,
    [ValidateRange(0, 1)] [int] $LiveCount,
    [bool] $LiveGenerationAttested = $false
  )

  $branchExact = $ActualBranch -ceq [string]$Lane.branch
  $headExact = $ActualHead -ceq [string]$Lane.head
  $resumePolicy = [string]$Lane.resume_policy
  if ($resumePolicy -cnotin @('pinned', 'current_worktree')) {
    throw "lane '$($Lane.agent)' has unsupported resume_policy '$resumePolicy'"
  }
  if ([string]::IsNullOrWhiteSpace($ActualBranch)) {
    throw "lane '$($Lane.agent)' cannot resume a detached HEAD"
  }
  if ($ActualHead -cnotmatch '^[0-9a-f]{40}$') {
    throw "lane '$($Lane.agent)' resolved a malformed HEAD: $ActualHead"
  }
  if ($branchExact -and $headExact) {
    return [pscustomobject]@{
      exact = $true
      summary = 'branch/head exact'
    }
  }
  $allowCurrentWorktree = (
    $resumePolicy -ceq 'current_worktree' -and
    ($LiveCount -eq 0 -or ($LiveCount -eq 1 -and $LiveGenerationAttested))
  )
  if ($allowCurrentWorktree) {
    return [pscustomobject]@{
      exact = $false
      summary = if ($LiveCount -eq 0) {
        'cold resume from canonical current worktree'
      } else {
        'attested live process; current worktree drift accepted without relaunch'
      }
    }
  }
  if (-not $branchExact) {
    throw (
      "lane '$($Lane.agent)' branch mismatch: expected '$($Lane.branch)', " +
      "found '$ActualBranch'"
    )
  }
  throw (
    "lane '$($Lane.agent)' HEAD mismatch: expected '$($Lane.head)', " +
    "found '$ActualHead'"
  )
}

function Get-NamedCommandLineArgumentValue {
  param(
    [AllowEmptyString()] [string] $CommandLine,
    [Parameter(Mandatory)] [string] $Name
  )

  if ([string]::IsNullOrWhiteSpace($CommandLine)) {
    return ''
  }
  $pattern = '(?i)(?:^|\s)-{0}\s+(?:"(?<quoted>[^"]+)"|(?<plain>\S+))' -f
    [regex]::Escape($Name)
  $match = [regex]::Match($CommandLine, $pattern)
  if (-not $match.Success) {
    return ''
  }
  if ($match.Groups['quoted'].Success) {
    return [string]$match.Groups['quoted'].Value
  }
  return [string]$match.Groups['plain'].Value
}

function Test-LaneGenerationAttestation {
  param(
    [Parameter(Mandatory)] $Lane,
    [Parameter(Mandatory)] $Process
  )

  try {
    if ([string]$Process.Name -cnotmatch '^(?i:powershell|pwsh)\.exe$') {
      return $false
    }
    $commandLine = [string]$Process.CommandLine
    $launcher = Resolve-NormalizedPath -Path (
      Get-NamedCommandLineArgumentValue -CommandLine $commandLine -Name 'File'
    )
    $manifestArgument = Resolve-NormalizedPath -Path (
      Get-NamedCommandLineArgumentValue `
        -CommandLine $commandLine `
        -Name 'ManifestPath'
    )
    $bundleStore = Resolve-NormalizedPath -Path 'C:\Python\wd-reboot-bundles'
    $machineLauncher = Resolve-NormalizedPath -Path 'C:\Python\start-wd-agent.ps1'
    $bundleRoot = if ($launcher.Equals(
        $machineLauncher,
        [System.StringComparison]::OrdinalIgnoreCase
      )) {
      Resolve-NormalizedPath -Path (Split-Path -Parent $manifestArgument)
    } else {
      Resolve-NormalizedPath -Path (Split-Path -Parent $launcher)
    }
    if (
      [IO.Path]::GetFileName($launcher) -cne 'start-wd-agent.ps1' -or
      -not (Split-Path -Parent $bundleRoot).Equals(
        $bundleStore,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      [IO.Path]::GetFileName($bundleRoot) -cnotmatch '^[0-9a-f]{40}$'
    ) {
      return $false
    }
    $bundleCommit = [IO.Path]::GetFileName($bundleRoot)
    $fleetPath = Join-Path $bundleRoot 'wd-fleet.json'
    $deploymentPath = Join-Path $bundleRoot 'deployment-manifest.json'
    if (
      -not $manifestArgument.Equals(
        (Resolve-NormalizedPath -Path $fleetPath),
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      -not (Test-NamedCommandLineArgument `
        -CommandLine $commandLine `
        -Name 'Agent' `
        -Value ([string]$Lane.agent))
    ) {
      return $false
    }

    $deploymentSnapshot = Read-Utf8FleetSnapshot -Path $deploymentPath
    $deployment = [string]$deploymentSnapshot.Text |
      ConvertFrom-Json -ErrorAction Stop
    if (
      [int]$deployment.schema_version -ne 1 -or
      [string]$deployment.source_commit -cne $bundleCommit
    ) {
      return $false
    }
    foreach ($name in @('start-wd-agent.ps1', 'wd-fleet.json')) {
      $expectedHash = [string]$deployment.files.PSObject.Properties[$name].Value
      $path = Join-Path $bundleRoot $name
      if (
        [string]::IsNullOrWhiteSpace($expectedHash) -or
        -not (Test-Path -LiteralPath $path -PathType Leaf) -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne $expectedHash
      ) {
        return $false
      }
    }
    $launcherText = Get-Content `
      -LiteralPath (Join-Path $bundleRoot 'start-wd-agent.ps1') `
      -Raw `
      -ErrorAction Stop
    if ($launcherText.IndexOf(
        'ExpectedManifestHash',
        [System.StringComparison]::Ordinal
      ) -ge 0) {
      $commandAnchor = Get-NamedCommandLineArgumentValue `
        -CommandLine $commandLine `
        -Name 'ExpectedManifestHash'
      if (
        $commandAnchor -cnotmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$deploymentSnapshot.Hash -cne
          $commandAnchor.ToUpperInvariant()
      ) {
        return $false
      }
    }
    $liveFleetSnapshot = Read-Utf8FleetSnapshot -Path $fleetPath
    $expectedLiveFleetHash = [string](
      $deployment.files.PSObject.Properties['wd-fleet.json'].Value
    )
    if ([string]$liveFleetSnapshot.Hash -cne $expectedLiveFleetHash) {
      return $false
    }
    $liveManifest = [string]$liveFleetSnapshot.Text |
      ConvertFrom-Json -ErrorAction Stop
    $liveLane = @(
      @($liveManifest.lanes) |
        Where-Object { [string]$_.agent -ceq [string]$Lane.agent }
    )
    if ($liveLane.Count -ne 1) {
      return $false
    }
    $liveLane = $liveLane[0]
    if (
      [string]$liveLane.agent_uuid -cne [string]$Lane.agent_uuid -or
      [string]$liveLane.role -cne [string]$Lane.role -or
      [string]$liveLane.branch -cne [string]$Lane.branch -or
      [string]$liveLane.head -cne [string]$Lane.head -or
      -not ([string]$liveLane.worktree).Equals(
        [string]$Lane.worktree,
        [System.StringComparison]::OrdinalIgnoreCase
      )
    ) {
      return $false
    }

    $runId = Get-NamedCommandLineArgumentValue `
      -CommandLine $commandLine `
      -Name 'RunId'
    $handshakeDirectory = Resolve-NormalizedPath -Path (
      Get-NamedCommandLineArgumentValue `
        -CommandLine $commandLine `
        -Name 'HandshakeDirectory'
    )
    $handshakeRoot = Resolve-NormalizedPath -Path (
      'C:\Python\wd-reboot-runtime\handshakes'
    )
    if (
      [string]::IsNullOrWhiteSpace($runId) -or
      -not (Split-Path -Parent $handshakeDirectory).Equals(
        $handshakeRoot,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      [IO.Path]::GetFileName($handshakeDirectory) -cne $runId
    ) {
      return $false
    }
    $handshakePath = Join-Path $handshakeDirectory (
      '{0}.json' -f [string]$Lane.agent
    )
    $handshake = (Read-NonEmptyFile `
      -Path $handshakePath `
      -Label 'live lane handshake') | ConvertFrom-Json -ErrorAction Stop
    $expectedCliExecutable = Resolve-ApplicationPath -Name ([string]$Lane.cli)
    $expectedCliHash = (
      Get-FileHash -LiteralPath $expectedCliExecutable -Algorithm SHA256
    ).Hash
    if (
      [int]$handshake.schema_version -ne 1 -or
      [string]$handshake.status -cne 'bridge_bootstrapped' -or
      [int]$handshake.pid -ne [int]$Process.ProcessId -or
      [string]$handshake.agent -cne [string]$Lane.agent -or
      [string]$handshake.agent_uuid -cne [string]$Lane.agent_uuid -or
      [string]$handshake.role -cne [string]$Lane.role -or
      [string]$handshake.run_id -cne $runId -or
      [string]$handshake.baseline_branch -cne [string]$Lane.branch -or
      [string]$handshake.baseline_head -cne [string]$Lane.head -or
      [string]$handshake.resume_policy -cne [string]$Lane.resume_policy -or
      [string]$handshake.model -cne [string]$Lane.model -or
      [string]$handshake.effort -cne [string]$Lane.effort -or
      [string]$handshake.model_selection -cne 'explicit' -or
      -not ([string]$handshake.cli_executable).Equals(
        $expectedCliExecutable,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      [string]$handshake.cli_executable_sha256 -cne $expectedCliHash -or
      -not (Test-WdJsonBooleanTrue -Object $handshake -Name 'append_canary') -or
      [string]$handshake.append_canary_task_id -cne
        "wd-append-canary-$runId" -or
      -not (Test-WdJsonIntegerRange `
        -Object $handshake `
        -Name 'append_canary_latency_ms' `
        -Minimum 0 `
        -Maximum 5000) -or
      [string]$handshake.bundle_generation -cne $bundleCommit -or
      [string]$handshake.branch -cnotmatch '^\S+$' -or
      [string]$handshake.head -cnotmatch '^[0-9a-f]{40}$' -or
      -not (Test-WdJsonBooleanTrue `
        -Object $handshake `
        -Name 'target_state_manifested') -or
      [string]$handshake.target_state_id -cne 'wd-swarm-target-state-v1' -or
      -not ([string]$handshake.worktree).Equals(
        [string]$Lane.worktree,
        [System.StringComparison]::OrdinalIgnoreCase
      )
    ) {
      return $false
    }
    $processCreated = ConvertTo-UtcDateTimeOffset `
      -Value $Process.CreationDate `
      -Label 'live lane process creation'
    $handshakeCreated = ConvertTo-UtcDateTimeOffset `
      -Value $handshake.created_at_utc `
      -Label 'live lane handshake creation'
    $canaryCreated = ConvertTo-UtcDateTimeOffset `
      -Value $handshake.append_canary_event_utc `
      -Label 'live lane append canary creation'
    $ageAtHandshake = $handshakeCreated - $processCreated
    $ageAtCanary = $canaryCreated - $processCreated
    return (
      $ageAtHandshake.TotalSeconds -ge 0 -and
      $ageAtHandshake.TotalMinutes -le 5 -and
      $ageAtCanary.TotalSeconds -ge -5 -and
      $ageAtCanary.TotalMinutes -le 5 -and
      $canaryCreated -le $handshakeCreated
    )
  } catch {
    return $false
  }
}

function Test-ToolsProcessReadiness {
  param(
    [Parameter(Mandatory)] $Process,
    [Parameter(Mandatory)] $ToolsConfig,
    [Parameter(Mandatory)] [string] $Generation
  )

  try {
    $readinessPath = Resolve-NormalizedPath -Path (
      [string]$ToolsConfig.readiness_path
    )
    if (-not (Test-Path -LiteralPath $readinessPath -PathType Leaf)) {
      return $false
    }
    $record = (Get-Content -LiteralPath $readinessPath -Raw) |
      ConvertFrom-Json -ErrorAction Stop
    $expectedCodex = Resolve-ApplicationPath -Name 'codex.cmd'
    $expectedCodexHash = (
      Get-FileHash -LiteralPath $expectedCodex -Algorithm SHA256
    ).Hash
    $expectedPython = Resolve-NormalizedPath -Path (
      [string]$ToolsConfig.python_executable
    )
    [void](Assert-WdFleetPathWithoutReparse `
        -Path $expectedPython `
        -TrustedRoot ([IO.Path]::GetPathRoot($expectedPython)) `
        -ExpectedType Leaf)
    $expectedPythonHash = (
      Get-FileHash -LiteralPath $expectedPython -Algorithm SHA256
    ).Hash
    $resumeCurrent = [string]$ToolsConfig.resume_policy -ceq 'current_worktree'
    $pinValid = if ($resumeCurrent) {
      [string]$record.branch -cmatch '^\S+$' -and
      [string]$record.head -cmatch '^[0-9a-f]{40}$' -and
      [string]$record.baseline_branch -ceq [string]$ToolsConfig.branch -and
      [string]$record.baseline_head -ceq [string]$ToolsConfig.head
    } else {
      [string]$record.branch -ceq [string]$ToolsConfig.branch -and
      [string]$record.head -ceq [string]$ToolsConfig.head
    }
    if (
      [string]$record.schema -cne 'wd.tools-consumer-ready.v1' -or
      [string]$record.generation -cne $Generation -or
      [int]$record.pid -ne [int]$Process.ProcessId -or
      -not $pinValid -or
      [string]$record.resume_policy -cne [string]$ToolsConfig.resume_policy -or
      [string]$record.model -cne [string]$ToolsConfig.model -or
      [string]$record.reasoning_effort -cne [string]$ToolsConfig.reasoning_effort -or
      -not ([string]$record.codex_command).Equals(
        $expectedCodex,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      [string]$record.codex_command_sha256 -cne $expectedCodexHash -or
      -not ([string]$record.python_executable).Equals(
        $expectedPython,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      [string]$record.python_executable_sha256 -cne $expectedPythonHash -or
      -not (Test-WdJsonBooleanTrue `
        -Object $record `
        -Name 'target_state_manifested') -or
      [string]$record.run_id -cnotmatch '^[A-Za-z0-9._-]{1,128}$' -or
      [string]$record.session_id -cne [string]$record.run_id -or
      -not (Test-WdJsonBooleanTrue -Object $record -Name 'append_canary') -or
      [string]$record.append_canary_task_id -cne
        "wd-append-canary-$([string]$record.run_id)" -or
      -not (Test-WdJsonIntegerRange `
        -Object $record `
        -Name 'append_canary_latency_ms' `
        -Minimum 0 `
        -Maximum 5000) -or
      [string]$record.target_state_id -cne 'wd-swarm-target-state-v1' -or
      -not ([string]$record.config_path).Equals(
        [string]$ToolsConfig.config_path,
        [System.StringComparison]::OrdinalIgnoreCase
      ) -or
      -not ([string]$record.worktree).Equals(
        [string]$ToolsConfig.worktree,
        [System.StringComparison]::OrdinalIgnoreCase
      )
    ) {
      return $false
    }
    $processCreated = ConvertTo-UtcDateTimeOffset `
      -Value $Process.CreationDate `
      -Label 'Tools process creation'
    $recordCreated = ConvertTo-UtcDateTimeOffset `
      -Value $record.process_start_utc `
      -Label 'Tools readiness process creation'
    $readyAt = ConvertTo-UtcDateTimeOffset `
      -Value $record.ready_at_utc `
      -Label 'Tools readiness creation'
    $canaryAt = ConvertTo-UtcDateTimeOffset `
      -Value $record.append_canary_event_utc `
      -Label 'Tools append canary creation'
    return (
      [Math]::Abs(($recordCreated - $processCreated).TotalSeconds) -le 1 -and
      $readyAt -ge $recordCreated -and
      $canaryAt -ge $recordCreated.AddSeconds(-5) -and
      $canaryAt -le $readyAt
    )
  } catch {
    return $false
  }
}

function Write-ToolsReadinessWarning {
  param([Parameter(Mandatory)] $ToolsConfig)

  $readinessPath = Resolve-NormalizedPath -Path (
    [string]$ToolsConfig.readiness_path
  )
  $record = (Read-NonEmptyFile `
    -Path $readinessPath `
    -Label 'Tools readiness record') |
    ConvertFrom-Json -ErrorAction Stop
  if ([string]$record.status -ceq 'degraded') {
    Write-Warning (
      'codex-tools-1 is headless and live, but its initial Codex tick was ' +
      "degraded: disposition=$([string]$record.initial_tick_disposition) " +
      "exit_code=$([string]$record.initial_tick_exit_code) " +
      "timed_out=$([string]$record.initial_tick_timed_out) " +
      "log=$([string]$record.initial_tick_log_path)"
    )
  }
}

function Get-ToolsProcessState {
  param(
    [Parameter(Mandatory)] $ToolsConfig,
    [Parameter(Mandatory)] [string] $Generation,
    [Parameter(Mandatory)] [object[]] $Processes
  )
  $launcher = Resolve-NormalizedPath -Path ([string]$ToolsConfig.launcher_script)
  $configPath = Resolve-NormalizedPath -Path ([string]$ToolsConfig.config_path)
  $generation = $Generation.ToLowerInvariant()
  if ($Generation -cne $generation -or $generation -cnotmatch '^[0-9a-f]{40}$') {
    throw 'Tools process generation must be a full lowercase Git commit'
  }
  $agentPattern = '(?i)(?:^|\s)-Agent\s+["'']?' +
    [regex]::Escape([string]$ToolsConfig.agent) + '(?:["'']?)(?:\s|$)'
  $wrappers = @(
    $Processes | Where-Object {
      $commandLine = [string]$_.CommandLine
      [string]$_.Name -match '^(?i:powershell|pwsh)\.exe$' -and
      (Test-NamedCommandLineArgument `
        -CommandLine $commandLine `
        -Name 'File' `
        -Value $launcher)
    }
  )
  $generationWrappers = @(
    $wrappers | Where-Object {
      $commandLine = [string]$_.CommandLine
      (Test-NamedCommandLineArgument `
        -CommandLine $commandLine `
        -Name 'ConfigPath' `
        -Value $configPath) -and
      (Test-NamedCommandLineArgument `
        -CommandLine $commandLine `
        -Name 'Generation' `
        -Value $generation)
    }
  )
  $current = @(
    $generationWrappers | Where-Object {
      Test-ToolsProcessReadiness `
        -Process $_ `
        -ToolsConfig $ToolsConfig `
        -Generation $generation
    }
  )
  $currentIds = @($current | ForEach-Object { [int]$_.ProcessId })
  $starting = @(
    $generationWrappers |
      Where-Object { [int]$_.ProcessId -notin $currentIds }
  )
  $generationIds = @(
    $generationWrappers | ForEach-Object { [int]$_.ProcessId }
  )
  $stale = @(
    $wrappers | Where-Object { [int]$_.ProcessId -notin $generationIds }
  )
  $legacy = @(
    $Processes | Where-Object {
      $commandLine = [string]$_.CommandLine
      $commandLine.IndexOf(
        'Start-AgentBridgeConsumerLoop.ps1',
        [System.StringComparison]::OrdinalIgnoreCase
      ) -ge 0 -and
      $commandLine -match $agentPattern
    }
  )
  return [pscustomobject]@{
    current = @($current)
    starting = @($starting)
    stale = @($stale)
    legacy = @($legacy)
  }
}

function Wait-WdToolsCurrentProcess {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)] $ToolsConfig,
    [Parameter(Mandatory)] [string] $Generation,
    [Parameter(Mandatory)] [int] $TimeoutSeconds,
    [scriptblock] $ProcessSnapshotAction = { Get-AllProcessSnapshots },
    [int] $PollMilliseconds = 500
  )

  if ($TimeoutSeconds -lt 0) {
    throw 'Tools readiness timeout cannot be negative'
  }
  if ($PollMilliseconds -lt 0) {
    throw 'Tools readiness poll interval cannot be negative'
  }

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    $processes = @(& $ProcessSnapshotAction)
    $state = Get-ToolsProcessState `
      -ToolsConfig $ToolsConfig `
      -Generation $Generation `
      -Processes $processes
    $current = @($state.current)
    $starting = @($state.starting)
    $stale = @($state.stale)
    $legacy = @($state.legacy)
    if (
      $current.Count -eq 1 -and
      $starting.Count -eq 0 -and
      $stale.Count -eq 0 -and
      $legacy.Count -eq 0
    ) {
      return $state
    }
    if ((Get-Date) -ge $deadline) {
      break
    }
    if ($PollMilliseconds -gt 0) {
      Start-Sleep -Milliseconds $PollMilliseconds
    }
  } while ($true)

  throw (
    'Tools supervisor did not establish exactly one current-generation ' +
    "consumer; current/starting/stale/legacy=$($current.Count)/" +
    "$($starting.Count)/$($stale.Count)/$($legacy.Count)"
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
  $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
  $deploymentSnapshot = Read-Utf8FleetSnapshot -Path $deploymentManifestPath
  if (
    $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
    [string]$deploymentSnapshot.Hash -cne
      $expectedManifestHash.ToUpperInvariant()
  ) {
    throw 'deployed reboot manifest is not externally anchored'
  }

  $deployment = [string]$deploymentSnapshot.Text |
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
  if (
    (Get-FileHash -LiteralPath $deploymentManifestPath -Algorithm SHA256).Hash -cne
      $expectedManifestHash.ToUpperInvariant()
  ) {
    throw 'deployed reboot manifest changed during bundle verification'
  }
  Write-Host ("  bundle: verified {0} deployed file hash(es)" -f $fileProperties.Count)
  return 'deployed'
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  throw "fleet manifest is missing: $ManifestPath"
}
$manifestSnapshot = Read-Utf8FleetSnapshot -Path $ManifestPath
$fixedDeploymentManifest = Join-Path $PSScriptRoot 'deployment-manifest.json'
if (Test-Path -LiteralPath $fixedDeploymentManifest -PathType Leaf) {
  $bundledFleetPath = Resolve-NormalizedPath -Path (
    Join-Path $PSScriptRoot 'wd-fleet.json'
  )
  if (-not (Resolve-NormalizedPath -Path $ManifestPath).Equals(
      $bundledFleetPath,
      [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw 'deployed fleet launcher requires its bundled wd-fleet.json'
  }
  $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
  $deploymentAnchorSnapshot = Read-Utf8FleetSnapshot `
    -Path $fixedDeploymentManifest
  if (
    $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
    [string]$deploymentAnchorSnapshot.Hash -cne
      $expectedManifestHash.ToUpperInvariant()
  ) {
    throw 'fleet deployment manifest is not externally anchored'
  }
  $bundleManifestAnchor = $expectedManifestHash.ToUpperInvariant()
  $deploymentAnchor = [string]$deploymentAnchorSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
  $fleetHashProperty = $deploymentAnchor.files.PSObject.Properties['wd-fleet.json']
  if (
    $null -eq $fleetHashProperty -or
    [string]$manifestSnapshot.Hash -cne [string]$fleetHashProperty.Value
  ) {
    throw 'loaded fleet manifest does not match the externally anchored bundle'
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
$script:WdGitExecutable = Resolve-WdFleetGitApplication `
  -ConfiguredPath ([string]$gitProperty.Value)
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
[void](Read-NonEmptyFile -Path $targetStatePath -Label 'fleet target state')
if (
  (Get-FileHash -LiteralPath $targetStatePath -Algorithm SHA256).Hash -cne
    [string]$targetState.sha256
) {
  throw 'fleet target-state document hash mismatch'
}
if (@($manifest.lanes).Count -ne 4) {
  throw "fleet manifest must pin exactly four interactive lanes"
}

Write-Host ''
Write-Host '=== WaggleDance reboot preflight ===' -ForegroundColor Cyan
$bundleMode = Assert-DeployedBundle -Manifest $manifest
Assert-WdLauncherBundleMode `
  -BundleMode $bundleMode `
  -LauncherMode $launcherMode
$bundleGeneration = if ($bundleMode -ceq 'deployed') {
  [string]$deploymentAnchor.source_commit
} else {
  Invoke-CheckedGit -Worktree $PSScriptRoot -Arguments @('rev-parse', 'HEAD')
}
$bundleGeneration = $bundleGeneration.ToLowerInvariant()
if ($bundleGeneration -cnotmatch '^[0-9a-f]{40}$') {
  throw 'reboot bundle generation must be a full lowercase Git commit'
}
if (
  $bundleMode -ceq 'deployed' -and
  [IO.Path]::GetFileName(
    (Resolve-NormalizedPath -Path $PSScriptRoot)
  ) -cne $bundleGeneration
) {
  throw 'deployed reboot bundle directory does not match its source commit'
}

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
$agentLauncherTarget = Join-Path $PSScriptRoot 'start-wd-agent.ps1'
$agentLauncher = 'C:\Python\start-wd-agent.ps1'
[void](Read-NonEmptyFile -Path $resolver -Label 'Grok model resolver')
[void](Read-NonEmptyFile -Path $agentLauncherTarget -Label 'per-lane launcher target')
if ($bundleMode -ceq 'deployed') {
  [void](Read-NonEmptyFile `
    -Path $agentLauncher `
    -Label 'stable per-lane forwarding launcher')
} elseif (-not (Test-Path -LiteralPath $agentLauncher -PathType Leaf)) {
  Write-Warning (
    "DryRun: stable per-lane launcher will be installed at $agentLauncher"
  )
}

$expectedCommonGit = Resolve-NormalizedPath -Path ([string]$manifest.repo_common_git_dir)
$processes = Get-AllProcessSnapshots
$laneStates = @()
$expectedLaneRuntimes = @{
  'codex-lead-1' = [pscustomobject]@{ cli = 'codex.cmd'; model = 'gpt-5.6-sol'; effort = 'ultra' }
  'claude-rco-1' = [pscustomobject]@{ cli = 'claude.cmd'; model = 'sonnet'; effort = 'max' }
  'claude-rco-2' = [pscustomobject]@{ cli = 'claude.cmd'; model = 'sonnet'; effort = 'max' }
  'fable-5' = [pscustomobject]@{ cli = 'claude.cmd'; model = 'fable'; effort = 'max' }
}
foreach ($lane in @($manifest.lanes)) {
  $expectedRuntime = $expectedLaneRuntimes[[string]$lane.agent]
  if (
    $null -eq $expectedRuntime -or
    [string]$lane.cli -cne [string]$expectedRuntime.cli -or
    [string]$lane.model -cne [string]$expectedRuntime.model -or
    [string]$lane.effort -cne [string]$expectedRuntime.effort
  ) {
    throw "lane '$($lane.agent)' runtime selection differs from the supported fleet contract"
  }
  $worktree = Resolve-NormalizedPath -Path ([string]$lane.worktree)
  $dedicatedProperty = $lane.PSObject.Properties['require_dedicated_worktree']
  if ($null -eq $dedicatedProperty -or $dedicatedProperty.Value -isnot [bool]) {
    throw "lane '$($lane.agent)' is missing boolean require_dedicated_worktree"
  }
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
  $live = @(Get-LaneProcesses -Lane $lane -Processes $processes)
  if ($live.Count -gt 1) {
    throw "duplicate live lane '$($lane.agent)' PID(s): $(@($live.ProcessId) -join ',')"
  }
  $liveGenerationAttested = (
    $live.Count -eq 1 -and
    (Test-LaneGenerationAttestation -Lane $lane -Process $live[0])
  )
  $pinLiveCount = if ($bundleMode -ceq 'source' -and $DryRun) {
    # A source rehearsal cannot launch a duplicate. Model the next cold boot
    # from the canonical current worktree even when the already-live process
    # belongs to the previously deployed handshake schema.
    0
  } else {
    $live.Count
  }
  $pinState = Resolve-LanePinState `
    -Lane $lane `
    -PrimaryRepoRoot ([string]$manifest.primary_repo_root) `
    -ActualBranch $actualBranch `
    -ActualHead $actualHead `
    -LiveCount $pinLiveCount `
    -LiveGenerationAttested:$liveGenerationAttested
  if (-not [bool]$pinState.exact) {
    Write-Warning (
      "lane '$($lane.agent)' uses resume_policy=current_worktree and has moved from baseline " +
      "$($lane.branch)@$($lane.head) to $actualBranch@$actualHead"
    )
  }
  $trustedSessionStarter = if ($bundleMode -ceq 'deployed') {
    Join-Path $PSScriptRoot (
      'tools-bootstrap\.agent-bridge\bin\Start-AgentBridgeSession.ps1'
    )
  } else {
    Join-Path (
      Resolve-NormalizedPath -Path (Join-Path $PSScriptRoot '..\..\..')
    ) '.agent-bridge\bin\Start-AgentBridgeSession.ps1'
  }
  [void](Read-NonEmptyFile `
    -Path $trustedSessionStarter `
    -Label "lane '$($lane.agent)' trusted bridge starter")
  [void](Read-NonEmptyFile -Path ([string]$lane.prompt) -Label "lane '$($lane.agent)' role prompt")
  [void](Read-NonEmptyFile -Path ([string]$lane.handoff) -Label "lane '$($lane.agent)' handoff")

  $laneStates += [pscustomobject]@{
    lane = $lane
    live = $live
    pin_state = $pinState
    actual_branch = $actualBranch
    actual_head = $actualHead
  }
  Write-Host (
    "  lane {0}: {1}; live launchers={2}" -f
      $lane.agent,
      [string]$pinState.summary,
      $live.Count
  )
}

$codexPath = Resolve-ApplicationPath -Name 'codex.cmd'
$claudePath = Resolve-ApplicationPath -Name 'claude.cmd'
$wtPath = Resolve-ApplicationPath -Name 'wt.exe'
$codexHash = (Get-FileHash -LiteralPath $codexPath -Algorithm SHA256).Hash
$claudeHash = (Get-FileHash -LiteralPath $claudePath -Algorithm SHA256).Hash
Write-Host '  Codex version probe:'
$codexVersion = Invoke-CheckedNative -Path $codexPath -Arguments @('--version') -Label 'codex version probe'
Write-Host '  Codex update command probe:'
[void](Invoke-CheckedNative -Path $codexPath -Arguments @('update', '--help') -Label 'codex update help')
Write-Host '  Claude Code version probe:'
$claudeVersion = Invoke-CheckedNative -Path $claudePath -Arguments @('--version') -Label 'claude version probe'
Write-Host '  Claude Code update command probe:'
[void](Invoke-CheckedNative -Path $claudePath -Arguments @('update', '--help') -Label 'claude update help')

$containment = $manifest.merge_driver_containment
$standingTask = Get-ScheduledTask `
  -TaskName ([string]$containment.standing_task) `
  -TaskPath '\' `
  -ErrorAction Stop
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
$bundleToolsConfig = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
$bundleToolsLauncher = Join-Path $PSScriptRoot 'start-wd-tools-consumer.ps1'
[void](Read-NonEmptyFile -Path $bundleToolsConfig -Label 'bundled Tools consumer config')
[void](Read-NonEmptyFile -Path $bundleToolsLauncher -Label 'bundled Tools consumer launcher')
$supervisorTask = Get-WdSingleScheduledTask `
  -TaskName ([string]$toolsConfig.task_name)
[void](Read-NonEmptyFile -Path ([string]$toolsConfig.supervisor_script) -Label 'Tools supervisor wrapper')
$expectedSupervisorExecutable = [IO.Path]::Combine(
  [Environment]::SystemDirectory,
  'WindowsPowerShell',
  'v1.0',
  'powershell.exe'
)
$expectedSupervisorArguments = (
  '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass ' +
  '-File "{0}" -Apply' -f [string]$toolsConfig.supervisor_script
)
if (@($supervisorTask.Triggers).Count -ne 1) {
  throw 'Tools supervisor must have exactly one trigger'
}
$expectedSupervisorPrincipalSid = [string](
  [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
)
$expectedSupervisorStartBoundary = [string]$supervisorTask.Triggers[0].StartBoundary
if (-not (Test-WdSupervisorTaskEnvelopeExact `
    -Task $supervisorTask `
    -ExpectedExecutable $expectedSupervisorExecutable `
    -ExpectedArguments $expectedSupervisorArguments `
    -ExpectedWorkingDirectory 'C:\Python' `
    -ExpectedPrincipalSid $expectedSupervisorPrincipalSid `
    -ExpectedStartBoundary $expectedSupervisorStartBoundary
  )) {
  throw 'Tools supervisor action is not the exact stable registered tuple'
}
$supervisorTaskActivation = Get-WdSupervisorTaskActivationPlan -Task $supervisorTask
Write-Host ("  WD-Supervisor activation: {0}" -f [string]$supervisorTaskActivation.summary)

$toolsSnapshotPath = if ($bundleMode -ceq 'source') {
  $bundleToolsConfig
} else {
  [string]$toolsConfig.snapshot_path
}
[void](Read-NonEmptyFile -Path $toolsSnapshotPath -Label 'Tools supervisor snapshot')
$snapshot = (Get-Content -LiteralPath $toolsSnapshotPath -Raw) |
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
  [string]$toolsSnapshot.expected_branch -cne [string]$toolsConfig.branch -or
  [string]$toolsSnapshot.expected_head -cne [string]$toolsConfig.head -or
  [string]$toolsSnapshot.resume_policy -cne [string]$toolsConfig.resume_policy -or
  [string]$toolsSnapshot.model -cne [string]$toolsConfig.model -or
  [string]$toolsSnapshot.reasoning_effort -cne [string]$toolsConfig.reasoning_effort -or
  -not ([string]$toolsSnapshot.python_executable).Equals(
    [string]$toolsConfig.python_executable,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  [bool]$toolsSnapshot.require_dedicated_worktree -ne
    [bool]$toolsConfig.require_dedicated_worktree -or
  -not ([string]$toolsSnapshot.primary_repo_root).Equals(
    [string]$manifest.primary_repo_root,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  -not ([string]$toolsSnapshot.expected_common_git_dir).Equals(
    [string]$manifest.repo_common_git_dir,
    [System.StringComparison]::OrdinalIgnoreCase
  )
) {
  throw 'Tools supervisor snapshot has inconsistent runtime identity pins'
}
if (-not ([string]$snapshot.watchers.git_executable).Equals(
    [string]$manifest.git_executable,
    [System.StringComparison]::OrdinalIgnoreCase
  )) {
  throw 'Tools supervisor snapshot has an inconsistent Git executable pin'
}
if ([bool]$toolsConfig.require_dedicated_worktree) {
  foreach ($lane in @($manifest.lanes)) {
    if ((Resolve-NormalizedPath -Path ([string]$lane.worktree)).Equals(
        (Resolve-NormalizedPath -Path ([string]$toolsConfig.worktree)),
        [System.StringComparison]::OrdinalIgnoreCase
      )) {
      throw "Tools worktree must be dedicated and not shared with lane '$($lane.agent)'"
    }
  }
}
if (
  [string]$toolsSnapshot.launcher_script -cne [string]$toolsConfig.launcher_script -or
  [string]$toolsSnapshot.config_path -cne [string]$toolsConfig.config_path -or
  -not ([string]$toolsSnapshot.readiness_path).Equals(
    [string]$toolsConfig.readiness_path,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  -not ([string]$toolsSnapshot.replacement_conflict_path).Equals(
    [string]$toolsConfig.replacement_conflict_path,
    [System.StringComparison]::OrdinalIgnoreCase
  )
) {
  throw (
    'Tools supervisor snapshot has an unexpected launcher, config, readiness, ' +
    'or replacement-conflict path'
  )
}
if ($toolsSnapshot.PSObject.Properties['executable']) {
  throw 'Tools supervisor snapshot must not pin an executable path'
}
if ($bundleMode -ceq 'deployed') {
  [void](Read-NonEmptyFile -Path ([string]$toolsConfig.launcher_script) -Label 'Tools consumer wrapper')
  [void](Read-NonEmptyFile -Path ([string]$toolsConfig.config_path) -Label 'Tools consumer config')
  if (
    (Get-FileHash -LiteralPath ([string]$toolsConfig.config_path) -Algorithm SHA256).Hash -cne
    (Get-FileHash -LiteralPath $bundleToolsConfig -Algorithm SHA256).Hash
  ) {
    throw 'Tools consumer config differs from the committed deployed bundle'
  }
} else {
  foreach ($machineFile in @(
      [pscustomobject]@{
        Path = [string]$toolsConfig.snapshot_path
        BundlePath = $bundleToolsConfig
        Label = 'snapshot'
      },
      [pscustomobject]@{
        Path = [string]$toolsConfig.config_path
        BundlePath = $bundleToolsConfig
        Label = 'config'
      }
    )) {
    if (-not (Test-Path -LiteralPath $machineFile.Path -PathType Leaf)) {
      Write-Warning (
        "DryRun: deployed Tools $($machineFile.Label) is missing; " +
        "the source bundle is being rehearsed: $($machineFile.Path)"
      )
      continue
    }
    if (
      (Get-FileHash -LiteralPath $machineFile.Path -Algorithm SHA256).Hash -cne
      (Get-FileHash -LiteralPath $machineFile.BundlePath -Algorithm SHA256).Hash
    ) {
      Write-Warning (
        "DryRun: deployed Tools $($machineFile.Label) differs from " +
        'the source bundle; deployment will replace it'
      )
    }
  }
  if (-not (Test-Path -LiteralPath ([string]$toolsConfig.launcher_script) -PathType Leaf)) {
    Write-Warning (
      'DryRun: deployed Tools forwarding launcher is missing; ' +
      "deployment will create it: $($toolsConfig.launcher_script)"
    )
  }
}
$toolsConflictPath = Resolve-NormalizedPath -Path (
  [string]$toolsConfig.replacement_conflict_path
)
if (Test-Path -LiteralPath $toolsConflictPath -PathType Leaf) {
  throw (
    'Tools replacement is blocked by a persistent orphan-conflict marker; ' +
    "inspect and clear it only after process cleanup: $toolsConflictPath"
  )
}
$toolsProcessState = Get-ToolsProcessState `
  -ToolsConfig $toolsConfig `
  -Generation $bundleGeneration `
  -Processes $processes
$toolsLive = @($toolsProcessState.current)
$toolsStarting = @($toolsProcessState.starting)
$toolsStale = @($toolsProcessState.stale)
$toolsLegacy = @($toolsProcessState.legacy)
if ($toolsLive.Count -gt 1) {
  throw "duplicate supervisor-managed Tools consumers PID(s): $(@($toolsLive | ForEach-Object { [string]$_.ProcessId }) -join ',')"
}
if ($toolsStarting.Count -gt 1) {
  throw "duplicate starting Tools consumers PID(s): $(@($toolsStarting | ForEach-Object { [string]$_.ProcessId }) -join ',')"
}
if ($toolsLegacy.Count -gt 0) {
  throw "legacy Tools consumers block safe restore PID(s): $(@($toolsLegacy | ForEach-Object { [string]$_.ProcessId }) -join ',')"
}
if (
  $toolsStale.Count -gt 1 -or
  ($toolsLive.Count -eq 1 -and (
      $toolsStarting.Count -gt 0 -or
      $toolsStale.Count -gt 0
    )) -or
  ($toolsStarting.Count -eq 1 -and $toolsStale.Count -gt 0)
) {
  throw (
    'conflicting Tools wrappers current/starting/stale PID(s): ' +
    "$(@($toolsLive | ForEach-Object { [string]$_.ProcessId }) -join ',') / " +
    "$(@($toolsStarting | ForEach-Object { [string]$_.ProcessId }) -join ',') / " +
    "$(@($toolsStale | ForEach-Object { [string]$_.ProcessId }) -join ',')"
  )
}
$toolsValidationLauncher = if ($bundleMode -ceq 'deployed') {
  [string]$toolsConfig.launcher_script
} else {
  $bundleToolsLauncher
}
$toolsValidationConfig = if ($bundleMode -ceq 'deployed') {
  [string]$toolsConfig.config_path
} else {
  $bundleToolsConfig
}
$toolsValidationOutput = @(
  & $toolsValidationLauncher `
    -ConfigPath $toolsValidationConfig `
    -Generation $bundleGeneration `
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
  throw 'Tools consumer returned no exact validation record'
}
$toolsValidation = $toolsValidation[0]
$toolsValidationPinValid = if (
  [string]$toolsConfig.resume_policy -ceq 'current_worktree'
) {
  [string]$toolsValidation.branch -cmatch '^\S+$' -and
  [string]$toolsValidation.head -cmatch '^[0-9a-f]{40}$' -and
  [string]$toolsValidation.baseline_branch -ceq [string]$toolsConfig.branch -and
  [string]$toolsValidation.baseline_head -ceq [string]$toolsConfig.head
} else {
  [string]$toolsValidation.branch -ceq [string]$toolsConfig.branch -and
  [string]$toolsValidation.head -ceq [string]$toolsConfig.head
}
if (
  -not [bool]$toolsValidation.validated -or
  [string]$toolsValidation.generation -cne $bundleGeneration -or
  -not ([string]$toolsValidation.readiness_path).Equals(
    [string]$toolsConfig.readiness_path,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  -not ([string]$toolsValidation.worktree).Equals(
    [string]$toolsConfig.worktree,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  -not $toolsValidationPinValid -or
  [string]$toolsValidation.resume_policy -cne [string]$toolsConfig.resume_policy -or
  [string]$toolsValidation.model -cne [string]$toolsConfig.model -or
  [string]$toolsValidation.reasoning_effort -cne [string]$toolsConfig.reasoning_effort -or
  [string]$toolsValidation.target_state_id -cne [string]$targetState.id -or
  -not ([string]$toolsValidation.git_top).Equals(
    [string]$toolsConfig.worktree,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  -not ([string]$toolsValidation.primary_repo_root).Equals(
    [string]$manifest.primary_repo_root,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  -not ([string]$toolsValidation.common_git_dir).Equals(
    [string]$manifest.repo_common_git_dir,
    [System.StringComparison]::OrdinalIgnoreCase
  ) -or
  [bool]$toolsValidation.require_dedicated_worktree -ne
    [bool]$toolsConfig.require_dedicated_worktree
) {
  throw 'Tools consumer validation does not match fleet pins'
}
Write-Host (
  "  Tools consumer current/starting/stale count: {0}/{1}/{2}; supervisor and deliberate driver HOLD are exact" -f
    $toolsLive.Count,
    $toolsStarting.Count,
    $toolsStale.Count
)
if ($toolsLive.Count -eq 1) {
  Write-ToolsReadinessWarning -ToolsConfig $toolsConfig
}

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

$supervisorPlan = Get-WdSupervisorInvocationPlan `
  -BundleMode $bundleMode `
  -SourceScript (Join-Path $PSScriptRoot 'wd_supervisor.ps1') `
  -SourceConfig $bundleToolsConfig `
  -DeployedScript ([string]$toolsConfig.supervisor_script)
$supervisorPreflightScript = [string]$supervisorPlan.preflight_script
$supervisorPreflightArguments = @($supervisorPlan.preflight_arguments)
Write-Host '  Supervisor byte-inert watcher/Tools preflight:'
$supervisorPreflightOutput = @(
  & $supervisorPreflightScript @supervisorPreflightArguments
)
if (@($supervisorPreflightOutput | Where-Object {
      [string]$_ -cmatch '(^|\s)CONFLICT(\s|$)'
    }).Count -gt 0) {
  throw 'supervisor report-only preflight returned a conflict'
}
if ($supervisorPreflightOutput) {
  $supervisorPreflightOutput | Out-Host
}

Write-Host '  Freezing canonical prefix and spool inventory:'
$bridgeSafetyBaseline = New-WdBridgeSafetyBaseline `
  -RuntimeRoot ([string]$manifest.runtime_root) `
  -SnapshotRuntimeRoot ([string]$snapshot.runtime_root) `
  -RecoveryStateRoot ([string]$snapshot.recovery_state_root) `
  -ToolsConflictPath ([string]$toolsConfig.replacement_conflict_path) `
  -WatcherConflictRoot ([string]$snapshot.watchers.replacement_conflict_root)
Write-Host (
  '    canonical prefix={0} bytes; spool={1} immutable file(s)' -f
    [int64]$bridgeSafetyBaseline.canonical.prefix_length,
    @($bridgeSafetyBaseline.spool).Count
)

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
  Write-Host (
    "  {0}: {1}/{2}; {3}" -f
      $state.lane.agent,
      $state.lane.model,
      $state.lane.effort,
      $(if (@($state.live).Count -eq 1) { 'idempotent skip (already live)' } else { 'launch in WT' })
  )
}
Write-Host (
  "  codex-tools-1: {0}" -f $(
    if ($toolsLive.Count -eq 1) {
      'supervisor-managed current generation is live'
    } elseif ($toolsStarting.Count -eq 1) {
      'supervisor-managed current generation is starting'
    } elseif ($toolsStale.Count -eq 1) {
      'ask WD-Supervisor to replace stale generation'
    } else {
      'ask WD-Supervisor to restore'
    }
  )
)
Write-Host (
  "  WD-Supervisor: {0}" -f $(
    if ([bool]$supervisorTaskActivation.enable_after_restore) {
      'enable exact task only after verified fleet restore'
    } else {
      'leave exact enabled task enabled'
    }
  )
)

if ($DryRun) {
  Assert-WdBridgeSafetyBaseline -Baseline $bridgeSafetyBaseline
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
  $mutexAcquired = Enter-WdFleetRebootMutex -Mutex $mutex
  if (-not $mutexAcquired) {
    throw 'another WaggleDance fleet reboot-control run is already active'
  }
  Assert-WdBridgeSafetyBaseline -Baseline $bridgeSafetyBaseline
  if (Test-Path -LiteralPath $handshakeDirectory) {
    throw "reboot handshake generation appeared after preflight: $handshakeDirectory"
  }
  $lockedProcesses = Get-AllProcessSnapshots
  foreach ($state in $laneStates) {
    $lockedLaneProcesses = @(
      Get-LaneProcesses -Lane $state.lane -Processes $lockedProcesses
    )
    if (-not (Test-WdProcessIdentitySetExact `
        -Expected @($state.live) `
        -Actual $lockedLaneProcesses
      )) {
      throw "lane process state changed after preflight: $($state.lane.agent)"
    }
  }
  $lockedToolsState = Get-ToolsProcessState `
    -ToolsConfig $toolsConfig `
    -Generation $bundleGeneration `
    -Processes $lockedProcesses
  foreach ($category in @('current', 'starting', 'stale', 'legacy')) {
    if (-not (Test-WdProcessIdentitySetExact `
        -Expected @($toolsProcessState.$category) `
        -Actual @($lockedToolsState.$category)
      )) {
      throw "Tools process state changed after preflight: $category"
    }
  }
  $lockedSupervisorTask = Get-WdSingleScheduledTask `
    -TaskName ([string]$toolsConfig.task_name)
  if (
    -not (Test-WdSupervisorTaskEnvelopeExact `
      -Task $lockedSupervisorTask `
      -ExpectedExecutable $expectedSupervisorExecutable `
      -ExpectedArguments $expectedSupervisorArguments `
      -ExpectedWorkingDirectory 'C:\Python' `
      -ExpectedPrincipalSid $expectedSupervisorPrincipalSid `
      -ExpectedStartBoundary $expectedSupervisorStartBoundary) -or
    [bool]$lockedSupervisorTask.Settings.Enabled -ne
      [bool]$supervisorTask.Settings.Enabled -or
    [string]$lockedSupervisorTask.State -cne [string]$supervisorTask.State
  ) {
    throw 'WD-Supervisor task state changed after preflight'
  }
  $lockedStandingTask = Get-WdSingleScheduledTask `
    -TaskName ([string]$containment.standing_task)
  if (
    [bool]$lockedStandingTask.Settings.Enabled -ne
      [bool]$containment.required_enabled -or
    [string]$lockedStandingTask.State -cne [string]$containment.required_state
  ) {
    throw 'merge-driver standing HOLD changed after preflight'
  }
  $lockedApplyDrivers = @(
    $lockedProcesses | Where-Object {
      ([string]$_.CommandLine).IndexOf(
        'Invoke-BridgeMergeDriver.ps1',
        [StringComparison]::OrdinalIgnoreCase
      ) -ge 0 -and
      (Test-ContainsApplySwitch ([string]$_.CommandLine))
    }
  )
  if ($lockedApplyDrivers.Count -gt 0) {
    throw 'merge-driver Apply process appeared after preflight'
  }
  $lockedLegacyTask = Get-OptionalScheduledTask `
    -TaskName ([string]$containment.legacy_task)
  if ($lockedLegacyTask -and [bool]$lockedLegacyTask.Settings.Enabled) {
    foreach ($action in @($lockedLegacyTask.Actions)) {
      if (-not (Test-DirectLegacyDriverAction `
          -Execute ([string]$action.Execute) `
          -Arguments ([string]$action.Arguments) `
          -ExpectedScript ([string]$containment.legacy_script)
        )) {
        throw 'legacy merge-driver task changed after preflight'
      }
    }
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
  $wtPath = Resolve-ApplicationPath -Name 'wt.exe'
  if (
    -not $codexAfterPath.Equals(
      $codexPath,
      [StringComparison]::OrdinalIgnoreCase
    ) -or
    -not $claudeAfterPath.Equals(
      $claudePath,
      [StringComparison]::OrdinalIgnoreCase
    )
  ) {
    throw 'CLI update changed a trusted native executable path'
  }
  $codexAfterHash = (
    Get-FileHash -LiteralPath $codexAfterPath -Algorithm SHA256
  ).Hash
  $claudeAfterHash = (
    Get-FileHash -LiteralPath $claudeAfterPath -Algorithm SHA256
  ).Hash
  if (
    $SkipCliUpdate -and
    ($codexAfterHash -cne $codexHash -or $claudeAfterHash -cne $claudeHash)
  ) {
    throw 'CLI executable bytes changed during a skipped update phase'
  }
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
      before_sha256 = $codexHash
      after_sha256 = $codexAfterHash
      update_command = 'codex update'
    }
    claude_code = [ordered]@{
      before = $claudeVersion
      after = $claudeAfterVersion
      executable = $claudeAfterPath
      before_sha256 = $claudeHash
      after_sha256 = $claudeAfterHash
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

  Write-Host ''
  Write-Host 'Reconciling five bridge watchers and the Tools consumer first...' -ForegroundColor Cyan
  $supervisorApplyArguments = @($supervisorPlan.apply_arguments)
  $supervisorApplyOutput = & ([string]$supervisorPlan.apply_script) `
    @supervisorApplyArguments
  if ($supervisorApplyOutput) {
    $supervisorApplyOutput | Out-Host
  }
  Start-Sleep -Milliseconds 500
  $supervisorVerifyArguments = @($supervisorPlan.verify_arguments)
  $supervisorVerifyOutput = @(
    & ([string]$supervisorPlan.verify_script) @supervisorVerifyArguments
  )
  if (@($supervisorVerifyOutput | Where-Object {
        [string]$_ -cmatch '(^|\s)CONFLICT(\s|$)'
      }).Count -gt 0) {
    throw 'supervisor post-Apply report returned a conflict'
  }
  if ($supervisorVerifyOutput) {
    $supervisorVerifyOutput | Out-Host
  }

  Write-Host 'Waiting for the supervisor-managed Tools consumer...' -ForegroundColor Cyan
  $toolsNowState = Wait-WdToolsCurrentProcess `
    -ToolsConfig $toolsConfig `
    -Generation $bundleGeneration `
    -TimeoutSeconds ([int]$toolsConfig.wait_seconds)
  Write-ToolsReadinessWarning -ToolsConfig $toolsConfig

  Write-Host 'Resolving the current Grok model...' -ForegroundColor Cyan
  $grokResult = & $resolver -OutputDirectory ([string]$manifest.grok_output_directory)
  [void](Read-NonEmptyFile -Path ([string]$manifest.grok_markdown) -Label 'generated Grok model guide')
  if ($grokResult) {
    $grokObjects = @($grokResult | Where-Object { $_ -is [psobject] })
    if ($grokObjects.Count -gt 0 -and $grokObjects[-1].PSObject.Properties['Model']) {
      Write-Host ("  Grok model: {0}" -f [string]$grokObjects[-1].Model)
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
      $expectedSupervisorExecutable,
      '-NoProfile',
      '-ExecutionPolicy', 'Bypass',
      '-File', $agentLauncher,
      '-ManifestPath', $ManifestPath,
      '-Agent', [string]$state.lane.agent,
      '-RunId', $RunId,
      '-HandshakeDirectory', $handshakeDirectory,
      '-ExpectedManifestHash', $bundleManifestAnchor
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
      $launchedState = @(
        $laneStates |
          Where-Object { [string]$_.lane.agent -ceq [string]$launchedAgent }
      )[0]
      $lane = $launchedState.lane
      $handshakePath = Join-Path $handshakeDirectory ("{0}.json" -f $launchedAgent)
      $handshake = Get-Content -LiteralPath $handshakePath -Raw |
        ConvertFrom-Json -ErrorAction Stop
      $expectedCliExecutable = Resolve-ApplicationPath -Name ([string]$lane.cli)
      $expectedCliHash = (
        Get-FileHash -LiteralPath $expectedCliExecutable -Algorithm SHA256
      ).Hash
      if (
        [int]$handshake.schema_version -ne 1 -or
        [string]$handshake.status -cne 'bridge_bootstrapped' -or
        [string]$handshake.agent -cne [string]$lane.agent -or
        [string]$handshake.agent_uuid -cne [string]$lane.agent_uuid -or
        [string]$handshake.role -cne [string]$lane.role -or
        [string]$handshake.run_id -cne $RunId -or
        [string]$handshake.session_id -cne $RunId -or
        [string]$handshake.branch -cne [string]$launchedState.actual_branch -or
        [string]$handshake.head -cne [string]$launchedState.actual_head -or
        [string]$handshake.baseline_branch -cne [string]$lane.branch -or
        [string]$handshake.baseline_head -cne [string]$lane.head -or
        [string]$handshake.resume_policy -cne [string]$lane.resume_policy -or
        [string]$handshake.model_selection -cne 'explicit' -or
        [string]$handshake.model -cne [string]$lane.model -or
        [string]$handshake.effort -cne [string]$lane.effort -or
        -not ([string]$handshake.cli_executable).Equals(
          $expectedCliExecutable,
          [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        [string]$handshake.cli_executable_sha256 -cne $expectedCliHash -or
        -not (Test-WdJsonBooleanTrue `
          -Object $handshake `
          -Name 'target_state_manifested') -or
        -not (Test-WdJsonBooleanTrue -Object $handshake -Name 'append_canary') -or
        [string]$handshake.append_canary_task_id -cne
          "wd-append-canary-$RunId" -or
        -not (Test-WdJsonIntegerRange `
          -Object $handshake `
          -Name 'append_canary_latency_ms' `
          -Minimum 0 `
          -Maximum 5000) -or
        [string]$handshake.bundle_generation -cne $bundleGeneration -or
        [string]$handshake.target_state_id -cne [string]$targetState.id -or
        [string]$handshake.target_state_sha256 -cne [string]$targetState.sha256 -or
        -not ([string]$handshake.worktree).Equals(
          [string]$lane.worktree,
          [System.StringComparison]::OrdinalIgnoreCase
        )
      ) {
        throw "bridge bootstrap handshake identity mismatch for $launchedAgent"
      }
      try {
        $createdUtc = ConvertTo-UtcDateTimeOffset `
          -Value $handshake.created_at_utc `
          -Label "bridge bootstrap handshake creation for $launchedAgent"
      } catch {
        throw "bridge bootstrap handshake is stale or malformed for $launchedAgent"
      }
      if ($createdUtc -lt $launchStartedUtc.AddSeconds(-5)) {
        throw "bridge bootstrap handshake is stale or malformed for $launchedAgent"
      }
      $canaryCreatedUtc = ConvertTo-UtcDateTimeOffset `
        -Value $handshake.append_canary_event_utc `
        -Label "append canary creation for $launchedAgent"
      if (
        $canaryCreatedUtc -lt $launchStartedUtc.AddSeconds(-5) -or
        $canaryCreatedUtc -gt $createdUtc
      ) {
        throw "append canary is stale or malformed for $launchedAgent"
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

  $finalProcesses = Get-AllProcessSnapshots
  foreach ($state in $laneStates) {
    $finalLaneProcesses = @(
      Get-LaneProcesses -Lane $state.lane -Processes $finalProcesses
    )
    if (
      $finalLaneProcesses.Count -ne 1 -or
      -not (Test-LaneGenerationAttestation `
        -Lane $state.lane `
        -Process $finalLaneProcesses[0])
    ) {
      throw "final lane generation verification failed for $($state.lane.agent)"
    }
  }
  $finalToolsState = Get-ToolsProcessState `
    -ToolsConfig $toolsConfig `
    -Generation $bundleGeneration `
    -Processes $finalProcesses
  if (
    @($finalToolsState.current).Count -ne 1 -or
    @($finalToolsState.starting).Count -ne 0 -or
    @($finalToolsState.stale).Count -ne 0 -or
    @($finalToolsState.legacy).Count -ne 0
  ) {
    throw 'final Tools generation verification failed before task activation'
  }

  $finalSupervisorOutput = @(
    & ([string]$supervisorPlan.verify_script) @supervisorVerifyArguments
  )
  if (@($finalSupervisorOutput | Where-Object {
        [string]$_ -cmatch '(^|\s)CONFLICT(\s|$)'
      }).Count -gt 0) {
    throw 'final supervisor report returned a conflict before task activation'
  }
  if ($finalSupervisorOutput) {
    $finalSupervisorOutput | Out-Host
  }
  Assert-WdBridgeSafetyBaseline -Baseline $bridgeSafetyBaseline

  $supervisorActivationResult = Enable-WdSupervisorTaskAfterRestore `
    -TaskName ([string]$toolsConfig.task_name) `
    -ExpectedExecutable $expectedSupervisorExecutable `
    -ExpectedArguments $expectedSupervisorArguments `
    -ExpectedWorkingDirectory 'C:\Python' `
    -ExpectedPrincipalSid $expectedSupervisorPrincipalSid `
    -ExpectedStartBoundary $expectedSupervisorStartBoundary
  try {
    $postActivationSupervisorOutput = @(
      & ([string]$supervisorPlan.verify_script) @supervisorVerifyArguments
    )
    if (@($postActivationSupervisorOutput | Where-Object {
          [string]$_ -cmatch '(^|\s)CONFLICT(\s|$)'
        }).Count -gt 0) {
      throw 'supervisor report returned a conflict after scheduled-path proof'
    }
    Assert-WdBridgeSafetyBaseline -Baseline $bridgeSafetyBaseline
  } catch {
    $postActivationFailure = $_
    try {
      Set-WdSupervisorTaskHeld `
        -TaskName ([string]$toolsConfig.task_name) `
        -ExpectedExecutable $expectedSupervisorExecutable `
        -ExpectedArguments $expectedSupervisorArguments `
        -ExpectedWorkingDirectory 'C:\Python' `
        -ExpectedPrincipalSid $expectedSupervisorPrincipalSid `
        -ExpectedStartBoundary $expectedSupervisorStartBoundary
    } catch {
      throw (
        "{0}; post-activation containment also failed: {1}" -f
          $postActivationFailure.Exception.Message,
          $_.Exception.Message
      )
    }
    throw $postActivationFailure
  }
  Write-Host (
    '  WD-Supervisor activation: changed={0}; state={1}; last_result={2}' -f
      [bool]$supervisorActivationResult.changed,
      [string]$supervisorActivationResult.state,
      [int64]$supervisorActivationResult.last_task_result
  )

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

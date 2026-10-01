#requires -Version 5.1
<# Read-only identity of the CURRENT canonical event log (F26 S-C; source only, no production caller).

   Output, closed: {log_generation, file_identity, log_bytes, prefix_sha256, observed_utc, complete}. It is
   snapshot METADATA only: no row, no content and no authority. complete=true means only that every field was
   measured on one stable, fully consistent prefix; it never means that any row was accepted. Every field is
   null whenever complete is false: incomplete is UNKNOWN, never empty and never a partial identity.

   Root: AGENT_BRIDGE_RUNTIME_ROOT, which must be an explicit absolute path (same rule as
   Get-BridgeRequestInventory); the log is <root>/shared/events.jsonl. No installed-pin or other fallback.

   Fields and how each is measured (reusing the canonical reader APIs, unchanged):
   * log_generation: the token of the canonical sidecar <root>/shared/events.generation.json read with
     Read-BridgeGenerationToken (Get-BridgeEventGenerationPath). The reader treats an ABSENT sidecar as an
     unconfigured generation; that proves no generation, so absence is UNKNOWN here (complete=false), never a
     label invented from a code pin. An invalid or unreadable sidecar is unknown too.
   * file_identity: Get-BridgeLogFileIdentity on the open handle (windows-v1:<volume>:<file index>).
   * log_bytes: the handle length frozen once; the prefix must end in LF (an unfinished tail row is unknown).
   * prefix_sha256: lowercase sha256 of exactly log_bytes bytes on the same handle
     (Get-BridgeReplyStreamPrefixHash, which refuses a short read).
   * observed_utc: UTC after every post-read check passed.
   After hashing, the same handle and a FRESH open must both show the same identity and exactly log_bytes;
   the fresh handle then hashes the same log_bytes again and must give the same sha256, and its identity and
   length are checked once more; finally the generation sidecar path and token must be unchanged. An append,
   truncation, same-length rewrite, rotation or generation change between the first hash and these checks is
   unknown (measure again). LIMITS: a change after the last check returns is seen only by a later measurement
   (inherent: this is metadata of what was read, not a lock), and a rewrite that is undone before the second
   hash (A-B-A) is not seen; the two hashes prove the same bytes were read twice, not a snapshot and not any
   row authority (bounded cost: two reads of at most -MaxBytes). A log or shared directory that is a
   reparse point, a missing or unreadable log, and a log larger than -MaxBytes are unknown. The log is opened
   read-only with ReadWrite|Delete sharing; nothing is written, cached or emitted, and no exception text (which
   could quote content or paths) is returned. #>
[CmdletBinding()]
param(
    [ValidateRange(1, 268435456)] [int64] $MaxBytes = 268435456,
    [switch] $Json
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'BridgeIncrementalReader.ps1')
. (Join-Path $PSScriptRoot 'BridgeReplyIndex.ps1')

function New-BridgeCurrentLogIdentity {
    param([AllowNull()] $Generation, [AllowNull()] $FileIdentity, [AllowNull()] $LogBytes,
          [AllowNull()] $PrefixSha256, [AllowNull()] $ObservedUtc, [bool] $Complete)
    [pscustomobject][ordered]@{
        log_generation = $Generation
        file_identity = $FileIdentity
        log_bytes = $LogBytes
        prefix_sha256 = $PrefixSha256
        observed_utc = $ObservedUtc
        complete = $Complete
    }
}

function Test-BridgeReparsePoint {
    param([string] $Path)
    $attributes = [IO.File]::GetAttributes($Path)
    return (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Measure-BridgeCurrentLogIdentity {
    param([int64] $Limit)
    $root = [string]$env:AGENT_BRIDGE_RUNTIME_ROOT
    $pathRoot = if ([string]::IsNullOrWhiteSpace($root)) { '' } else { [IO.Path]::GetPathRoot($root) }
    $fullyQualified = if ([IO.Path]::DirectorySeparatorChar -eq '\') {
        $pathRoot -match '^[A-Za-z]:[\\/]$' -or $pathRoot -match '^\\\\[^\\]+\\[^\\]+[\\/]?$'
    } else { $pathRoot -ceq '/' }
    if (-not $fullyQualified) { return $null }
    $resolved = Resolve-BridgeFileSystemPath -Path (Join-Path (Join-Path $root 'shared') 'events.jsonl')
    if (-not $resolved.valid) { return $null }
    $path = $resolved.path
    if ((Test-BridgeReparsePoint -Path (Split-Path -Parent $path)) -or (Test-BridgeReparsePoint -Path $path)) {
        return $null
    }
    $generationPath = Get-BridgeEventGenerationPath -Path $path
    if (-not $generationPath) { return $null }                 # no sidecar: generation not proven
    if (Test-BridgeReparsePoint -Path $generationPath) { return $null }
    $before = Read-BridgeGenerationToken -Path $generationPath
    if ($before.status -cne 'OK') { return $null }

    $stream = Open-BridgeLogReadStream -Path $path
    try {
        $identity = Get-BridgeLogFileIdentity -Stream $stream
        $length = [int64]$stream.Length
        if ($length -gt $Limit) { return $null }
        if ($length -gt 0) {
            [void]$stream.Seek($length - 1, [IO.SeekOrigin]::Begin)
            $last = $stream.ReadByte()
            if ($last -ne 10) { return $null }                 # unfinished tail row
        }
        $prefix = (Get-BridgeReplyStreamPrefixHash -Stream $stream -Length $length).ToLowerInvariant()
        if ((Get-BridgeLogFileIdentity -Stream $stream) -cne $identity -or [int64]$stream.Length -ne $length) {
            return $null
        }
        $fresh = Open-BridgeLogReadStream -Path $path
        try {
            if ((Get-BridgeLogFileIdentity -Stream $fresh) -cne $identity -or [int64]$fresh.Length -ne $length) {
                return $null                                   # rotated, appended or truncated
            }
            # A same-length in-place rewrite keeps identity and length: hash the same frozen prefix again on
            # the fresh handle and require the same bytes (RCO1/fable-5 SC-F1), then recheck the fresh handle.
            $again = (Get-BridgeReplyStreamPrefixHash -Stream $fresh -Length $length).ToLowerInvariant()
            if ($again -cne $prefix) { return $null }
            if ((Get-BridgeLogFileIdentity -Stream $fresh) -cne $identity -or [int64]$fresh.Length -ne $length) {
                return $null
            }
        } finally { $fresh.Dispose() }
    } finally { $stream.Dispose() }
    if ((Get-BridgeEventGenerationPath -Path $path) -cne $generationPath) { return $null }
    $after = Read-BridgeGenerationToken -Path $generationPath
    if ($after.status -cne 'OK' -or $after.generation -cne $before.generation) { return $null }
    $observed = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ',
        [Globalization.CultureInfo]::InvariantCulture)
    return New-BridgeCurrentLogIdentity -Generation ([string]$before.generation) -FileIdentity ([string]$identity) `
        -LogBytes $length -PrefixSha256 $prefix -ObservedUtc $observed -Complete $true
}

$result = $null
try { $result = Measure-BridgeCurrentLogIdentity -Limit $MaxBytes } catch { $result = $null }
if ($null -eq $result) {
    $result = New-BridgeCurrentLogIdentity -Generation $null -FileIdentity $null -LogBytes $null `
        -PrefixSha256 $null -ObservedUtc $null -Complete $false
}
if ($Json) { $result | ConvertTo-Json -Compress } else { $result }

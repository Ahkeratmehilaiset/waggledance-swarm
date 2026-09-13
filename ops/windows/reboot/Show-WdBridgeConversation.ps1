#requires -Version 5.1
<#
.SYNOPSIS
    Read-only, color-coded bridge discussion in the current console host.
.DESCRIPTION
    Replays at most InitialTail physical rows, then follows bounded byte deltas.
    Heartbeat/liveness, wake and message ACK records are hidden. Labels remain
    textual. The view is advisory: no ACK, delivery, task or approval authority.
    Reader cursors live only in memory. Rotation/truncation announces a possible
    history gap before a fresh bounded replay; invalid records retain the cursor.
    InitialTail 0 skips existing rows. A local named mutex prevents duplicate
    viewers for the same normalized runtime path in this Windows session.
    This script never starts processes, consumes queues, or writes runtime files.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RuntimeRoot,
    [string] $ReaderPath = '',
    [ValidateRange(100, 60000)] [int] $PollIntervalMs = 1000,
    [ValidateRange(0, 100000)] [int] $MaxIterations = 0,
    [ValidateRange(0, 1000)] [int] $InitialTail = 40
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-WdConversationField {
    param($Event, [string] $Name)
    if ($null -ne $Event -and $null -ne $Event.PSObject.Properties[$Name]) {
        return [string]$Event.PSObject.Properties[$Name].Value
    }
    return ''
}

function ConvertTo-WdConversationText {
    param([AllowEmptyString()] [string] $Text, [int] $Limit = 1200)
    $truncated = $Text.Length -gt $Limit
    if ($truncated) { $Text = $Text.Substring(0, $Limit) }
    # Remove terminal commands, then replace controls and Unicode format/line
    # separators. Even an incomplete escape at the display bound becomes inert.
    $Text = [regex]::Replace($Text, '\x1B\[[0-?]*[ -/]*[@-~]', '')
    $Text = [regex]::Replace($Text, '\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)', '')
    $Text = [regex]::Replace($Text, '[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]', ' ')
    if ($truncated) { $Text += ' [truncated]' }
    return $Text
}

function Write-WdConversationEvent {
    param($Event)
    $kind = Get-WdConversationField $Event 'type'
    $status = Get-WdConversationField $Event 'status'
    if ($kind -in @('heartbeat', 'liveness', 'wake', 'wake_request') -or
        $kind -like 'wake_*' -or $status -like 'wake_*' -or
        ($kind -eq 'message' -and $status -in @('ack', 'received', 'seen', 'acknowledged'))) {
        return
    }
    $agent = Get-WdConversationField $Event 'agent'
    $severity = Get-WdConversationField $Event 'severity'
    $color = switch -Regex ($agent) {
        '^codex-lead-' { 'Cyan'; break }
        '^codex-tools-' { 'Green'; break }
        '^claude-rco-1$' { 'Yellow'; break }
        '^claude-rco-2$' { 'Magenta'; break }
        '^fable-' { 'Blue'; break }
        default { 'Gray' }
    }
    if ($severity -in @('error', 'critical', 'fatal') -or $status -in @('error', 'failed', 'failure')) {
        $color = 'Red'
    } elseif ($severity -in @('warning', 'warn') -or $status -in @('warning', 'warn')) {
        $color = 'Yellow'
    }
    $timestamp = ConvertTo-WdConversationText (Get-WdConversationField $Event 'ts_utc') 64
    $agent = ConvertTo-WdConversationText $agent 80
    $kind = ConvertTo-WdConversationText $kind 80
    $status = ConvertTo-WdConversationText $status 80
    $severity = ConvertTo-WdConversationText $severity 40
    $message = ConvertTo-WdConversationText (Get-WdConversationField $Event 'message')
    if (-not $agent) { $agent = 'unknown-agent' }
    if (-not $kind) { $kind = 'unknown-type' }
    if (-not $status) { $status = 'unknown-status' }
    $severityLabel = if ($severity) { " [severity:$severity]" } else { '' }
    Write-Host "$timestamp [$agent] [$kind/$status]$severityLabel $message" -ForegroundColor $color
}

function Resolve-WdConversationReader {
    param([string] $Requested)
    if ($Requested) {
        $candidate = [IO.Path]::GetFullPath($Requested)
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw 'conversation reader is unavailable'
        }
        return $candidate
    }
    $sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
    foreach ($candidate in @(
        (Join-Path $PSScriptRoot 'tools-bootstrap/.agent-bridge/bin/BridgeIncrementalReader.ps1'),
        (Join-Path $sourceRoot '.agent-bridge/bin/BridgeIncrementalReader.ps1')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'conversation reader is unavailable; supply -ReaderPath'
}

function Invoke-WdConversationLoop {
    param([string] $EventsPath, [int] $Tail, [int] $PollMs, [int] $Iterations)
    $cursor = $null
    $skipInitial = $Tail -eq 0
    $replayRows = [Math]::Max(1, $Tail)
    $lastDiagnostic = ''
    $iteration = 0
    Write-Host "[viewer] Bounded context: last $Tail physical rows; then new discussion. Heartbeat/wake/ACK hidden. No task authority." -ForegroundColor Gray
    while ($Iterations -eq 0 -or $iteration -lt $Iterations) {
        $iteration++
        try {
            $result = if ($null -eq $cursor) {
                Read-BridgeEventTail -Path $EventsPath -MaxLines $replayRows -MaxBytes 4194304
            } else {
                Read-BridgeEventDelta -Path $EventsPath -Cursor $cursor -MaxRows 200 -MaxBytes 4194304
            }
            $diagnostic = ''
            $diagnosticColor = 'Yellow'
            if ($result.status -in @('OK', 'IDLE')) {
                if (-not $skipInitial) {
                    foreach ($row in @($result.rows)) { Write-WdConversationEvent $row }
                }
                if ($null -ne $result.candidate_cursor) {
                    $cursor = $result.candidate_cursor
                    $skipInitial = $false
                }
                if ($result.reason -eq 'log_missing') {
                    $diagnostic = '[viewer] log_missing; waiting for bridge log. No runtime files created.'
                    $skipInitial = $false
                    $replayRows = [Math]::Max(40, $Tail)
                } elseif ($result.reason -in @('partial_record', 'no_rows')) {
                    $diagnostic = '[viewer] No complete new row yet; partial tail retained for next read.'
                } elseif ($null -ne $result.PSObject.Properties['snapshot_length'] -and
                          $null -ne $cursor -and $cursor.offset -lt $result.snapshot_length) {
                    $diagnostic = '[viewer] Remaining bytes or partial tail retained for next bounded read.'
                }
            } elseif ($result.reason -in @('file_identity_changed', 'log_truncated',
                                           'generation_changed', 'generation_configuration_changed')) {
                $diagnostic = "[viewer] $($result.reason); history gap possible. Restarting with bounded context; earlier history may require separate review."
                $cursor = $null
                $skipInitial = $false
                $replayRows = [Math]::Max(40, $Tail)
            } else {
                $diagnostic = "[viewer] $($result.status)/$($result.reason); cursor retained. No events inferred or skipped."
                if ($result.status -eq 'BLOCKED') { $diagnosticColor = 'Red' }
            }
            if ($diagnostic -and $diagnostic -cne $lastDiagnostic) {
                Write-Host $diagnostic -ForegroundColor $diagnosticColor
            }
            $lastDiagnostic = $diagnostic
        } catch {
            if ($lastDiagnostic -cne 'reader_error') {
                Write-Host '[viewer] reader_error; cursor retained. Inspect reader/input; no work inferred.' -ForegroundColor Red
            }
            $lastDiagnostic = 'reader_error'
        }
        if ($Iterations -eq 0 -or $iteration -lt $Iterations) {
            Start-Sleep -Milliseconds $PollMs
        }
    }
}

function Start-WdBridgeConversation {
    $normalizedRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd([char[]]@('\', '/'))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = [BitConverter]::ToString($hasher.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($normalizedRoot.ToLowerInvariant()))).Replace('-', '')
    } finally { $hasher.Dispose() }
    $mutex = New-Object Threading.Mutex($false, "Local\WD-BridgeConversation-$digest")
    $acquired = $false
    try {
        try { $acquired = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) {
            Write-Host '[viewer] A bridge conversation viewer is already open for this runtime.' -ForegroundColor Gray
            return
        }
        try { $Host.UI.RawUI.WindowTitle = 'WD Bridge Conversation' }
        catch { Write-Host '[viewer] Window title unavailable in this host.' -ForegroundColor Gray }
        $resolvedReader = Resolve-WdConversationReader $ReaderPath
        . $resolvedReader
        Invoke-WdConversationLoop -EventsPath (Join-Path $normalizedRoot 'shared/events.jsonl') `
            -Tail $InitialTail -PollMs $PollIntervalMs -Iterations $MaxIterations
    } finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

if ($MyInvocation.InvocationName -ne '.') { Start-WdBridgeConversation }

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
    [ValidateRange(0, 1000)] [int] $InitialTail = 40,
    [ValidateLength(0, 128)] [string] $AgentFilter = '',
    [ValidateLength(0, 128)] [string] $TypeFilter = '',
    [switch] $PlainText
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

function Get-WdConversationSuppression {
    param($Event, [string] $Agent = '', [string] $KindFilter = '')
    $kind = Get-WdConversationField $Event 'type'
    $status = Get-WdConversationField $Event 'status'
    if ($kind -in @('heartbeat', 'liveness')) { return 'hb' }
    if ($kind -in @('wake', 'wake_request') -or $kind -like 'wake_*' -or $status -like 'wake_*') { return 'wake' }
    if ($kind -eq 'message' -and $status -in @('ack', 'received', 'seen', 'acknowledged')) { return 'ack' }
    if (($Agent -and (Get-WdConversationField $Event 'agent') -cne $Agent) -or
        ($KindFilter -and $kind -cne $KindFilter)) { return 'filter' }
    return ''
}

function Format-WdConversationEvent {
    param($Event)
    $kind = Get-WdConversationField $Event 'type'
    $status = Get-WdConversationField $Event 'status'
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
    $role = switch -Regex ($agent) {
        '^codex-lead-' { 'LEAD'; break }
        '^codex-tools-' { 'TOOLS'; break }
        '^claude-rco-1$' { 'RCO1'; break }
        '^claude-rco-2$' { 'RCO2'; break }
        '^fable-' { 'FABLE'; break }
        default { 'OTHER' }
    }
    $alert = ''
    if ($severity -in @('error', 'critical', 'fatal') -or $status -in @('error', 'failed', 'failure')) {
        $color = 'Red'
        $alert = ' [ERROR]'
    } elseif ($severity -in @('warning', 'warn') -or $status -in @('warning', 'warn')) {
        $color = 'Yellow'
        $alert = ' [WARN]'
    }
    $timestamp = ConvertTo-WdConversationText (Get-WdConversationField $Event 'ts_utc') 64
    $screenTime = $timestamp -replace '^\d{4}-\d{2}-\d{2}T', ''
    # PowerShell versions differ: ConvertFrom-Json may materialize ISO dates.
    $timeProperty = $Event.PSObject.Properties['ts_utc']
    if ($null -ne $timeProperty -and
        ($timeProperty.Value -is [DateTime] -or $timeProperty.Value -is [DateTimeOffset])) {
        $screenTime = $timeProperty.Value.ToUniversalTime().ToString("HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
    }
    $agent = ConvertTo-WdConversationText $agent 80
    $kind = ConvertTo-WdConversationText $kind 80
    $status = ConvertTo-WdConversationText $status 80
    $severity = ConvertTo-WdConversationText $severity 40
    $message = ConvertTo-WdConversationText (Get-WdConversationField $Event 'message')
    if (-not $agent) { $agent = 'unknown-agent' }
    if (-not $kind) { $kind = 'unknown-type' }
    if (-not $status) { $status = 'unknown-status' }
    $severityLabel = if ($severity) { " [severity:$severity]" } else { '' }
    $recipient = ConvertTo-WdConversationText (Get-WdConversationField $Event 'to') 80
    $task = ConvertTo-WdConversationText (Get-WdConversationField $Event 'task_id') 160
    $routing = if ($recipient) { " [to:$recipient]" } else { '' }
    if ($task) { $routing += " [task:$task]" }
    [pscustomobject]@{
        Text = "$timestamp [$agent] [$kind/$status] [$role]$alert$severityLabel$routing $message"
        ScreenText = "$screenTime [$role] [$kind/$status]$alert $message"
        Color = $color
    }
}

function Write-WdConversationEvent {
    param($Event)
    if (Get-WdConversationSuppression $Event) { return }
    $line = Format-WdConversationEvent $Event
    Write-Host $line.Text -ForegroundColor $line.Color
}

function New-WdConversationView {
    $interactive = $false
    if (-not $PlainText) {
        try {
            $interactive = $Host.Name -eq 'ConsoleHost' -and
                -not [Console]::IsOutputRedirected -and -not [Console]::IsInputRedirected
        } catch { $interactive = $false }
    }
    [pscustomobject]@{
        Interactive = $interactive; Paused = $false; Quit = $false
        Agent = $AgentFilter; Kind = $TypeFilter
        Counts = @{ rows=[long]0; visible=[long]0; hb=[long]0; wake=[long]0; ack=[long]0; filter=[long]0; skipped=[long]0 }
        Lag = $null; Reader = 'unknown'; Reason = ''; Diagnostic = ''; DiagnosticColor = 'Gray'
        Entries = [Collections.Generic.List[object]]::new()
        Frame = @(); Geometry = ''; Controls = $interactive
    }
}

function Get-WdConversationSummary {
    param($View)
    $mode = if ($View.Paused) { 'PAUSED' } else { 'FOLLOW' }
    $lag = if ($View.Paused -or $null -eq $View.Lag) { 'unknown' } else { "$($View.Lag)B" }
    $c = $View.Counts
    "WD BRIDGE | $mode | READ-ONLY no authority | lag=$lag | rows=$($c.rows) visible=$($c.visible) hidden: hb=$($c.hb) wake=$($c.wake) ack=$($c.ack) filter=$($c.filter) skipped=$($c.skipped)"
}

function Add-WdConversationLine {
    param($View, [string] $Text, [string] $Color = 'Gray')
    if ($View.Interactive) {
        $View.Entries.Add([pscustomobject]@{ Text=$Text; Color=$Color })
        if ($View.Entries.Count -gt 200) { $View.Entries.RemoveAt(0) }
    } else { Write-Host $Text -ForegroundColor $Color }
}

function Get-WdConversationKey {
    if ([Console]::KeyAvailable) { return [string][Console]::ReadKey($true).Key }
    return ''
}

function Update-WdConversationControl {
    param($View, [string] $Key)
    switch ($Key) {
        'P' { $View.Paused = -not $View.Paused }
        'Spacebar' { $View.Paused = -not $View.Paused }
        'Q' { $View.Quit = $true }
        'A' {
            $agents = @('', 'codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
            $View.Agent = $agents[([array]::IndexOf($agents, $View.Agent) + 1) % $agents.Count]
            $View.Entries.Clear()
            Add-WdConversationLine $View '[viewer] Sender filter changed; applies to subsequent reads. No history rescan.' 'Yellow'
        }
        'T' {
            $kinds = @('', 'message', 'finding', 'decision', 'test', 'blocked', 'handoff', 'done')
            $View.Kind = $kinds[([array]::IndexOf($kinds, $View.Kind) + 1) % $kinds.Count]
            $View.Entries.Clear()
            Add-WdConversationLine $View '[viewer] Type filter changed; applies to subsequent reads. No history rescan.' 'Yellow'
        }
    }
}

function ConvertTo-WdConversationCellText {
    param([string] $Text, [int] $Width)
    if ($Width -le 0) { return '' }
    # Host cell widths, not UTF-16 string length, prevent wide glyph wrapping.
    $result = [Text.StringBuilder]::new()
    $cells = 0
    $clipped = $Host.UI.RawUI.LengthInBufferCells($Text) -gt $Width
    $capacity = if ($clipped) { $Width - 1 } else { $Width }
    $elements = [Globalization.StringInfo]::GetTextElementEnumerator($Text)
    while ($elements.MoveNext()) {
        $element = [string]$elements.Current
        $size = [Math]::Max(1, $Host.UI.RawUI.LengthInBufferCells($element))
        if ($cells + $size -gt $capacity) { break }
        [void]$result.Append($element)
        $cells += $size
    }
    $suffix = if ($clipped) { '>' } else { '' }
    return $result.ToString() + (' ' * [Math]::Max(0, $capacity - $cells)) + $suffix
}

function Write-WdConversationScreenLine {
    param([int] $Y, [string] $Text, [string] $Color, [int] $Width)
    $savedColor = [Console]::ForegroundColor
    try {
        [Console]::SetCursorPosition(0, $Y)
        [Console]::ForegroundColor = [ConsoleColor]$Color
        [Console]::Write((ConvertTo-WdConversationCellText $Text $Width))
    } finally { [Console]::ForegroundColor = $savedColor }
}

function Show-WdConversationFrame {
    param($View)
    if (-not $View.Interactive) { return }
    try {
        $width = [Math]::Min([Console]::WindowWidth, [Console]::BufferWidth) - 1
        $height = [Math]::Min([Console]::WindowHeight, 60) - 1
        $top = [Console]::WindowTop
        if ($width -lt 20 -or $height -lt 6) { throw 'terminal too small' }
        $geometry = "$width/$height/$top"
        $summary = Get-WdConversationSummary $View
        $split = $summary.IndexOf(' | rows=')
        $agent = if ($View.Agent) { ConvertTo-WdConversationText $View.Agent 128 } else { 'ALL' }
        $kind = if ($View.Kind) { ConvertTo-WdConversationText $View.Kind 128 } else { 'ALL' }
        $frame = [Collections.Generic.List[object]]::new()
        $frame.Add([pscustomobject]@{ Text=$summary.Substring(0, $split); Color='Cyan' })
        $frame.Add([pscustomobject]@{ Text=$summary.Substring($split + 3); Color='Gray' })
        $keys = if ($View.Controls) { 'P pause A sender T type Q quit' } else { 'keyboard unavailable' }
        $frame.Add([pscustomobject]@{ Text="agent=$agent type=$kind | $keys | counts=read observations, not work"; Color='Gray' })
        $reason = ConvertTo-WdConversationText "$($View.Reader)/$($View.Reason) $($View.Diagnostic)" 500
        $frame.Add([pscustomobject]@{ Text=$reason; Color=$View.DiagnosticColor })
        $take = $height - 4
        $start = [Math]::Max(0, $View.Entries.Count - $take)
        for ($i = $start; $i -lt $View.Entries.Count; $i++) { $frame.Add($View.Entries[$i]) }
        while ($frame.Count -lt $height) { $frame.Add([pscustomobject]@{ Text=''; Color='Gray' }) }
        for ($i = 0; $i -lt $frame.Count; $i++) {
            $line = $frame[$i]
            if ($geometry -cne $View.Geometry -or $i -ge $View.Frame.Count -or
                $line.Text -cne $View.Frame[$i].Text -or $line.Color -cne $View.Frame[$i].Color) {
                Write-WdConversationScreenLine -Y ($top + $i) -Text $line.Text -Color $line.Color -Width $width
            }
        }
        $View.Frame = @($frame.ToArray())
        $View.Geometry = $geometry
    } catch {
        # An unsupported/resized-away console is a visible, one-way fallback.
        $View.Interactive = $false
        $View.Controls = $false
        $View.Paused = $false
        Write-Host '[viewer] Interactive display unavailable; continuing plain stream without keyboard controls.' -ForegroundColor Yellow
        foreach ($line in $View.Entries) { Write-Host $line.Text -ForegroundColor $line.Color }
        $View.Entries.Clear()
    }
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
    $view = New-WdConversationView
    Add-WdConversationLine $view "[viewer] Bounded context: last $Tail physical rows; then new discussion. Heartbeat/liveness, wake and message receipts/ACK hidden. No task authority. Counts are sampled read observations, including replay; not unique events or productive work."
    while ($Iterations -eq 0 -or $iteration -lt $Iterations) {
        $iteration++
        if ($view.Controls) {
            try {
                for ($keyCount = 0; $keyCount -lt 16; $keyCount++) {
                    $key = Get-WdConversationKey
                    if (-not $key) { break }
                    Update-WdConversationControl $view $key
                }
            } catch {
                $view.Controls = $false
                $view.Paused = $false
                Add-WdConversationLine $view '[viewer] Keyboard unavailable; continuing FOLLOW without controls.' 'Yellow'
            }
        }
        if ($view.Quit) { break }
        if ($view.Paused) {
            Show-WdConversationFrame $view
            if ($Iterations -eq 0 -or $iteration -lt $Iterations) { Start-Sleep -Milliseconds $PollMs }
            continue
        }
        try {
            $result = if ($null -eq $cursor) {
                Read-BridgeEventTail -Path $EventsPath -MaxLines $replayRows -MaxBytes 4194304
            } else {
                Read-BridgeEventDelta -Path $EventsPath -Cursor $cursor -MaxRows 200 -MaxBytes 4194304
            }
            $diagnostic = ''
            $diagnosticColor = 'Yellow'
            $view.Reader = [string]$result.status
            $view.Reason = [string]$result.reason
            $view.Lag = $null
            if ($result.status -in @('OK', 'IDLE')) {
                foreach ($row in @($result.rows)) {
                    $view.Counts.rows++
                    if ($skipInitial) { $view.Counts.skipped++; continue }
                    $hidden = Get-WdConversationSuppression $row $view.Agent $view.Kind
                    if ($hidden) { $view.Counts[$hidden]++; continue }
                    $view.Counts.visible++
                    $line = Format-WdConversationEvent $row
                    $displayText = if ($view.Interactive) { $line.ScreenText } else { $line.Text }
                    Add-WdConversationLine $view $displayText $line.Color
                }
                if ($null -ne $result.candidate_cursor) {
                    $cursor = $result.candidate_cursor
                    $skipInitial = $false
                }
                if ($null -ne $result.PSObject.Properties['snapshot_length'] -and $null -ne $cursor -and
                    $result.reason -ne 'log_missing') {
                    $view.Lag = [Math]::Max([long]0, [long]$result.snapshot_length - [long]$cursor.offset)
                }
                if ($result.reason -eq 'log_missing') {
                    $diagnostic = '[viewer] log_missing; waiting for bridge log. No runtime files created.'
                    $skipInitial = $false
                    $replayRows = [Math]::Max(40, $Tail)
                } elseif ($result.reason -in @('partial_record', 'no_rows') -and
                          ($null -eq $view.Lag -or $view.Lag -gt 0)) {
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
            $diagnostic = ConvertTo-WdConversationText $diagnostic 1000
            $view.Diagnostic = $diagnostic
            $view.DiagnosticColor = $diagnosticColor
            if ($diagnostic -and $diagnostic -cne $lastDiagnostic) {
                Add-WdConversationLine $view $diagnostic $diagnosticColor
            }
            $lastDiagnostic = $diagnostic
        } catch {
            $view.Reader = 'ERROR'
            $view.Reason = 'reader_error'
            $view.Lag = $null
            $view.Diagnostic = 'reader_error; cursor retained. Inspect reader/input; no work inferred.'
            $view.DiagnosticColor = 'Red'
            if ($lastDiagnostic -cne 'reader_error') {
                Add-WdConversationLine $view '[viewer] reader_error; cursor retained. Inspect reader/input; no work inferred.' 'Red'
            }
            $lastDiagnostic = 'reader_error'
        }
        Show-WdConversationFrame $view
        if ($Iterations -eq 0 -or $iteration -lt $Iterations) {
            Start-Sleep -Milliseconds $PollMs
        }
    }
    if ($view.Interactive) { [Console]::WriteLine() }
    Write-Host (Get-WdConversationSummary $view) -ForegroundColor Gray
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

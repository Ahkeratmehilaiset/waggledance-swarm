#requires -Version 5.1
<#
.SYNOPSIS
    Reconciles ONE stuck native wake attempt ONLY when strict failure evidence establishes an exact explicit refusal.
.DESCRIPTION
    Passive by default: reads native-bridge-wake.json, the snapshot it names (.wake.<snapshot_id>) and the relay's refusal
    receipt native-bridge-wake.json.refusal-<delivery_id> in ONE lane journal, prints the classification and the
    three SHA-256 digests, and writes nothing. Only that receipt is evidence. The relay writes it create-new and
    flushed after a COMPLETED Codex queue process that its own classifier (Get-WdNativeQueueOutcome) called
    rejected, BEFORE the rejected state, and removes it after; so submitting plus an exact receipt is exactly the
    crash window between the two. Every binding field, both output hashes and the classifier (loaded from the
    bundle's start-wd-tools-consumer.ps1) are re-verified. Anything else is UNKNOWN, including the legacy record
    whose terminal error has no delivery id and no exit code: text and timing are never evidence.
    With that evidence, -Apply also needs the three dry-run digests, -RefusedStatus rejected and an operator label.
    It holds the EXISTING relay lock exclusively, re-verifies the bytes and the evidence, creates a new fsynced
    audit (all three files byte-exact) and atomically replaces the state with the relay-owned retry shape (status
    rejected, rejections 1, rejected_reason). The snapshot stays in place, so the relay redelivers that exact wake
    after its backoff. It never calls the native queue, never touches queued submissions, never starts or stops a
    process and never writes queued or watching.

    Partial failures: a crash after the audit leaves the state submitting (blocked), and a rerun may apply again
    because every audit has its own name. A failed state write deletes its temp file and leaves the state
    unchanged. If File.Replace reports that the replacement could not be moved, the state file is gone: the error
    names the temp file holding the reconciled record (rename it to native-bridge-wake.json) and the audit holding
    the original bytes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Journal,
    [switch] $Apply,
    [string] $RefusedStatus = '',
    [string] $ExpectedStateSha256 = '',
    [string] $ExpectedSnapshotSha256 = '',
    [string] $ExpectedReceiptSha256 = '',
    [string] $Operator = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Rule = 'strict-failure-evidence-v1'
$Bound = @('agent', 'thread_id', 'generation', 'relay_pid', 'native_pid', 'delivery_id', 'snapshot_id')
$StateLimit = 32768  # the relay and the preflight refuse a larger state record

$full = [IO.Path]::GetFullPath($Journal)
if ($full -cne $Journal -or $full -notmatch '^[Cc]:\\' -or
    -not $full.EndsWith('\.codex-audit\wd-turn-loop', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'journal must be a normalized C: lane journal ending in .codex-audit\wd-turn-loop'
}
$walk = $full
while ($walk) {
    $item = Get-Item -LiteralPath $walk -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "reparse point on the journal path: $walk" }
    $walk = Split-Path -Parent $walk
}
$jsonArguments = @{ ErrorAction = 'Stop' }
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonArguments.DateKind = 'String' }
# The relay's own classifier (nested in Send-WdNativeToolsQueueMessage), loaded from the same bundle, so this
# helper can never accept an outcome that the relay would not.
$relayPath = Join-Path $PSScriptRoot 'start-wd-tools-consumer.ps1'
$relayItem = Get-Item -LiteralPath $relayPath -Force
if ($relayItem.PSIsContainer -or ($relayItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'the relay source is not a regular file' }
$parseErrors = $null
$relayAst = [Management.Automation.Language.Parser]::ParseFile($relayPath, [ref]$null, [ref]$parseErrors)
$classifier = @($relayAst.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Get-WdNativeQueueOutcome' }, $true))
if ($parseErrors.Count -or $classifier.Count -ne 1) { throw 'the relay classifier Get-WdNativeQueueOutcome is missing or ambiguous' }
. ([scriptblock]::Create($classifier[0].Extent.Text))
function Get-WdField($Object, [string] $Name) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-WdSha256([byte[]] $Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-', '') } finally { $sha.Dispose() }
}

function Read-WdEvidence([string] $Name, [int] $Limit) {
    $path = Join-Path $full $Name
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $null
    }
    if ($item.Length -gt $Limit) { throw "evidence file is over $Limit bytes: $Name" }
    $bytes = [IO.File]::ReadAllBytes($path)
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    return [pscustomobject]@{ Name = $Name; Path = $path; Bytes = $bytes; Hash = (Get-WdSha256 $bytes); Text = $text
        Mtime = [DateTimeOffset]::new($item.LastWriteTimeUtc.Ticks, [TimeSpan]::Zero) }
}

# Strict failure evidence: the relay's refusal receipt for THIS submitting attempt. Every binding field must equal
# the state, both output hashes must match, the exit code must be non-zero, the relay's classifier must
# re-classify the stored output as rejected, and the refusal must complete after the attempt. A missing,
# malformed or tampered field is UNKNOWN.
function Get-WdStrictFailureEvidence($Evidence) {
    $state = $Evidence.State
    $receiptFile = Read-WdEvidence ('native-bridge-wake.json.refusal-' + [string](Get-WdField $state 'delivery_id')) $StateLimit
    $Evidence.R = $receiptFile
    if ($null -eq $receiptFile) { return $null }
    try { $receipt = $receiptFile.Text | ConvertFrom-Json @jsonArguments } catch { return $null }
    if ((Get-WdField $receipt 'schema') -cne 'wd.native-queue-refusal.v1' -or (Get-WdField $receipt 'outcome') -cne 'rejected') { return $null }
    foreach ($name in $Bound) {
        $expected = [string](Get-WdField $state $name)
        if ([string]::IsNullOrEmpty($expected) -or [string](Get-WdField $receipt $name) -cne $expected) { return $null }
    }
    $exit = Get-WdField $receipt 'exit_code'; $stdout = Get-WdField $receipt 'stdout'; $stderr = Get-WdField $receipt 'stderr'
    if (-not ($exit -is [int] -or $exit -is [long]) -or $exit -eq 0 -or $stdout -isnot [string] -or $stderr -isnot [string]) { return $null }
    if ((Get-WdSha256 ([Text.Encoding]::UTF8.GetBytes($stdout))) -cne [string](Get-WdField $receipt 'stdout_sha256') -or
        (Get-WdSha256 ([Text.Encoding]::UTF8.GetBytes($stderr))) -cne [string](Get-WdField $receipt 'stderr_sha256')) { return $null }
    $outcome = Get-WdNativeQueueOutcome -ExitCode ([int]$exit) -Stdout $stdout -Stderr $stderr -ThreadId ([string](Get-WdField $state 'thread_id'))
    $cap = [regex]::Match($stderr, 'more than ([1-9][0-9]{0,5}) submissions').Groups[1].Value
    $code = Get-WdField $receipt 'code'; $recordedCap = Get-WdField $receipt 'cap'
    if ($outcome.outcome -cne 'rejected' -or -not ($code -is [int] -or $code -is [long]) -or $code -ne -32600 -or
        -not ($recordedCap -is [int] -or $recordedCap -is [long]) -or [string]$recordedCap -cne $cap) { return $null }
    try {
        $completed = [DateTimeOffset]::Parse([string](Get-WdField $receipt 'completed_at_utc'), [Globalization.CultureInfo]::InvariantCulture)
        $attempted = [DateTimeOffset]::Parse([string](Get-WdField $state 'updated_at_utc'), [Globalization.CultureInfo]::InvariantCulture)
    } catch { return $null }
    if ($completed -lt $attempted) { return $null }
    return [pscustomobject]@{ Reason = ('Codex queue rejected the submission; nothing was queued: ' + $stderr + $stdout); Cap = [int]$cap }
}
function Get-WdClassification {
    $s = Read-WdEvidence 'native-bridge-wake.json' $StateLimit
    $result = [ordered]@{ classification = 'unknown'; reason = ''; rule = $Rule; cap = $null
        state_sha256 = $(if ($s) { $s.Hash }); snapshot_sha256 = $null; receipt_sha256 = $null
        binding_limit = 'only the relay receipt of one completed, exactly classified refusal is evidence; terminal text and timing never are' }
    $evidence = [pscustomobject]@{ Result = $result; State = $null; S = $s; W = $null; R = $null; Strict = $null }
    if ($null -eq $s) { $result.reason = 'state must exist'; return $evidence }
    try { $state = $s.Text | ConvertFrom-Json @jsonArguments } catch { $result.reason = 'state is not JSON'; return $evidence }
    $evidence.State = $state
    $queue = $state.PSObject.Properties['queue_id']
    if ((Get-WdField $state 'schema') -cne 'wd.native-tools-wake.v1' -or (Get-WdField $state 'status') -cne 'submitting' -or
        $null -eq $queue -or $queue.Value -isnot [string] -or $queue.Value -cne '' -or
        [string](Get-WdField $state 'delivery_id') -cnotmatch '^[0-9a-f]{32}$') {
        $result.reason = 'state is not one unresolved submitting attempt'; return $evidence
    }
    # Attempt-bound snapshot (RCO1 c97): only the snapshot the record itself names is evidence.
    $snapshotId = [string](Get-WdField $state 'snapshot_id')
    if ($snapshotId -cnotmatch '^[0-9a-f]{32}$') { $result.reason = 'state names no attempt-bound snapshot'; return $evidence }
    $w = Read-WdEvidence ('native-bridge-wake.json.wake.' + $snapshotId) 65536
    $evidence.W = $w
    if ($null -eq $w) { $result.reason = 'the snapshot the state names must exist'; return $evidence }
    $result.snapshot_sha256 = $w.Hash
    $strict = Get-WdStrictFailureEvidence $evidence
    if ($evidence.R) { $result.receipt_sha256 = $evidence.R.Hash }
    if ($null -eq $strict) {
        $result.reason = 'UNKNOWN: no exact relay refusal receipt binds a completed refusal to this attempt (delivery id, ' +
            'bindings, exit code, output hashes and the relay classifier); text and timing are never evidence'
        return $evidence
    }
    $evidence.Strict = $strict
    $result.classification = 'explicit_rejection'
    $result.cap = $strict.Cap
    return $evidence
}

function Write-WdCreateNew([string] $Path, [byte[]] $Bytes) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

$first = Get-WdClassification
if (-not $Apply) { ($first.Result | ConvertTo-Json -Compress); return }
if ($first.Result.classification -cne 'explicit_rejection') { throw ('refusing: ' + $first.Result.reason) }
if ($RefusedStatus -cne 'rejected') {
    throw 'apply needs -RefusedStatus rejected: the one explicit-refusal status the relay accepts'
}
foreach ($digest in @($ExpectedStateSha256, $ExpectedSnapshotSha256, $ExpectedReceiptSha256)) {
    if ($digest -cnotmatch '^[0-9A-F]{64}$') { throw 'apply needs the three uppercase SHA-256 digests from the dry run' }
}
if ($Operator -cnotmatch '^[A-Za-z0-9._:@-]{1,80}$') { throw 'apply needs an operator label' }
if (-not $jsonArguments.ContainsKey('DateKind')) {
    throw 'apply needs ConvertFrom-Json -DateKind (PowerShell 7.5 or later) so every state field round-trips unchanged'
}
$lockPath = Join-Path $full 'native-bridge-wake.lock'
$lockItem = Get-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
if ($null -eq $lockItem -or $lockItem.PSIsContainer -or ($lockItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'the existing relay lock file is missing or not a regular file'
}
# FileMode.Open never creates the lock; FileShare.None fails while the relay (or anyone) holds it.
$lease = [IO.File]::Open($lockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    $again = Get-WdClassification
    $r = $again.Result
    if ($r.classification -cne 'explicit_rejection') { throw ('refusing: ' + $r.reason) }
    if ($r.state_sha256 -cne $ExpectedStateSha256 -or $r.snapshot_sha256 -cne $ExpectedSnapshotSha256 -or
        $r.receipt_sha256 -cne $ExpectedReceiptSha256) { throw 'refusing: the evidence changed since the dry run' }
    $delivery = [string](Get-WdField $again.State 'delivery_id')
    $utc = [DateTimeOffset]::UtcNow
    $now = $utc.ToString('o')
    $utf8 = New-Object Text.UTF8Encoding($false)
    $auditName = 'native-bridge-wake.reconciled-' + $delivery + '-' +
        $utc.ToString('yyyyMMddTHHmmssfffffffZ', [Globalization.CultureInfo]::InvariantCulture) + '.json'
    $audit = [ordered]@{ schema = 'wd.native-wake-reconciliation.v1'; rule = $Rule; classification = 'explicit_rejection'
        operator = $Operator; reconciled_at_utc = $now; status_written = 'rejected'; cap = $r.cap
        binding_limit = $r.binding_limit; snapshot = 'kept in place for relay redelivery'; native_queue = 'untouched'
        authority_effect = 'none'
        evidence = @(foreach ($e in @($again.S, $again.W, $again.R)) {
            [ordered]@{ name = $e.Name; sha256 = $e.Hash; bytes_base64 = [Convert]::ToBase64String($e.Bytes) } }) }
    $auditBytes = $utf8.GetBytes(($audit | ConvertTo-Json -Depth 6) + "`n")
    $reason = [string]$again.Strict.Reason
    if ($reason.Length -gt 512) { $reason = $reason.Substring(0, 512) }
    # The relay-owned retry shape: the relay redelivers the kept snapshot after its backoff.
    $next = [ordered]@{}
    foreach ($property in $again.State.PSObject.Properties) { $next[$property.Name] = $property.Value }
    $next.status = 'rejected'
    $next.rejections = 1
    $next.rejected_reason = $reason
    $next.previous_status = 'submitting'
    $next.reconciliation_audit = [ordered]@{ file = $auditName; sha256 = (Get-WdSha256 $auditBytes) }
    $next.reconciled_at_utc = $now
    $stateBytes = $utf8.GetBytes(($next | ConvertTo-Json -Depth 6) + "`n")
    if ($stateBytes.Length -gt $StateLimit) { throw 'refusing: the new record exceeds the relay 32768-byte limit' }
    Write-WdCreateNew (Join-Path $full $auditName) $auditBytes
    $temporary = Join-Path $full ('.native-bridge-wake.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Write-WdCreateNew $temporary $stateBytes
        [IO.File]::Replace($temporary, $again.S.Path, [NullString]::Value)
    } catch {
        $failure = $_.Exception.Message
        if ([IO.File]::Exists($again.S.Path)) {
            if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
            throw ('refusing: the state write failed and the state is unchanged; a rerun may apply again: ' + $failure)
        }
        throw ('the state file is gone after a failed replace: rename ' + $temporary + ' to native-bridge-wake.json ' +
            '(the reconciled record); the original bytes are in ' + $auditName + ': ' + $failure)
    }
    ([ordered]@{ applied = $true; status = 'rejected'; audit = $auditName; snapshot = 'kept' } | ConvertTo-Json -Compress)
} finally { $lease.Dispose() }

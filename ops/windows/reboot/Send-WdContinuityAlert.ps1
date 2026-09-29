#Requires -Version 5.1
<#
.SYNOPSIS
    Publish one operator-visible continuity alert per (agent, thread, reason, checkpoint digest).

.DESCRIPTION
    Called by the lane relay, under its lifetime-exclusive lock, after it has
    written its durable local continuity alert. Without this helper that alert
    stays in a journal file that no human reads, which is how the 2026-09-28
    22:59Z stall stayed silent for hours.

    The helper publishes ONE bridge event: type=message, status=continuity_alert,
    to=operator. It is never a request or a wake_request, never addressed to a
    paid lane, and carries authority none. The payload is bounded and holds only
    codes and digests, never checkpoint free text, thread ids or identity fields.

    Trust: deployment-manifest.json must hash to the externally anchored value,
    and every manifest entry under tools-bootstrap/.agent-bridge/bin/*.ps1 must
    match before the pinned Write-AgentEvent.ps1 is invoked. There is no
    "latest" pointer.

    Idempotency: a durable per-thread ledger
    <Worktree>\.codex-audit\wd-turn-loop\continuity-alert-v1-<ThreadId>.json
    records a 'submitting' intent before the call and the receipt after it. A new
    Codex thread after a restart gets its own ledger, so it can still publish. A
    repeated key gives already_reported (or queued again). An uncertain delivery
    (crash, suppressed or unparseable receipt) gives unknown and is never blindly
    retried, so the failure stays visible. A full ledger fails closed; reconcile
    it manually.

    CheckpointDigest is the lowercase SHA-256 of the checkpoint bytes. When the
    checkpoint is missing or unreadable the caller passes 64 zeros with
    -Reason checkpoint_unavailable; that sentinel and that reason are accepted
    only together. The key then repeats per thread, so an unavailable checkpoint
    is reported once per thread.

    ProgressKey (optional, 64 lowercase hex) is the caller's hash of the stable
    progress fields (task, status, next action, next wake, blockers). When it is
    given, the alert key uses it instead of CheckpointDigest, so a heartbeat
    rewrite that changes only timestamps or history does not alert again. The
    payload still carries the real CheckpointDigest. The all-zero ProgressKey is
    accepted only with the unavailable sentinel above.

    Output: exactly one JSON line (wd.continuity-alert-result.v1) on the success
    stream. status is one of:
      published         canonical durable event
      queued            accepted into the spool only: not yet operator-visible,
                        not completion
      already_reported  this key was already published
      unknown           anything else, with reason_code
    $LASTEXITCODE is set to 0 for published, already_reported and queued, and to
    1 for unknown. The script never calls exit, so a relay that invokes it with
    & stays alive.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Agent,
    [Parameter(Mandatory)] [string] $TaskId,
    [Parameter(Mandatory)] [string] $ThreadId,
    [Parameter(Mandatory)] [string] $Worktree,
    [Parameter(Mandatory)] [string] $Reason,
    [Parameter(Mandatory)] [string] $CheckpointDigest,
    [string] $ProgressKey = '',
    [string] $BundleRoot = '',
    [string] $ExpectedManifestHash = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$LedgerSchema = 'wd.continuity-alert-ledger.v1'
$MaxLedgerBytes = 262144
$MaxLedgerEntries = 256
$BinPrefix = 'tools-bootstrap/.agent-bridge/bin/'
$ResultMarker = 'WD_CONTINUITY_ALERT_RESULT'
$script:AlertResult = $null

# Early returns unwind through a private marker exception, never through exit,
# so the relay host that invoked this script with & stays alive.
function Write-AlertResult {
    param([string] $Status, [string] $AlertKey = '', [hashtable] $Extra = @{})
    $result = [ordered]@{schema = 'wd.continuity-alert-result.v1'; status = $Status; alert_key = $AlertKey}
    foreach ($name in @($Extra.Keys | Sort-Object)) { $result[$name] = $Extra[$name] }
    $script:AlertResult = $result
    throw $ResultMarker
}

function Stop-Unknown {
    param([string] $Code, [string] $AlertKey = '')
    Write-AlertResult -Status 'unknown' -AlertKey $AlertKey -Extra @{reason_code = $Code}
}

function Get-Sha256Hex {
    param([string] $Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))
        return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

# .NET hashing, not Get-FileHash: in Windows PowerShell 5.1 that is a script-module
# function which fails to load under a pwsh 7 parent's PSModulePath.
function Get-FileSha256Upper {
    param([string] $Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try { $bytes = $sha.ComputeHash($stream) } finally { $stream.Dispose() }
        return ([BitConverter]::ToString($bytes)).Replace('-', '')
    } finally { $sha.Dispose() }
}

function Test-AbsoluteDirectory {
    param([string] $Path)
    if (-not $Path -or -not [IO.Path]::IsPathRooted($Path)) { return $false }
    # IsPathRooted also accepts drive-relative (C:foo) and root-relative (\foo) forms.
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.TrimEnd('\', '/').Equals($Path.TrimEnd('\', '/'), [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    return [IO.Directory]::Exists($Path)
}

function Assert-NoReparsePoint {
    param([string] $Path)
    if ([IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path)) {
        if (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Stop-Unknown 'ledger_path_reparse_point'
        }
    }
}

function Write-LedgerAtomic {
    param([string] $Path, $Ledger)
    $json = ConvertTo-Json -InputObject $Ledger -Depth 6 -Compress
    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt $MaxLedgerBytes) { Stop-Unknown 'ledger_oversized' }
    $temp = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temp, $json, (New-Object Text.UTF8Encoding($false)))
    # PowerShell turns $null into '' for a string argument; File.Replace needs a real null.
    if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temp, $Path, [NullString]::Value) }
    else { [IO.File]::Move($temp, $Path) }
}

function Invoke-ContinuityAlert {
    # --- parameter validation (codes and digests only; no free text reaches the bridge) ----
    if ($Agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') { Stop-Unknown 'invalid_agent' }
    if ($TaskId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$') { Stop-Unknown 'invalid_task_id' }
    # The thread id names the ledger file: exact lowercase UUID only.
    if ($ThreadId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
        Stop-Unknown 'invalid_thread_id'
    }
    if ($Reason -cnotmatch '^[a-z0-9][a-z0-9_.:-]{0,127}$') { Stop-Unknown 'invalid_reason_code' }
    if ($CheckpointDigest -cnotmatch '^[0-9a-f]{64}$') { Stop-Unknown 'invalid_checkpoint_digest' }
    # Missing or unreadable checkpoint: the caller passes the all-zero digest as an explicit
    # "unavailable" sentinel. It is bound both ways to reason checkpoint_unavailable, so a real
    # checkpoint can never be reported as unavailable and a sentinel never looks like a hash.
    $unavailable = ($CheckpointDigest -ceq ('0' * 64))
    if ($unavailable -ne ($Reason -ceq 'checkpoint_unavailable')) { Stop-Unknown 'invalid_checkpoint_sentinel' }
    if (-not (Test-AbsoluteDirectory $Worktree)) { Stop-Unknown 'invalid_worktree' }

    if ($ProgressKey) {
        if ($ProgressKey -cnotmatch '^[0-9a-f]{64}$') { Stop-Unknown 'invalid_progress_key' }
        # The zero key means "no checkpoint to hash" and is bound to the same sentinel.
        if (($ProgressKey -ceq ('0' * 64)) -ne $unavailable) { Stop-Unknown 'invalid_checkpoint_sentinel' }
    }

    # Without ProgressKey the key is unchanged. With it, a separate domain tag keeps the
    # two key spaces apart even if a progress hash ever equals a checkpoint digest.
    if ($ProgressKey) {
        $alertKey = Get-Sha256Hex ($Agent + "`n" + $ThreadId + "`n" + $Reason + "`nprogress`n" + $ProgressKey)
    } else {
        $alertKey = Get-Sha256Hex ($Agent + "`n" + $ThreadId + "`n" + $Reason + "`n" + $CheckpointDigest)
    }

    $bundle = $BundleRoot
    if (-not $bundle) {
        $wrapper = [string]$env:WD_BRIDGE_PYTHON_WRAPPER
        if (-not $wrapper) { Stop-Unknown 'bundle_anchor_missing' $alertKey }
        $bundle = Split-Path -Parent $wrapper
    }
    $expectedHash = $ExpectedManifestHash
    if (-not $expectedHash) { $expectedHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH }
    if ($expectedHash -cnotmatch '^[0-9A-Fa-f]{64}$') { Stop-Unknown 'manifest_anchor_missing' $alertKey }
    if (-not (Test-AbsoluteDirectory $bundle)) { Stop-Unknown 'bundle_root_invalid' $alertKey }

    # --- per-thread ledger under its own exclusive lock (the relay lock is not assumed) ---------
    $auditDir = Join-Path $Worktree '.codex-audit'
    $journal = Join-Path $auditDir 'wd-turn-loop'
    Assert-NoReparsePoint $auditDir
    Assert-NoReparsePoint $journal
    [void][IO.Directory]::CreateDirectory($journal)
    Assert-NoReparsePoint $auditDir
    Assert-NoReparsePoint $journal
    $ledgerPath = Join-Path $journal ('continuity-alert-v1-' + $ThreadId + '.json')
    $lockPath = Join-Path $journal ('continuity-alert-v1-' + $ThreadId + '.lock')
    Assert-NoReparsePoint $ledgerPath
    Assert-NoReparsePoint $lockPath

    $lock = $null
    try {
        try {
            $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::None)
        } catch { Stop-Unknown 'ledger_locked' $alertKey }

        $ledger = [ordered]@{schema = $LedgerSchema; agent = $Agent; thread_id = $ThreadId; entries = @()}
        if ([IO.File]::Exists($ledgerPath)) {
            if ((New-Object IO.FileInfo($ledgerPath)).Length -gt $MaxLedgerBytes) { Stop-Unknown 'ledger_oversized' $alertKey }
            $parsed = $null
            try { $parsed = [IO.File]::ReadAllText($ledgerPath, [Text.Encoding]::UTF8) | ConvertFrom-Json }
            catch { Stop-Unknown 'ledger_corrupt' $alertKey }
            if ($null -eq $parsed -or -not $parsed.PSObject.Properties['schema'] -or
                -not $parsed.PSObject.Properties['entries'] -or -not $parsed.PSObject.Properties['agent'] -or
                -not $parsed.PSObject.Properties['thread_id'] -or $parsed.schema -cne $LedgerSchema) {
                Stop-Unknown 'ledger_corrupt' $alertKey
            }
            if ($parsed.agent -cne $Agent -or $parsed.thread_id -cne $ThreadId) {
                Stop-Unknown 'ledger_identity_mismatch' $alertKey
            }
            $entries = @()
            foreach ($entry in @($parsed.entries)) {
                if ($null -eq $entry -or -not $entry.PSObject.Properties['key'] -or
                    -not $entry.PSObject.Properties['status'] -or
                    [string]$entry.key -cnotmatch '^[0-9a-f]{64}$' -or
                    [string]$entry.status -cnotin @('submitting', 'published', 'queued', 'uncertain')) {
                    Stop-Unknown 'ledger_corrupt' $alertKey
                }
                $entries += $entry
            }
            $ledger.entries = $entries
        }

        foreach ($entry in @($ledger.entries)) {
            if ([string]$entry.key -ceq $alertKey) {
                if ([string]$entry.status -ceq 'published') {
                    Write-AlertResult -Status 'already_reported' -AlertKey $alertKey -Extra @{delivery_status = 'canonical'}
                }
                if ([string]$entry.status -ceq 'queued') {
                    # Accepted into the spool earlier: never re-send, never claim operator-visible.
                    Write-AlertResult -Status 'queued' -AlertKey $alertKey -Extra @{delivery_status = 'queued'}
                }
                Stop-Unknown 'delivery_uncertain' $alertKey
            }
        }
        if (@($ledger.entries).Count -ge $MaxLedgerEntries) { Stop-Unknown 'ledger_full' $alertKey }

        # --- trust: anchored manifest, then every pinned bridge helper the writer may load ---
        $manifestPath = Join-Path $bundle 'deployment-manifest.json'
        if (-not [IO.File]::Exists($manifestPath)) { Stop-Unknown 'manifest_missing' $alertKey }
        if ((Get-FileSha256Upper $manifestPath) -cne $expectedHash.ToUpperInvariant()) {
            Stop-Unknown 'manifest_hash_mismatch' $alertKey
        }
        $manifest = $null
        try { $manifest = [IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json }
        catch { Stop-Unknown 'manifest_corrupt' $alertKey }
        if ($null -eq $manifest -or -not $manifest.PSObject.Properties['files']) { Stop-Unknown 'manifest_corrupt' $alertKey }
        $writerRelative = $BinPrefix + 'Write-AgentEvent.ps1'
        $pinned = @($manifest.files.PSObject.Properties | Where-Object {
            $_.Name.StartsWith($BinPrefix, [StringComparison]::Ordinal) -and
            $_.Name.EndsWith('.ps1', [StringComparison]::Ordinal)
        })
        if (-not @($pinned | Where-Object { $_.Name -ceq $writerRelative })) { Stop-Unknown 'writer_not_pinned' $alertKey }
        foreach ($entry in $pinned) {
            $file = Join-Path $bundle ($entry.Name.Replace('/', [IO.Path]::DirectorySeparatorChar))
            if (-not [IO.File]::Exists($file) -or
                (Get-FileSha256Upper $file) -cne ([string]$entry.Value).ToUpperInvariant()) {
                Stop-Unknown 'helper_hash_mismatch' $alertKey
            }
        }
        $writer = Join-Path $bundle ($writerRelative.Replace('/', [IO.Path]::DirectorySeparatorChar))

        # --- durable intent, then publish ---------------------------------------------------------
        $intent = [ordered]@{key = $alertKey; status = 'submitting'; reason = $Reason; task_id = $TaskId;
            checkpoint_digest = $CheckpointDigest; progress_key = $ProgressKey;
            at_utc = [DateTimeOffset]::UtcNow.ToString('o');
            delivery_status = ''; event_ts_utc = ''}
        $ledger.entries = @($ledger.entries) + @($intent)
        Write-LedgerAtomic $ledgerPath $ledger

        $message = 'Continuity alert: lane ' + $Agent + ' needs operator reconciliation (' + $Reason +
            '). Task ' + $TaskId + '. This notice grants no authority and requests no lane action.'
        $payload = ConvertTo-Json -Compress -InputObject ([ordered]@{schema = 'wd.continuity-alert.v1';
            agent = $Agent; task_id = $TaskId; reason = $Reason; checkpoint_digest = $CheckpointDigest;
            alert_key = $alertKey; authority = 'none'})
        $outcome = 'uncertain'
        $deliveryStatus = ''
        $eventTs = ''
        try {
            $raw = (& $writer -Agent $Agent -Type message -Status continuity_alert -TaskId $TaskId -To operator `
                -Message $message -PayloadJson $payload -ReceiptJson | Out-String).Trim()
            $receipt = $raw | ConvertFrom-Json
            $delivery = $receipt._bridge_delivery
            # Bind the receipt to THIS event; never infer completion from a clean return.
            if ($receipt.agent -ceq $Agent -and $receipt.type -ceq 'message' -and
                $receipt.status -ceq 'continuity_alert' -and $receipt.to -ceq 'operator' -and
                $receipt.task_id -ceq $TaskId -and $delivery.accepted -eq $true) {
                if ($delivery.delivery_status -ceq 'canonical' -and $delivery.canonical_durable -eq $true) {
                    $outcome = 'published'
                } elseif ($delivery.delivery_status -ceq 'queued') {
                    $outcome = 'queued'
                }
                $deliveryStatus = [string]$delivery.delivery_status
                if ($receipt.PSObject.Properties['ts_utc']) {
                    # pwsh 7 ConvertFrom-Json turns the timestamp into a DateTime whose
                    # [string] form is culture-specific; report ISO 8601 UTC in both shells.
                    $tsValue = $receipt.ts_utc
                    if ($tsValue -is [DateTime]) { $eventTs = $tsValue.ToUniversalTime().ToString('o') }
                    else { $eventTs = [string]$tsValue }
                }
            }
        } catch {
            $outcome = 'uncertain'
        }
        $intent.status = $outcome
        $intent.delivery_status = $deliveryStatus
        $intent.event_ts_utc = $eventTs
        Write-LedgerAtomic $ledgerPath $ledger
        if ($outcome -ceq 'uncertain') { Stop-Unknown 'delivery_uncertain' $alertKey }
        Write-AlertResult -Status $outcome -AlertKey $alertKey -Extra @{delivery_status = $deliveryStatus; event_ts_utc = $eventTs}
    } finally {
        if ($null -ne $lock) { $lock.Dispose() }
    }
}

try {
    Invoke-ContinuityAlert
} catch {
    if ($null -eq $script:AlertResult -or $_.Exception.Message -cne $ResultMarker) {
        # Any unforeseen failure is visible as unknown, never as success.
        $script:AlertResult = [ordered]@{schema = 'wd.continuity-alert-result.v1'; status = 'unknown';
            alert_key = ''; reason_code = 'internal_error'}
    }
}
if ($script:AlertResult.status -ceq 'unknown') { $global:LASTEXITCODE = 1 } else { $global:LASTEXITCODE = 0 }
Write-Output (ConvertTo-Json -InputObject $script:AlertResult -Compress -Depth 4)

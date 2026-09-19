#requires -Version 5.1
<#
.SYNOPSIS
    Reports compact-state, wake, and write-scope parallelism for the WD fleet.

.DESCRIPTION
    Read-only. It does not acknowledge bridge events, consume wake sentinels,
    change Git state, or start processes. Missing or stale compact state is
    reported, never repaired implicitly.

    Compatibility: runnable and summary.runnable_lanes retain their v1 meaning
    (a recorded next action outside the literal blocked status). They do not
    establish current runtime readiness. runnable_evidence is an observation,
    not task authority or a scheduler decision. No heartbeat is counted as
    substantive progress. A readiness record is not a full runtime attestation.
#>
[CmdletBinding()]
param(
    [string] $ManifestPath = '',
    [string] $CurrentStatePath = 'C:\Python\WD_REBOOT_STATE_CURRENT.json',
    [ValidateRange(60, 86400)]
    [int] $StaleAfterSeconds = 1800,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-WdStatusManifest {
    param([string] $Requested)

    if ($Requested) { return [IO.Path]::GetFullPath($Requested) }
    $pointerPath = $CurrentStatePath
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        throw "current reboot pointer is missing: $pointerPath"
    }
    $pointer = Read-WdStatusRecord -Path $pointerPath
    if ([string]::IsNullOrWhiteSpace([string]$pointer.fleet_manifest)) {
        throw 'current reboot pointer has no fleet_manifest'
    }
    return [IO.Path]::GetFullPath([string]$pointer.fleet_manifest)
}

function Get-WdStatusGitText {
    param(
        [Parameter(Mandatory)] [string] $Git,
        [Parameter(Mandatory)] [string] $Worktree,
        [Parameter(Mandatory)] [string[]] $Arguments
    )
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Git --no-replace-objects -C $Worktree @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($exitCode -ne 0) { return '' }
    return (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Get-WdStatusProperty {
    param($Object, [string] $Name)
    if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) {
        return $Object.PSObject.Properties[$Name].Value
    }
    return $null
}

function Read-WdStatusRecord {
    param([string] $Path)
    # Bound memory and I/O even if a concurrently written record grows.
    # Permit atomic checkpoint/readiness replacement while this snapshot is open.
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open,
        [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        $buffer = New-Object byte[] 32769
        $count = 0
        while ($count -lt $buffer.Length) {
            $read = $stream.Read($buffer, $count, $buffer.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -gt 32768) { throw 'record exceeds 32 KiB' }
        $jsonArguments=@{ErrorAction='Stop'}
        if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonArguments.DateKind='String' }
        return ([Text.Encoding]::UTF8.GetString($buffer, 0, $count).TrimStart(
            [char]0xFEFF) | ConvertFrom-Json @jsonArguments)
    }
    finally { $stream.Dispose() }
}

function Get-WdStatusWakeObservation {
    param([string] $Root, [string] $Agent, [string] $Worktree,
        [object[]] $Processes, [string] $MonitorFile)
    $path = Join-Path $Root ('shared/monitor_{0}.cursor.json' -f $Agent)
    $result = [pscustomobject]@{
        source_domain = 'bounded_agent_inbox_cursor_delta'; source_path = $path
        status = 'unknown'; pending = $null; reason = 'inbox_cursor_missing'
        sentinel_present = Test-Path -LiteralPath (Join-Path $Root ('wake_' + $Agent)) -PathType Leaf
        observed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        task_completion_verified = $false
    }
    try {
        # Native Monitor permits a lane-local -StatePath. Resolve it from an
        # exact installed script invocation, never guess a recent file by glob.
        $candidates = @(
            foreach ($process in $Processes) {
                if ([string](Get-WdStatusProperty $process 'Name') -notmatch '^(powershell|pwsh)\.exe$') { continue }
                $arguments = @{}
                $command = [string](Get-WdStatusProperty $process 'CommandLine')
                foreach ($name in @('File','Agent','StatePath')) {
                    $pattern = '(?i)(?:^|\s)-' + $name + '\s+(?:"(?<v>[^"]+)"|''(?<v>[^'']+)''|(?<v>\S+))(?=\s|$)'
                    $found = [regex]::Matches($command, $pattern)
                    if ($found.Count -eq 1) { $arguments[$name] = $found[0].Groups['v'].Value }
                }
                if ($arguments.ContainsKey('File') -and $arguments.ContainsKey('Agent') -and
                    $arguments.Agent -ceq $Agent -and
                    ([IO.Path]::GetFullPath([string]$arguments.File)).Equals(
                        [IO.Path]::GetFullPath($MonitorFile), [StringComparison]::OrdinalIgnoreCase)) {
                    if (([regex]::Matches($command, '(?i)(?:^|\s)-StatePath(?:\s|$)')).Count -gt 0 -and
                        -not $arguments.ContainsKey('StatePath')) { throw 'ambiguous Monitor StatePath' }
                    $arguments
                }
            }
        )
        if ($candidates.Count -gt 1) { $result.reason = 'multiple_live_inbox_monitors'; return $result }
        if ($candidates.Count -eq 1 -and $candidates[0].ContainsKey('StatePath')) {
            $custom = [string]$candidates[0].StatePath
            if (-not [IO.Path]::IsPathRooted($custom)) { throw 'relative Monitor StatePath' }
            $custom = [IO.Path]::GetFullPath($custom)
            $allowed = $false
            foreach ($directory in @($Root, (Join-Path $Worktree '.codex-audit'))) {
                $prefix = [IO.Path]::GetFullPath($directory).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
                if ($custom.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { $allowed = $true }
            }
            if (-not $allowed) { throw 'Monitor StatePath outside lane/runtime evidence roots' }
            $path = $custom; $result.source_path = $path
        }
    } catch { $result.reason = 'inbox_source_unknown: ' + $_.Exception.Message; return $result }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $result }
    try {
        # One atomic bounded record read: never combine metadata and a cursor
        # from different replacements. Human/filtered/legacy cursors cannot
        # attest that all addressed wake requests were delivered.
        $result.reason = 'inbox_cursor_invalid'
        $record = Read-WdStatusRecord $path
        $metadata = Get-WdStatusProperty $record 'metadata'
        if ((Get-WdStatusProperty $metadata 'agent') -cne $Agent -or
            (Get-WdStatusProperty $metadata 'delivery_scope') -cne 'agent_inbox' -or
            (Get-WdStatusProperty $metadata 'targeted_only') -isnot [bool] -or
            -not $metadata.targeted_only -or
            (Get-WdStatusProperty $metadata 'include_wake_requests') -isnot [bool] -or
            -not $metadata.include_wake_requests -or
            (Get-WdStatusProperty $metadata 'from_agent')) {
            $result.reason = 'inbox_cursor_scope_unknown'; return $result
        }
        $cursor = Get-WdStatusProperty $record 'cursor'
        if ($null -eq $cursor -or -not (Get-BridgeCursorValidation $cursor).valid) { return $result }
        $delta = Read-BridgeEventDelta -Path (Join-Path $Root 'shared/events.jsonl') `
            -Cursor $cursor -MaxRows 1200 -MaxBytes 1048576
        $result.reason = $delta.reason
        if ($delta.status -cnotin @('OK','IDLE')) { return $result }
        foreach ($event in @($delta.rows)) {
            if ((Get-WdStatusProperty $event 'agent') -cne $Agent -and
                @(Get-BridgeEventTargets $event) -contains $Agent -and
                (Test-BridgeWakeEligible $event)) {
                $result.status = 'pending'; $result.pending = $true
                $result.reason = 'eligible_event_after_inbox_cursor'; return $result
            }
        }
        # Truncated, partial, missing or bounded reads never prove an empty inbox.
        if ($null -ne $delta.candidate_cursor -and
            $delta.candidate_cursor.offset -eq $delta.snapshot_length) {
            $result.status = 'drained'; $result.pending = $false
            $result.reason = 'no_eligible_event_through_observed_eof'
        } else { $result.reason = 'inbox_delta_incomplete' }
    } catch { $result.reason = 'inbox_observation_failed: ' + $_.Exception.Message }
    return $result
}

function Get-WdStatusTurnExecution {
    param($Definition, [object[]] $Processes, [bool] $QueryAvailable,
        [string] $HandshakeRoot, [string] $RuntimeRoot, [DateTimeOffset] $Now)
    $result = [pscustomobject]@{
        source_domain = 'live_launcher_and_bounded_handshake_observation'
        observed_turn_mode = 'unknown'; external_wake_support = 'unknown'
        reason = 'process_query_unavailable'; observed_pid = $null
        observed_worktree = $null; recorded_generation = $null
        handshake_path = $null; turn_execution_verified = $false
        relay_status = 'unknown'; relay_error = $null
    }
    if (-not $QueryAvailable) { return $result }
    if ($Definition.agent -ceq 'codex-tools-1') {
        $result.reason = 'canonical_consumer_runtime_reported_separately'
        return $result
    }
    try {
        $result.reason = 'launcher_absent_or_ambiguous'
        $candidates = @(
            foreach ($process in $Processes) {
                if ([string](Get-WdStatusProperty $process 'Name') -notmatch '^(powershell|pwsh)\.exe$') { continue }
                $command = [string](Get-WdStatusProperty $process 'CommandLine')
                if (-not $command) { throw 'PowerShell command line unavailable' }
                $arguments = @{}
                foreach ($name in @('File','Agent','RunId','HandshakeDirectory')) {
                    $pattern = '(?i)(?:^|\s)-' + $name + '\s+(?:"(?<v>[^"]+)"|''(?<v>[^'']+)''|(?<v>\S+))(?=\s|$)'
                    $found = [regex]::Matches($command, $pattern)
                    if ($found.Count -eq 1) { $arguments[$name] = $found[0].Groups['v'].Value }
                }
                if (-not $arguments.ContainsKey('File')) { continue }
                $leaf = [IO.Path]::GetFileName(($arguments.File -split '\\')[-1])
                $genericMatch = $leaf -ieq 'start-wd-agent.ps1' -and
                    $arguments.ContainsKey('Agent') -and $arguments.Agent -ceq $Definition.agent
                $legacyMatch = @($Definition.legacy_process_markers) -icontains $leaf
                if ($genericMatch -or $legacyMatch) {
                    [pscustomobject]@{ process=$process; arguments=$arguments }
                }
            }
        )
        if ($candidates.Count -ne 1) { return $result }
        $candidate = $candidates[0]; $arguments = $candidate.arguments
        $result.reason = 'handshake_missing_or_mismatched'
        if (-not $HandshakeRoot -or -not $arguments.ContainsKey('RunId') -or
            -not $arguments.ContainsKey('HandshakeDirectory') -or
            $arguments.RunId -cnotmatch '^[A-Za-z0-9._-]{1,128}$' -or
            $arguments.RunId -cin @('.','..')) { return $result }
        $comparison = if ([IO.Path]::DirectorySeparatorChar -eq '\') {
            [StringComparison]::OrdinalIgnoreCase
        } else { [StringComparison]::Ordinal }
        $expectedDirectory = [IO.Path]::GetFullPath((Join-Path $HandshakeRoot $arguments.RunId))
        if (-not ([IO.Path]::GetFullPath($arguments.HandshakeDirectory)).Equals($expectedDirectory, $comparison)) { return $result }
        $handshakePath = Join-Path $expectedDirectory ($Definition.agent + '.json')
        foreach ($path in @($HandshakeRoot, $expectedDirectory, $handshakePath)) {
            if (((Get-Item -LiteralPath $path -Force -ErrorAction Stop).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $result }
        }
        $record = Read-WdStatusRecord $handshakePath
        $processStartValue = Get-WdStatusProperty $candidate.process 'CreationDate'
        $createdValue = Get-WdStatusProperty $record 'created_at_utc'
        $processStart = if ($processStartValue -is [datetime]) { [DateTimeOffset]$processStartValue } else {
            [DateTimeOffset]::Parse([string]$processStartValue, [Globalization.CultureInfo]::InvariantCulture)
        }
        $created = if ($createdValue -is [datetime]) { [DateTimeOffset]$createdValue } else {
            [DateTimeOffset]::Parse([string]$createdValue, [Globalization.CultureInfo]::InvariantCulture)
        }
        if ([int]$record.schema_version -ne 1 -or [string]$record.status -cne 'bridge_bootstrapped' -or
            [string]$record.agent -cne $Definition.agent -or [int]$record.pid -le 0 -or
            [int]$record.pid -ne [int]$candidate.process.ProcessId -or
            [string]$record.run_id -cne $arguments.RunId -or [string]$record.session_id -cne $arguments.RunId -or
            $created -lt $processStart -or $created -gt $Now.AddSeconds(5) -or
            -not ([IO.Path]::GetFullPath([string]$record.runtime_root)).Equals($RuntimeRoot, $comparison) -or
            [string]$record.bundle_generation -cnotmatch '^(?:[0-9a-f]{32}|[0-9a-f]{40})$') { return $result }
        # Missing mode is the pre-managed handshake compatibility contract, not
        # an inference from the selected (possibly newer) manifest's turn_mode.
        $modeProperty = $record.PSObject.Properties['turn_mode']
        $mode = if ($null -eq $modeProperty) { 'legacy_interactive' } else { [string]$modeProperty.Value }
        if ($null -ne $modeProperty -and $mode -cnotin @('interactive','managed')) { return $result }
        $observedWorktree = [string](Get-WdStatusProperty $record 'worktree')
        if ([string]::IsNullOrWhiteSpace($observedWorktree) -or
            -not [IO.Path]::IsPathRooted($observedWorktree)) { return $result }
        $observedWorktree = [IO.Path]::GetFullPath($observedWorktree)
        # Publish positive fields only after every required field is validated.
        $result.observed_turn_mode = $mode
        $result.observed_pid = [int]$candidate.process.ProcessId
        $result.observed_worktree = $observedWorktree
        $result.recorded_generation = [string]$record.bundle_generation
        $result.handshake_path = $handshakePath
        $result.reason = if ($mode -ceq 'legacy_interactive') { 'live_launcher_and_legacy_handshake' } else { 'live_launcher_and_handshake' }
        $result.external_wake_support = if ($Definition.agent -ceq 'codex-lead-1' -and
            $mode -cin @('interactive','legacy_interactive')) { 'unsupported_existing_interactive' } else { 'not_verified' }
        if ($Definition.agent -ceq 'codex-lead-1' -and $mode -ceq 'interactive') {
            $readyPath=Join-Path $observedWorktree '.codex-audit/wd-turn-loop/native-terminal.json'
            if (Test-Path -LiteralPath $readyPath -PathType Leaf) {
                $result.external_wake_support='native_queue_unverified'
                $ready=Read-WdStatusRecord $readyPath
                # Expose negative transport evidence even when readiness fails.
                if ($ready.schema -ceq 'wd.native-lead-ready.v1' -and $ready.agent -ceq $Definition.agent -and
                    $ready.generation -ceq $record.bundle_generation -and $ready.session_id -ceq $record.session_id -and
                    [int]$ready.relay_pid -eq [int]$record.pid) {
                    $result.relay_status=[string]$ready.status
                    $result.relay_error=Get-WdStatusProperty $ready 'error'
                }
                $native=@($Processes | Where-Object {
                    [int]$_.ProcessId -eq [int]$ready.native_pid -and [int]$_.ParentProcessId -eq [int]$record.pid -and
                    [string]$_.Name -ieq 'codex.exe'
                })
                if ($ready.schema -ceq 'wd.native-lead-ready.v1' -and $ready.agent -ceq 'codex-lead-1' -and
                    $ready.status -ceq 'terminal_ready' -and $ready.bridge_wake_transport -ceq 'codex_queue' -and
                    $ready.generation -ceq $record.bundle_generation -and $ready.session_id -ceq $record.session_id -and
                    [int]$ready.relay_pid -eq [int]$record.pid -and $native.Count -eq 1 -and
                    $ready.thread_id -cmatch '^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$' -and
                    ([string]$ready.worktree).Equals($observedWorktree,$comparison) -and
                    [Math]::Abs(((ConvertTo-WdStatusUtc $ready.relay_process_start_utc)-$processStart).TotalSeconds) -lt 1 -and
                    [Math]::Abs(((ConvertTo-WdStatusUtc $ready.native_process_start_utc)-(ConvertTo-WdStatusUtc $native[0].CreationDate)).TotalSeconds) -lt 1 -and
                    [string]$native[0].CommandLine -match ('\bresume\s+"?' + [regex]::Escape($ready.thread_id) + '"?(?:\s|$)')) {
                    $result.external_wake_support='native_queue_bridge'
                    $result.reason='live_native_lead_and_queue_relay_observed'
                }
            }
        }
    }
    catch { <# Preserve the failed observation stage; never infer a live mode. #> }
    return $result
}

function ConvertTo-WdStatusUtc {
    param($Value)
    if ($Value -is [datetime]) { return ([DateTimeOffset]$Value).ToUniversalTime() }
    if ([string]$Value -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$') { throw 'timestamp requires explicit timezone' }
    return [DateTimeOffset]::Parse([string]$Value,[Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
}

function Get-WdStatusClaims {
    param([string]$Root, [DateTimeOffset]$Now)
    $active=[Collections.Generic.List[object]]::new()
    $errors=[Collections.Generic.List[string]]::new()
    $directory=Join-Path $Root 'work_queue/claims'
    if (Test-Path -LiteralPath $directory) {
        foreach ($file in @(Get-ChildItem -LiteralPath $directory -Filter '*.json' -File)) {
            try {
                $claim=Read-WdStatusRecord $file.FullName
                if (Get-WdStatusProperty $claim 'released_at_utc') { continue }
                if ((Get-WdStatusProperty $claim 'mode') -ceq 'read') { continue }
                if ((Get-WdStatusProperty $claim 'mode') -cne 'write') { throw 'unknown claim mode' }
                $stamp=Get-WdStatusProperty $claim 'last_heartbeat_utc'
                if (-not $stamp) { $stamp=Get-WdStatusProperty $claim 'claimed_at_utc' }
                $heartbeat=ConvertTo-WdStatusUtc $stamp
                if ($heartbeat -gt $Now.AddSeconds(5)) { throw 'future claim heartbeat' }
                $lease=300
                $value=Get-WdStatusProperty $claim 'lease_seconds'
                if ($null -ne $value -and (-not [int]::TryParse([string]$value,[ref]$lease) -or $lease -le 0)) { throw 'invalid claim lease' }
                $expires=$heartbeat.AddSeconds($lease)
                $explicit=Get-WdStatusProperty $claim 'claim_lease_expires_utc'
                if ($explicit) { $specified=ConvertTo-WdStatusUtc $explicit; if ($specified -gt $expires) { $expires=$specified } }
                if ($Now -ge $expires) { continue }
                if (-not $claim.agent -or -not $claim.cwd) { throw 'missing claim owner or cwd' }
                $resources=@(Resolve-BridgeResourceScopes -Scopes @($claim.write_scope) -Worktree $claim.cwd -BridgeRoot $Root)
                $active.Add([pscustomobject]@{agent=$claim.agent;task_id=$claim.task_id;resources=$resources;expires_at_utc=$expires.ToString('o')})
            } catch { $errors.Add($file.Name + ': ' + $_.Exception.Message) }
        }
    }
    $collisions=@(for ($i=0; $i -lt $active.Count; $i++) {
        for ($j=$i+1; $j -lt $active.Count; $j++) {
            if ($active[$i].agent -cne $active[$j].agent -and
                (Test-BridgeResourceOverlap $active[$i].resources $active[$j].resources)) {
                [pscustomobject]@{agents=@($active[$i].agent,$active[$j].agent);resources=@($active[$i].resources,$active[$j].resources)}
            }
        }
    })
    [pscustomobject]@{status=$(if ($errors.Count) {'unknown'} else {'observed'});source_domain='active_claim_files';
        observed_at_utc=$Now.ToString('o');active_claims=@($active);errors=@($errors);collisions=$collisions}
}

function Get-WdStatusToolsConversationRuntime {
    param($Definition, $InstalledBundle, [DateTimeOffset] $Now, $Result)
    try {
        $Result.reason='conversation_readiness_v2_required'
        $record=Read-WdStatusRecord $Result.readiness_path
        if ([string]$record.schema -cne 'wd.tools-consumer-ready.v2') { return $Result }
        $Result.reason='conversation_readiness_invalid'
        $created=ConvertTo-WdStatusUtc $record.process_start_utc
        $nativeCreated=ConvertTo-WdStatusUtc $record.native_process_start_utc
        $readyAt=ConvertTo-WdStatusUtc $record.ready_at_utc
        $transportAt=ConvertTo-WdStatusUtc $record.transport_ready_at_utc
        if ([string]$record.status -cne 'transport_ready' -or [string]$record.readiness_scope -cne 'ui_transport_only' -or
            [string]$record.conversation_surface -cne 'local_window' -or $record.transport_ready -isnot [bool] -or -not $record.transport_ready -or
            $record.task_completion_verified -isnot [bool] -or $record.task_completion_verified -or
            [string]$record.generation -cnotmatch '^[0-9a-f]{40}$' -or
            [string]$record.model -cne 'gpt-5.6-terra' -or [string]$record.reasoning_effort -cne 'high' -or
            [string]$record.session_id -cnotmatch '^[A-Za-z0-9._-]{1,128}$' -or [string]$record.run_id -cne [string]$record.session_id -or
            [string]$record.thread_id -cnotmatch '^[A-Za-z0-9._:-]{1,256}$' -or
            -not ([string]$record.worktree).Equals([string]$Definition.worktree,[StringComparison]::OrdinalIgnoreCase) -or
            $nativeCreated -lt $created -or $readyAt -lt $nativeCreated -or $transportAt -lt $nativeCreated -or
            $readyAt -gt $Now.AddSeconds(5) -or $transportAt -gt $Now.AddSeconds(5)) { return $Result }
        foreach ($name in @('pid','native_pid','native_parent_pid')) {
            $value=Get-WdStatusProperty $record $name
            if ([string]$value -cnotmatch '^[1-9][0-9]{0,9}$' -or [int64]$value -gt [int]::MaxValue) { return $Result }
        }
        if ([int]$record.native_parent_pid -ne [int]$record.pid -or [int]$record.native_pid -eq [int]$record.pid) { return $Result }
        $Result.reason='process_query_unavailable'
        $wrapper=@(Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId={0}" -f $record.pid) -ErrorAction Stop)
        $native=@(Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId={0}" -f $record.native_pid) -ErrorAction Stop)
        $Result.reason='conversation_process_identity_unproved'
        if ($wrapper.Count -ne 1 -or $native.Count -ne 1) { return $Result }
        $wrapper=$wrapper[0]; $native=$native[0]
        $wrapperStart=ConvertTo-WdStatusUtc $wrapper.CreationDate
        $nativeStart=ConvertTo-WdStatusUtc $native.CreationDate
        if ([int]$wrapper.ProcessId -ne [int]$record.pid -or [string]$wrapper.Name -notmatch '^(powershell|pwsh)\.exe$' -or
            [int]$native.ProcessId -ne [int]$record.native_pid -or [int]$native.ParentProcessId -ne [int]$record.pid -or
            [string]$native.Name -notmatch '^codex\.exe(?:\.old\.\d+)?$' -or
            [Math]::Abs(($wrapperStart-$created).TotalSeconds) -gt 1 -or [Math]::Abs(($nativeStart-$nativeCreated).TotalSeconds) -gt 1 -or
            -not [IO.Path]::IsPathRooted([string]$record.codex_command) -or
            -not ([IO.Path]::GetFullPath([string]$native.ExecutablePath)).Equals([IO.Path]::GetFullPath([string]$record.codex_command),[StringComparison]::OrdinalIgnoreCase) -or
            [string]$native.CommandLine -cnotmatch '(?:^|\s)(?:"app-server"|app-server)\s+(?:"--listen"|--listen)\s+(?:"stdio://"|stdio://)(?=\s|$)') { return $Result }
        $files=[regex]::Matches([string]$wrapper.CommandLine,'(?i)(?:^|\s)-File\s+(?:"(?<v>[^"]+)"|''(?<v>[^'']+)''|(?<v>\S+))(?=\s|$)')
        $generations=[regex]::Matches([string]$wrapper.CommandLine,'(?i)(?:^|\s)-Generation\s+(?:"(?<v>[0-9a-f]{40})"|''(?<v>[0-9a-f]{40})''|(?<v>[0-9a-f]{40}))(?=\s|$)')
        if ($files.Count -ne 1 -or $generations.Count -ne 1 -or $generations[0].Groups['v'].Value -cne [string]$record.generation) { return $Result }
        $Result.reason='installed_generation_unknown'
        if ($InstalledBundle.status -cne 'recorded' -or -not $InstalledBundle.matches_selected_manifest -or
            [string]$record.generation -cne [string]$InstalledBundle.source_commit) { return $Result }
        $launcher=[IO.Path]::GetFullPath($files[0].Groups['v'].Value)
        $allowedLaunchers=@((Join-Path $InstalledBundle.active_bundle 'start-wd-tools-consumer.ps1'))
        if ($Definition.launcher_script) { $allowedLaunchers += [string]$Definition.launcher_script }
        $Result.reason='conversation_wrapper_path_unproved'
        if (-not @($allowedLaunchers | Where-Object { $launcher.Equals([IO.Path]::GetFullPath($_),[StringComparison]::OrdinalIgnoreCase) }).Count) { return $Result }
        # Publish positive transport observations only after both process identities
        # bind. This is not a GUI-interaction, checkpoint, or useful-progress proof.
        $Result.identity='matched'; $Result.reason='wrapper_and_native_transport_identity_match'
        $Result.readiness_status='transport_ready'; $Result.readiness_scope='ui_transport_only'
        $Result.readiness_age_seconds=[Math]::Max(0,[int64][Math]::Floor(($Now-$transportAt).TotalSeconds))
        $Result.recorded_pid=[int]$record.pid; $Result.observed_pid=[int]$wrapper.ProcessId
        $Result.recorded_process_start_utc=$created.ToString('o'); $Result.observed_process_start_utc=$wrapperStart.ToString('o')
        $Result.recorded_generation=[string]$record.generation; $Result.observed_generation=[string]$record.generation
        $Result.observed_native_pid=[int]$native.ProcessId; $Result.observed_native_process_start_utc=$nativeStart.ToString('o')
        $Result.thread_id=[string]$record.thread_id; $Result.transport_ready_verified=$true
        try {
            if ($record.native_checkpoint_verified -isnot [bool]) { throw 'checkpoint flag is not boolean' }
            $checkpoint=[ordered]@{source_domain='tools_readiness_checkpoint_record';status='recorded';latest_final_recorded_verified=[bool]$record.native_checkpoint_verified}
            foreach ($name in @('last_turn_id','last_native_turn_id','last_native_status','last_turn_disposition','last_turn_finalized_at_utc',
                    'last_checkpoint_turn_id','last_checkpoint_native_turn_id','last_checkpoint_disposition','last_checkpoint_verified_at_utc')) {
                $value=[string](Get-WdStatusProperty $record $name)
                if ($value.Length -gt 256) { throw 'checkpoint field oversized' }
                $checkpoint[$name]=$value
            }
            foreach ($prefix in @('last_turn','last_checkpoint')) {
                $idName=if($prefix -ceq 'last_turn'){'last_turn_id'}else{'last_checkpoint_turn_id'}
                $id=$checkpoint[$idName]; $timeName=if($prefix -ceq 'last_turn'){'last_turn_finalized_at_utc'}else{'last_checkpoint_verified_at_utc'}
                if ($id -or $checkpoint[$timeName]) {
                    if ($id -cnotmatch '^turn-[0-9a-f]{32}$') { throw 'checkpoint turn id invalid' }
                    $stamp=ConvertTo-WdStatusUtc $checkpoint[$timeName]
                    if ($stamp -lt $nativeCreated -or $stamp -gt $Now.AddSeconds(5)) { throw 'checkpoint time invalid' }
                }
            }
            if ($checkpoint.latest_final_recorded_verified -and (-not $checkpoint.last_turn_id -or
                $checkpoint.last_native_status -cne 'completed' -or $checkpoint.last_turn_disposition -cnotin @('completed','idle','blocked') -or
                $checkpoint.last_checkpoint_turn_id -cne $checkpoint.last_turn_id -or
                -not $checkpoint.last_native_turn_id -or
                $checkpoint.last_checkpoint_native_turn_id -cne $checkpoint.last_native_turn_id -or
                $checkpoint.last_checkpoint_disposition -cne $checkpoint.last_turn_disposition)) { throw 'latest checkpoint binding invalid' }
            $Result.native_checkpoint=[pscustomobject]$checkpoint
        } catch { $Result.native_checkpoint.status='invalid_record' }
    } catch { <# No positive identity is published before all transport checks pass. #> }
    return $Result
}

function Get-WdStatusRuntime {
    param($Definition, $InstalledBundle, [DateTimeOffset] $Now)
    $result = [pscustomobject]@{
        source_domain = 'process_query_and_readiness_record'
        identity = 'unknown'
        reason = 'no_readiness_source'
        readiness_path = [string]$Definition.readiness_path
        readiness_status = 'unknown'
        readiness_age_seconds = $null
        recorded_pid = $null
        recorded_process_start_utc = $null
        recorded_generation = $null
        observed_pid = $null
        observed_process_start_utc = $null
        observed_generation = $null
        readiness_scope = 'unknown'
        transport_ready_verified = $false
        observed_native_pid = $null
        observed_native_process_start_utc = $null
        thread_id = $null
        native_checkpoint = [pscustomobject]@{
            source_domain='tools_readiness_checkpoint_record';status='not_observed';latest_final_recorded_verified=$false
        }
    }
    if (-not $result.readiness_path) { return $result }
    if ($Definition.configured_conversation_surface -ceq 'unknown') {
        $result.reason='configured_conversation_surface_unknown'
        return $result
    }
    if ($Definition.configured_conversation_surface -ceq 'local_window') {
        return Get-WdStatusToolsConversationRuntime -Definition $Definition -InstalledBundle $InstalledBundle -Now $Now -Result $result
    }
    try {
        $result.reason = 'readiness_missing_or_invalid'
        $nativeTerminal = $Definition.configured_conversation_surface -ceq 'native_terminal'
        $record = Read-WdStatusRecord -Path $result.readiness_path
        $created = [DateTimeOffset]::Parse([string]$record.process_start_utc,
            [Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
        $readyAt = [DateTimeOffset]::Parse([string]$record.ready_at_utc,
            [Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
        if (
            [string]$record.schema -cne $(if ($nativeTerminal) { 'wd.tools-consumer-ready.v3' } else { 'wd.tools-consumer-ready.v1' }) -or
            ($nativeTerminal -and $record.status -cne 'terminal_ready') -or
            (-not $nativeTerminal -and [string]$record.status -cnotin @('ready', 'degraded')) -or
            [string]$record.generation -cnotmatch '^[0-9a-f]{40}$' -or
            [string]$record.pid -cnotmatch '^[1-9][0-9]{0,9}$' -or
            [int64]$record.pid -gt [int]::MaxValue -or
            -not ([string]$record.worktree).Equals(
                [string]$Definition.worktree, [StringComparison]::OrdinalIgnoreCase) -or
            $readyAt -lt $created -or $readyAt -gt $Now.AddSeconds(5)
        ) { return $result }
        $result.readiness_status = [string]$record.status
        $result.readiness_age_seconds = [Math]::Max(0,
            [int64][Math]::Floor(($Now - $readyAt).TotalSeconds))
        $result.recorded_pid = [int]$record.pid
        $result.recorded_process_start_utc = $created.ToString('o')
        $result.recorded_generation = [string]$record.generation
        $result.reason = 'process_query_unavailable'
        $processes = @(if ($nativeTerminal) {
            @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop | Where-Object { [int]$_.ProcessId -eq $result.recorded_pid })
        } else { @(Get-CimInstance -ClassName Win32_Process `
            -Filter ("ProcessId={0}" -f $result.recorded_pid) -ErrorAction Stop) })
        if ($processes.Count -eq 0) {
            $result.identity = 'absent'
            $result.reason = 'recorded_pid_absent'
            return $result
        }
        if ($processes.Count -ne 1) { return $result }
        $process = $processes[0]
        $result.observed_pid = [int]$process.ProcessId
        $observedStart = [DateTimeOffset]([datetime]$process.CreationDate).ToUniversalTime()
        $result.observed_process_start_utc = $observedStart.ToString('o')
        if ($result.observed_pid -ne $result.recorded_pid -or
            [Math]::Abs(($observedStart - $created).TotalSeconds) -gt 1) {
            $result.identity = 'mismatch'
            $result.reason = 'process_identity_mismatch'
            return $result
        }
        # Observe the ordinary launcher argument without executing or disclosing
        # the process command line. Missing/ambiguous generation stays unknown.
        $pattern = '(?i)(?:^|\s)-Generation\s+(?:"([0-9a-f]{40})"|''([0-9a-f]{40})''|([0-9a-f]{40}))(?=\s|$)'
        $generationArguments = [regex]::Matches([string]$process.CommandLine, $pattern)
        $result.reason = 'process_generation_unknown'
        if ($generationArguments.Count -ne 1) { return $result }
        $result.observed_generation = (@(1..3 | ForEach-Object {
            $generationArguments[0].Groups[$_].Value
        } | Where-Object { $_ }) -join '').ToLowerInvariant()
        if ($result.observed_generation -cne $result.recorded_generation) {
            $result.identity = 'mismatch'
            $result.reason = 'readiness_generation_mismatch'
            return $result
        }
        $result.reason = 'installed_generation_unknown'
        if ($InstalledBundle.status -cne 'recorded' -or
            -not $InstalledBundle.matches_selected_manifest) { return $result }
        if ($result.observed_generation -cne $InstalledBundle.source_commit) {
            $result.identity = 'mismatch'
            $result.reason = 'installed_generation_mismatch'
            return $result
        }
        if ($nativeTerminal) {
            $result.reason='native_terminal_identity_unknown'
            if ($record.readiness_scope -cne 'native_cli_only' -or $record.conversation_surface -cne 'native_terminal' -or
                [string]$record.thread_id -cnotmatch '^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$' -or
                $record.task_completion_verified -isnot [bool] -or $record.task_completion_verified) { return $result }
            $native=@(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop | Where-Object { [int]$_.ProcessId -eq [int]$record.native_pid })
            if ($native.Count -ne 1 -or $native[0].Name -ine 'codex.exe' -or
                [int]$native[0].ParentProcessId -ne $result.observed_pid -or
                [int]$record.native_parent_pid -ne $result.observed_pid -or
                -not ([string]$native[0].ExecutablePath).Equals([string]$record.codex_command,[StringComparison]::OrdinalIgnoreCase)) { return $result }
            $nativeAt=ConvertTo-WdStatusUtc $native[0].CreationDate
            $recordedNativeAt=ConvertTo-WdStatusUtc $record.native_process_start_utc
            if ($nativeAt -lt $created -or $nativeAt -gt $readyAt -or [Math]::Abs(($nativeAt-$recordedNativeAt).TotalSeconds) -gt 1) { return $result }
            $result.observed_native_pid=[int]$native[0].ProcessId
            $result.observed_native_process_start_utc=$nativeAt.ToString('o')
            $result.thread_id=[string]$record.thread_id; $result.readiness_scope='native_cli_only'
        }
        $result.identity = 'matched'
        $result.reason = if ($nativeTerminal) { 'native_terminal_identity_match_progress_not_verified' } else { 'pid_start_and_generation_match' }
    }
    catch {
        # Keep the failed evidence stage visible, never infer a live process.
        $result.identity = 'unknown'
    }
    return $result
}

$manifestFull = Resolve-WdStatusManifest -Requested $ManifestPath
if (-not (Test-Path -LiteralPath $manifestFull -PathType Leaf)) {
    throw "fleet manifest is missing: $manifestFull"
}
$manifest = Get-Content -LiteralPath $manifestFull -Raw |
    ConvertFrom-Json -ErrorAction Stop
if ([int]$manifest.schema_version -ne 2) {
    throw "unsupported fleet manifest schema: $($manifest.schema_version)"
}
$git = [string]$manifest.git_executable
if (-not (Test-Path -LiteralPath $git -PathType Leaf)) {
    throw "fleet Git executable is missing: $git"
}

$definitions = [Collections.Generic.List[object]]::new()
foreach ($lane in @($manifest.lanes)) {
    $modeProperty = $lane.PSObject.Properties['turn_mode']
    $configuredMode = if ($null -eq $modeProperty) { 'interactive' } else { [string]$modeProperty.Value }
    if ($configuredMode -cnotin @('interactive','managed')) { $configuredMode = 'unknown' }
    $surfaceProperty = $lane.PSObject.Properties['conversation_surface']
    $configuredSurface = if ($null -eq $surfaceProperty) { 'none' } else { [string]$surfaceProperty.Value }
    if ($configuredSurface -cnotin @('none','local_window') -or
        ($configuredSurface -ceq 'local_window' -and
         ([string]$lane.agent -cne 'codex-lead-1' -or $configuredMode -cne 'managed'))) {
        $configuredSurface = 'unknown'
    }
    $configuredPosture = if ($configuredSurface -ceq 'unknown') { 'unknown' } else { 'not_applicable' }
    if ($configuredSurface -ceq 'local_window') {
        $permissionsProperty = $lane.PSObject.Properties['conversation_permissions']
        $configuredPosture = 'workspace_write'
        if ($null -ne $permissionsProperty) {
            if ($permissionsProperty.Value -isnot [pscustomobject]) { $configuredPosture='unknown' }
            else {
                $postureProperty = $permissionsProperty.Value.PSObject.Properties['posture']
                if ($null -ne $postureProperty) {
                    $configuredPosture = if ($postureProperty.Value -is [string] -and
                        $postureProperty.Value -cin @('workspace_write','existing_interactive')) { [string]$postureProperty.Value } else { 'unknown' }
                }
            }
        }
    }
    $definitions.Add([pscustomobject]@{
        agent = [string]$lane.agent
        worktree = [IO.Path]::GetFullPath([string]$lane.worktree)
        readiness_path = ''
        configured_turn_mode = $configuredMode
        configured_conversation_surface = $configuredSurface
        configured_permission_posture = $configuredPosture
        legacy_process_markers = @(Get-WdStatusProperty $lane 'legacy_process_markers')
    })
}
$toolsSurfaceProperty = $manifest.tools_supervisor.PSObject.Properties['conversation_surface']
$toolsSurface = if ($null -eq $toolsSurfaceProperty) { 'none' } else { [string]$toolsSurfaceProperty.Value }
if ($toolsSurface -cnotin @('none','local_window','native_terminal') -or
    ($toolsSurface -ceq 'local_window' -and ([string]$manifest.tools_supervisor.agent -cne 'codex-tools-1' -or
        [string](Get-WdStatusProperty $manifest.tools_supervisor 'model') -cne 'gpt-5.6-terra' -or
        [string](Get-WdStatusProperty $manifest.tools_supervisor 'reasoning_effort') -cne 'high'))) { $toolsSurface='unknown' }
$definitions.Add([pscustomobject]@{
    agent = [string]$manifest.tools_supervisor.agent
    worktree = [IO.Path]::GetFullPath(
        [string]$manifest.tools_supervisor.worktree
    )
    readiness_path = [string](Get-WdStatusProperty $manifest.tools_supervisor 'readiness_path')
    configured_turn_mode = 'tools_consumer'
    configured_conversation_surface = $toolsSurface
    configured_permission_posture = if ($toolsSurface -cin @('local_window','native_terminal')) { 'workspace_write' } elseif ($toolsSurface -ceq 'none') { 'not_applicable' } else { 'unknown' }
    launcher_script = [string](Get-WdStatusProperty $manifest.tools_supervisor 'launcher_script')
    legacy_process_markers = @()
})
if (@($definitions).Count -ne 5) {
    throw 'parallel status requires exactly five unique lane definitions'
}
if (@($definitions.agent | Select-Object -Unique).Count -ne 5) {
    throw 'parallel status lane identities are not unique'
}

$runtimeRoot = [IO.Path]::GetFullPath([string]$manifest.runtime_root)
$now = [DateTimeOffset]::UtcNow
$helperBin=Join-Path $PSScriptRoot 'tools-bootstrap/.agent-bridge/bin'
if (-not (Test-Path -LiteralPath $helperBin)) { $helperBin=Join-Path (Split-Path -Parent $manifestFull) 'tools-bootstrap/.agent-bridge/bin' }
# Machine copies can live directly below the drive root. Prefer the selected
# bundle before the source-tree fallback; normalize '..' before provider access.
if (-not (Test-Path -LiteralPath $helperBin)) { $helperBin=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../.agent-bridge/bin')) }
. (Join-Path $helperBin 'BridgeResourceScope.ps1')
. (Join-Path $helperBin 'BridgeIncrementalReader.ps1')
. (Join-Path $helperBin 'BridgeEventClassifier.ps1')
$claimObservation=Get-WdStatusClaims -Root $runtimeRoot -Now $now
$recentProgress=@{}
$progressRead=Read-BridgeEventTail -Path (Join-Path $runtimeRoot 'shared/events.jsonl') -MaxLines 1200
if ($progressRead.status -cin @('OK','IDLE')) {
    foreach ($event in @($progressRead.rows)) {
        if ((Test-BridgeAnswerEvent $event) -and (Get-WdStatusProperty $event 'in_reply_to_request_id')) {
            $recentProgress[[string]$event.agent]=$event
        }
    }
}
$installedBundle = [pscustomobject]@{
    source_domain = 'installed_pointer'
    source_path = $CurrentStatePath
    status = 'unknown'
    source_commit = $null
    active_bundle = $null
    installed_at_utc = $null
    matches_selected_manifest = $false
}
try {
    $installed = Read-WdStatusRecord -Path $CurrentStatePath
    if ([int]$installed.schema_version -ne 1 -or
        [string]$installed.source_commit -cnotmatch '^[0-9a-f]{40}$' -or
        [string]::IsNullOrWhiteSpace([string]$installed.active_bundle) -or
        [string]::IsNullOrWhiteSpace([string]$installed.fleet_manifest)) {
        throw 'invalid installation pointer'
    }
    $installedBundle.source_commit = [string]$installed.source_commit
    $installedBundle.active_bundle = [IO.Path]::GetFullPath([string]$installed.active_bundle)
    $installedBundle.installed_at_utc = Get-WdStatusProperty $installed 'installed_at_utc'
    $installedBundle.matches_selected_manifest = ([IO.Path]::GetFullPath(
        [string]$installed.fleet_manifest)).Equals($manifestFull,
        [StringComparison]::OrdinalIgnoreCase)
    $installedBundle.status = 'recorded'
}
catch { $installedBundle.status = 'unknown' }
$supervisor = [pscustomobject]@{
    source_domain = 'scheduled_task_query'
    task_name = [string](Get-WdStatusProperty $manifest.tools_supervisor 'task_name')
    status = 'unknown'
    observed_state = $null
}
try {
    if ($supervisor.task_name) {
        $task = @(Get-ScheduledTask -TaskName $supervisor.task_name -ErrorAction Stop)
        if ($task.Count -eq 1) {
            $supervisor.observed_state = [string]$task[0].State
            $supervisor.status = switch ($supervisor.observed_state) {
                'Disabled' { 'disabled' }
                'Ready' { 'enabled' }
                'Running' { 'enabled' }
                default { 'unknown' }
            }
        }
    }
}
catch { $supervisor.status = 'unknown' }
$lanes = [Collections.Generic.List[object]]::new()
$laneProcessQueryAvailable = $false
$laneProcesses = @()
try {
    $laneProcesses = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    $laneProcessQueryAvailable = $true
} catch { <# Runtime observation remains unknown. #> }
foreach ($definition in @($definitions)) {
    $agent = [string]$definition.agent
    $worktree = [string]$definition.worktree
    $statePath = Join-Path $worktree '.codex-audit\wd-current-state.json'
    $stateHealth = 'missing'
    $state = $null
    $ageSeconds = $null
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            $state = Read-WdStatusRecord -Path $statePath
            $updated = [DateTimeOffset]::Parse(
                [string]$state.updated_at_utc,
                [Globalization.CultureInfo]::InvariantCulture
            ).ToUniversalTime()
            if ($updated -gt $now.AddSeconds(5)) { throw 'checkpoint is future dated' }
            $ageSeconds = [Math]::Max(
                0,
                [int64][Math]::Floor(($now - $updated).TotalSeconds)
            )
            if (
                [string]$state.schema -cne 'wd.lane-current.v1' -or
                [string]$state.agent -cne $agent -or
                -not ([string]$state.worktree).Equals(
                    $worktree,
                    [StringComparison]::OrdinalIgnoreCase
                ) -or
                [string]$state.status -cnotmatch '^[a-z][a-z0-9_-]{0,63}$' -or
                [string]$state.head -cnotmatch '^[0-9a-f]{40}$' -or
                $null -eq $state.PSObject.Properties['write_scope'] -or
                [string]::IsNullOrWhiteSpace([string]$state.task_id) -or
                [string]::IsNullOrWhiteSpace([string]$state.next_action)
            ) {
                throw 'state identity mismatch'
            }
            $stateHealth = if ($ageSeconds -gt $StaleAfterSeconds) {
                'stale'
            } else { 'current' }
        }
        catch {
            $stateHealth = 'invalid'
            $state = $null
            $ageSeconds = $null
        }
    }

    $branch = Get-WdStatusGitText -Git $git -Worktree $worktree -Arguments @(
        'branch', '--show-current'
    )
    $head = Get-WdStatusGitText -Git $git -Worktree $worktree -Arguments @(
        'rev-parse', 'HEAD'
    )
    $scope = if ($null -eq $state) { @() } else {
        @($state.write_scope | ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ })
    }
    $status = if ($null -eq $state) { '' } else { [string]$state.status }
    $nextAction = if ($null -eq $state) { '' } else {
        [string]$state.next_action
    }
    $runtime = Get-WdStatusRuntime -Definition $definition `
        -InstalledBundle $installedBundle -Now $now
    $headMatches = ($null -ne $state -and $head -and
        [string](Get-WdStatusProperty $state 'head') -ceq $head)
    $blockers = @(Get-WdStatusProperty $state 'blockers' | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    })
    $runnableEvidence = 'unknown'
    if ($stateHealth -ceq 'current' -and
        ($status -cin @('blocked', 'waiting', 'paused', 'stopped', 'disabled',
            'complete', 'completed', 'done', 'idle') -or $blockers.Count -gt 0)) {
        $runnableEvidence = 'not_observed'
    }
    elseif ($definition.readiness_path -and $supervisor.status -ceq 'disabled') {
        $runnableEvidence = 'not_observed'
    }
    elseif ($runtime.identity -cin @('absent', 'mismatch') -or
        $runtime.readiness_status -ceq 'degraded') {
        $runnableEvidence = 'not_observed'
    }
    elseif ($stateHealth -ceq 'current' -and $headMatches -and
        $status -cin @('ready', 'working', 'running', 'active', 'in_progress', 'in-progress') -and
        -not [string]::IsNullOrWhiteSpace($nextAction) -and
        $runtime.identity -ceq 'matched' -and
        $runtime.readiness_status -ceq 'ready' -and $supervisor.status -ceq 'enabled') {
        $runnableEvidence = 'observed'
    }
    $turnExecution = Get-WdStatusTurnExecution -Definition $definition `
        -Processes $laneProcesses -QueryAvailable $laneProcessQueryAvailable `
        -HandshakeRoot ([string](Get-WdStatusProperty $manifest 'handshake_root')) `
        -RuntimeRoot $runtimeRoot -Now $now
    $wakeObservation = Get-WdStatusWakeObservation -Root $runtimeRoot -Agent $agent `
        -Worktree $worktree -Processes $laneProcesses -MonitorFile (Join-Path $helperBin 'Monitor-AgentBridge.ps1')
    $lanes.Add([pscustomobject]@{
        agent = $agent
        configured_turn_mode = $definition.configured_turn_mode
        configured_conversation_surface = $definition.configured_conversation_surface
        configured_permission_posture = $definition.configured_permission_posture
        # A manifest/handshake does not prove the GUI is open or a RPC was accepted.
        conversation_control_verified = $false
        turn_execution = $turnExecution
        health_observation = [pscustomobject]@{
            process_presence = $(if ($runtime.identity -ceq 'matched' -or $turnExecution.observed_pid) { 'observed' } else { 'unknown' })
            identity_scope = $(if ($runtime.identity -ceq 'matched') { 'readiness_process_identity' } elseif ($turnExecution.external_wake_support -ceq 'native_queue_bridge') { 'native_lead_and_relay_identity' } elseif ($turnExecution.observed_pid) { 'launcher_handshake_only' } else { 'unknown' })
            wake_transport = $turnExecution.external_wake_support
            relay_status = $turnExecution.relay_status
            relay_error = $turnExecution.relay_error
            inbox_delivery = $wakeObservation.status
            last_answer_at_utc = $(if ($recentProgress.ContainsKey($agent)) { $recentProgress[$agent].ts_utc } else { $null })
            task_completion_verified = $false
        }
        worktree = $worktree
        state_health = $stateHealth
        age_seconds = $ageSeconds
        task_id = if ($null -eq $state) { '' } else { [string]$state.task_id }
        status = $status
        branch = $branch
        head = $head
        checkpoint_head = if ($null -eq $state) { '' } else {
            [string]$state.head
        }
        head_matches = (
            $null -ne $state -and $head -and
            [string]$state.head -ceq $head
        )
        write_scope = @($scope)
        next_action = $nextAction
        recorded_next_action = $nextAction
        runnable_evidence = $runnableEvidence
        checkpoint = [pscustomobject]@{
            source_domain = 'lane_checkpoint'
            source_path = $statePath
            freshness = $stateHealth
            updated_at_utc = Get-WdStatusProperty $state 'updated_at_utc'
            age_seconds = $ageSeconds
        }
        checkout_source_domain = 'manifest_worktree_git_query'
        runtime = $runtime
        progress = [pscustomobject]@{
            status = $(if ($recentProgress.ContainsKey($agent)) {'canonical_answer_observed'} else {'unknown'})
            source_domain = 'bounded_canonical_tail'
            last_substantive_progress_at_utc = $(if ($recentProgress.ContainsKey($agent)) {$recentProgress[$agent].ts_utc} else {$null})
            request_id = $(if ($recentProgress.ContainsKey($agent)) {$recentProgress[$agent].in_reply_to_request_id} else {$null})
            task_completion_verified = $false
            reader_status = $progressRead.status
            reader_reason = $progressRead.reason
            wait_age_seconds = $null
        }
        runnable = (
            $null -ne $state -and
            $status -cne 'blocked' -and
            -not [string]::IsNullOrWhiteSpace($nextAction)
        )
        sentinel_present = $wakeObservation.sentinel_present
        wake_pending = $wakeObservation.pending
        wake_observation = $wakeObservation
    })
}

$collisions = @($claimObservation.collisions)
$report = [pscustomobject]@{
    schema = 'wd.swarm-parallel-status.v1'
    observed_at_utc = $now.ToString('o')
    manifest = $manifestFull
    stale_after_seconds = $StaleAfterSeconds
    installed_bundle = $installedBundle
    supervisor = $supervisor
    claim_observation = $claimObservation
    semantics = [pscustomobject]@{
        runnable = 'legacy recorded next action; no freshness or runtime guarantee'
        runnable_evidence = 'observed requires current matching checkpoint with recognized active status, no recorded blocker, enabled supervisor and matching ready PID/start/generation; not authority or full runtime attestation'
        installed_bundle = 'installation pointer record, not proof of running code or package integrity'
        configured_turn_mode = 'selected manifest startup setting (missing legacy field defaults interactive), never live-mode evidence'
        configured_conversation_surface = 'next-start UI setting only; not live window, native RPC, shared context or task-completion proof'
        configured_permission_posture = 'configuration-only posture projection, not live permissions or validation of the full launch policy; native interactive peers are not_applicable'
        tools_conversation_runtime = 'v2 transport_ready binds wrapper/native PID, creation, generation and recorded thread only; not useful progress, accepted interaction or full package attestation; v1 remains valid only in none mode'
        native_checkpoint = 'bounded producer-reported latest terminal and previous verified checkpoint facts, kept separate; this status command does not reverify receipts or infer useful progress'
        turn_execution = 'known launcher and bounded PID/time/session-bound handshake observation; legacy missing mode means legacy interactive; neither bootstrap nor process existence proves a model turn started or completed'
        progress = 'latest substantive answer in a bounded canonical tail, separately from checkpoint age; binding, result correctness and task completion are not inferred; absent observation is unknown'
        wake_pending = 'nullable: eligible unread event after a scoped agent inbox cursor; false only through observed EOF; null for missing/legacy/invalid/incomplete observations. Delivery is not task completion. Sentinel presence is separate.'
        health_observation = 'projection of independent process, identity, wake and answer observations; launcher identity is not native conversation attestation, inbox delivery is not model processing'
    }
    lanes = @($lanes)
    summary = [pscustomobject]@{
        total_lanes = @($lanes).Count
        current_checkpoints = @($lanes | Where-Object {
                $_.state_health -ceq 'current'
            }).Count
        runnable_lanes = @($lanes | Where-Object { $_.runnable }).Count
        fresh_runnable_evidence_lanes = @($lanes | Where-Object {
                $_.runnable_evidence -ceq 'observed'
            }).Count
        unknown_runnable_evidence_lanes = @($lanes | Where-Object {
                $_.runnable_evidence -ceq 'unknown'
            }).Count
        matched_runtime_identities = @($lanes | Where-Object {
                $_.runtime.identity -ceq 'matched'
            }).Count
        blocked_lanes = @($lanes | Where-Object {
                $_.status -ceq 'blocked'
            }).Count
        blocked_wake_transports = @($lanes | Where-Object { $_.turn_execution.relay_status -ceq 'bridge_wake_blocked' }).Count
        pending_wakes = @($lanes | Where-Object { $_.wake_pending }).Count
        unknown_wake_observations = @($lanes | Where-Object { $null -eq $_.wake_pending }).Count
        wake_sentinels_present = @($lanes | Where-Object { $_.sentinel_present }).Count
        scope_collisions = $collisions.Count
    }
    scope_collisions = @($collisions)
}

if ($Json) {
    $report | ConvertTo-Json -Depth 8
} else {
    $report.lanes | Format-Table `
        agent, state_health, age_seconds, status, task_id, head_matches,
        runnable_evidence, @{Name='inbox_delivery';Expression={$_.wake_observation.status}},
        sentinel_present -AutoSize
    $report.summary | Format-List
    $report.lanes | Select-Object agent,
        @{Name='process_presence';Expression={$_.health_observation.process_presence}},
        @{Name='identity_scope';Expression={$_.health_observation.identity_scope}},
        @{Name='last_answer_utc';Expression={$_.health_observation.last_answer_at_utc}},
        @{Name='inbox_reason';Expression={$_.wake_observation.reason}} | Format-Table -AutoSize -Wrap
    $report.lanes | Select-Object agent, configured_turn_mode, configured_conversation_surface,
        @{Name='cfg_posture';Expression={$_.configured_permission_posture}},
        @{Name='observed_turn_mode';Expression={$_.turn_execution.observed_turn_mode}},
        @{Name='external_wake_support';Expression={$_.turn_execution.external_wake_support}},
        @{Name='observation_reason';Expression={$_.turn_execution.reason}} | Format-Table -AutoSize -Wrap
    $report.lanes | Where-Object { $_.configured_conversation_surface -ceq 'local_window' } |
        Select-Object agent, @{Name='transport_identity';Expression={$_.runtime.identity}},
        @{Name='readiness_scope';Expression={$_.runtime.readiness_scope}},
        @{Name='native_pid';Expression={$_.runtime.observed_native_pid}},
        @{Name='latest_final_checkpoint_recorded';Expression={$_.runtime.native_checkpoint.latest_final_recorded_verified}} |
        Format-Table -AutoSize -Wrap
    if ($collisions.Count -gt 0) {
        $collisions | Format-Table write_scope, agents -AutoSize
    }
}

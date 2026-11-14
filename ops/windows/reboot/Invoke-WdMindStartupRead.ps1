#requires -Version 5.1

function Invoke-WdMindStartupRead {
    <# One optional, bounded status read through the manifest-pinned session CLI.
       This helper defines no writer/producer route and never starts an agent.
       Success means a locally validated status projection, not Mind adoption,
       authenticated origin, a gate result, or proof of current swarm state. #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] [string] $ExpectedManifestHash,
        [Parameter(Mandatory)] [string] $Lane,
        [string] $Worktree = '',
        [ValidateRange(1, 90)] [int] $TimeoutSeconds = 15
    )
    $result = [ordered]@{schema='wd.mind-startup-status.v1';outcome='refused';reason='package_refused';
        authority='none';view_json='';view_sha256='';cleanup='not_started';record_written=$false}
    $process = $null
    function Read-WdMindPinnedSnapshot {
        param([string] $Path, [string] $Pin, [int] $Limit=1048576)
        if ($Pin -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or -not [IO.Path]::IsPathRooted($Path)) { throw 'pin' }
        $current = [IO.Path]::GetFullPath($Path)
        while ($current) {
            if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'path' }
            $parent = [IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }
            $current = $parent
        }
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            if ($stream.Length -gt $Limit) { throw 'size' }
            $bytes = New-Object byte[] ([int]$stream.Length)
            $offset = 0
            while ($offset -lt $bytes.Length) {
                $n = $stream.Read($bytes, $offset, $bytes.Length-$offset)
                if ($n -le 0) { throw 'read' }
                $offset += $n
            }
            if ($stream.ReadByte() -ne -1) { throw 'changed' }
        } finally { $stream.Dispose() }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($digest -cne $Pin.ToLowerInvariant()) { throw 'pin' }
        $utf8 = New-Object Text.UTF8Encoding($false,$true)
        return [pscustomobject]@{Text=$utf8.GetString($bytes).TrimStart([char]0xFEFF);Hash=$digest}
    }
    function ConvertTo-WdMindLiteral([string] $Value) { return "'" + $Value.Replace("'","''") + "'" }
    # Closed shapes apply recursively: a known outer object is not permission to
    # forward arbitrary nested message/payload fields or free-form strings.
    function Assert-WdMindObject($Value, [string[]] $Required, [string[]] $Optional=@()) {
        if ($Value -isnot [pscustomobject]) { throw 'shape' }
        $names=@($Value.PSObject.Properties.Name)
        foreach ($name in $Required) { if ($names -cnotcontains $name) { throw 'shape' } }
        foreach ($name in $names) { if (($Required+$Optional) -cnotcontains $name) { throw 'shape' } }
    }
    function Assert-WdMindString($Value, [string] $Pattern) {
        if ($Value -isnot [string] -or $Value -cnotmatch $Pattern) { throw 'shape' }
    }
    function Assert-WdMindNumber($Value, [switch] $Integer) {
        if (($Value -isnot [int] -and $Value -isnot [long] -and
             ($Integer -or $Value -isnot [double])) -or $Value -lt 0 -or
            [double]::IsNaN([double]$Value) -or [double]::IsInfinity([double]$Value)) { throw 'shape' }
    }
    function Assert-WdMindDirectStatus($View, [string] $ExpectedLane, [string] $FleetHash, $Fleet) {
        # This is a separate observational schema; it cannot impersonate a
        # verified publication or carry free-form event bodies into prompts.
        Assert-WdMindObject $View @('schema','authority','scope','lane','fleet_sha256','coverage','generation','continuity','sender_authentication','as_of_utc','observation_status','facts')
        foreach ($name in @('schema','authority','scope','lane','fleet_sha256','coverage','generation','continuity','sender_authentication','as_of_utc','observation_status')) {
            if ($View.PSObject.Properties[$name].Value -isnot [string]) { throw 'shape' }
        }
        if ($View.schema -cne 'wd.mind-direct-status.v1' -or $View.authority -cne 'none' -or
            $View.scope -cne 'bounded_bridge_status_not_gate' -or $View.lane -cne $ExpectedLane -or
            $View.fleet_sha256 -cne $FleetHash -or $View.coverage -cne 'bounded_tail' -or
            $View.generation -cne 'UNKNOWN' -or $View.continuity -cne 'UNKNOWN' -or
            $View.sender_authentication -cne 'UNKNOWN' -or
            $View.facts -isnot [array] -or $View.facts.Count -gt 1) { throw 'shape' }
        Assert-WdMindString $View.fleet_sha256 '\A[0-9a-f]{64}\z'
        Assert-WdMindString $View.as_of_utc '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\z'
        $asOf=[DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse($View.as_of_utc, [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None, [ref]$asOf) -or $asOf.Offset -ne [TimeSpan]::Zero) { throw 'shape' }
        if (@('OBSERVED','UNKNOWN') -cnotcontains $View.observation_status -or
            ($View.facts.Count -eq 0 -and $View.observation_status -cne 'UNKNOWN')) { throw 'shape' }
        if ($Fleet -isnot [pscustomobject] -or $Fleet.lanes -isnot [array] -or
            $Fleet.tools_supervisor -isnot [pscustomobject]) { throw 'shape' }
        $knownLanes=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
        $roster=@($Fleet.lanes) + @($Fleet.tools_supervisor)
        $agents=@()
        foreach ($entry in $roster) {
            if ($entry -isnot [pscustomobject] -or $entry.agent -isnot [string] -or
                $knownLanes -cnotcontains $entry.agent -or $agents -ccontains $entry.agent) { throw 'shape' }
            $agents+=@($entry.agent)
        }
        if ($agents.Count -ne $knownLanes.Count -or $agents -cnotcontains $ExpectedLane) { throw 'shape' }
        foreach ($fact in $View.facts) {
            Assert-WdMindObject $fact @('agent','event_type','observed_utc','event_ref','age_seconds')
            foreach ($name in @('agent','event_type','observed_utc','event_ref')) {
                if ($fact.PSObject.Properties[$name].Value -isnot [string]) { throw 'shape' }
            }
            if ($fact.agent -cne $ExpectedLane -or $agents -cnotcontains $fact.agent -or
                @('heartbeat','liveness') -cnotcontains $fact.event_type) { throw 'shape' }
            Assert-WdMindString $fact.observed_utc '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?(?:Z|\+00:00)\z'
            $observed=[DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse($fact.observed_utc, [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::None, [ref]$observed) -or $observed.Offset -ne [TimeSpan]::Zero) { throw 'shape' }
            Assert-WdMindString $fact.event_ref '\A[0-9a-f]{64}\z'
            Assert-WdMindNumber $fact.age_seconds -Integer
            $calculatedAge=($asOf-$observed).TotalSeconds
            if ($calculatedAge -lt 0 -or [Math]::Abs($calculatedAge-[double]$fact.age_seconds) -gt 1 -or
                ($calculatedAge -le 900 -and $View.observation_status -cne 'OBSERVED') -or
                ($calculatedAge -gt 900 -and $View.observation_status -cne 'UNKNOWN')) { throw 'shape' }
        }
    }
    function Assert-WdMindNestedView($View) {
        $lanes=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
        $identity='\A(?:windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]{1,16}:[0-9a-f]{1,16})\z'
        $generation='\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\z'
        $time='\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00)\z'
        $unknownTime='\A(?:UNKNOWN|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00))\z'
        Assert-WdMindString $View.as_of_utc $time
        Assert-WdMindObject $View.binding @('file_identity','generation','origin') @('generation_status','rotation_continuity')
        Assert-WdMindString $View.binding.file_identity $identity
        if ($null -eq $View.binding.generation) {
            if ($View.binding.origin -cne 'UNKNOWN' -or $View.binding.generation_status -cne 'UNKNOWN' -or
                $View.binding.rotation_continuity -cne 'UNKNOWN') { throw 'shape' }
        } else {
            Assert-WdMindString $View.binding.generation $generation
            if (@('UNKNOWN','lane_reader_view_not_authenticated') -cnotcontains $View.binding.origin -or
                @($View.binding.PSObject.Properties).Count -ne 3) { throw 'shape' }
        }
        Assert-WdMindObject $View.pointer @('cycle_dir','cycle_generation') @('state_read_mode','state_head_origin','head_to_state_content')
        Assert-WdMindString $View.pointer.cycle_dir '\A[0-9]{8}T[0-9]{6}Z\z'
        Assert-WdMindNumber $View.pointer.cycle_generation -Integer
        if ($View.pointer.cycle_generation -lt 1) { throw 'shape' }
        if (@($View.pointer.PSObject.Properties).Count -ne 2) {
            if (@($View.pointer.PSObject.Properties).Count -ne 5 -or $View.pointer.state_read_mode -cne 'compact_head' -or
                $View.pointer.state_head_origin -cne 'UNVERIFIED' -or $View.pointer.head_to_state_content -cne 'UNVERIFIED' -or
                $View.binding.origin -cne 'UNKNOWN') { throw 'shape' }
        }
        Assert-WdMindObject $View.freshness @('age_seconds','max_age_seconds','status')
        Assert-WdMindObject $View.identity @('roster','bound_lanes')
        if (@('caller_declared','none') -cnotcontains $View.identity.roster -or
            $View.identity.bound_lanes -isnot [array]) { throw 'shape' }
        foreach ($boundLane in $View.identity.bound_lanes) { if ($lanes -cnotcontains $boundLane) { throw 'shape' } }
        Assert-WdMindObject $View.source @('log_identity','scope','generation','as_of_utc','max_event_utc',
            'event_count','unique_event_count','duplicate_count','window','blocked')
        if ($View.source.log_identity -cne ('capture-window:'+$View.binding.file_identity) -or
            $View.source.scope -cne 'bounded_window_of_bridge_log_not_whole_log' -or
            $View.source.generation -cne $View.binding.generation) { throw 'shape' }
        Assert-WdMindString $View.source.as_of_utc $time
        Assert-WdMindString $View.source.max_event_utc $unknownTime
        foreach ($name in @('event_count','unique_event_count','duplicate_count')) {
            Assert-WdMindNumber $View.source.PSObject.Properties[$name].Value -Integer
        }
        Assert-WdMindObject $View.source.window @('cursor_start','cursor_end','coverage')
        Assert-WdMindNumber $View.source.window.cursor_start -Integer
        Assert-WdMindNumber $View.source.window.cursor_end -Integer
        if ($View.source.window.coverage -cne 'complete_over_window_only') { throw 'shape' }
        if ($null -ne $View.source.blocked) {
            Assert-WdMindObject $View.source.blocked @('active','reason')
            $reasons=@('canonical_event_invalid','complete_coverage_required','cursor_discontinuity_rebuild_required',
                'file_identity_changed','generation_changed','guarded_word_refused','head_binding_conflict','log_truncated',
                'partial_record','provenance_envelope_required','provenance_hash_mismatch','source_cursor_count_invalid','source_cursor_required')
            if ($View.source.blocked.active -isnot [bool] -or -not $View.source.blocked.active -or
                $reasons -cnotcontains $View.source.blocked.reason) { throw 'shape' }
        }
        Assert-WdMindObject $View.lanes $lanes
        foreach ($laneName in $lanes) {
            $row=$View.lanes.PSObject.Properties[$laneName].Value
            if ($row.observed_in_window -is [bool] -and $row.observed_in_window) {
                Assert-WdMindObject $row @('observed_in_window','identity_verification','last_event_utc','observation_age_seconds','claims')
                if ($row.identity_verification -cne 'roster_bound' -or $row.claims -isnot [array]) { throw 'shape' }
                Assert-WdMindString $row.last_event_utc $time
                Assert-WdMindNumber $row.observation_age_seconds
                foreach ($claim in $row.claims) {
                    Assert-WdMindObject $claim @('task_id','lease_expires_utc','expired')
                    Assert-WdMindString $claim.task_id '\A[A-Za-z0-9][A-Za-z0-9._-]{0,120}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,120})?\z'
                    Assert-WdMindString $claim.lease_expires_utc $unknownTime
                    if ($claim.expired -isnot [bool] -and $claim.expired -cne 'UNKNOWN') { throw 'shape' }
                }
            } elseif ($row.observed_in_window -is [bool]) {
                Assert-WdMindObject $row @('observed_in_window','status')
                if (@('not_seen_in_window','unverified_identity_rows_only') -cnotcontains $row.status) { throw 'shape' }
            } else {
                Assert-WdMindObject $row @('observed_in_window','status','identity_verification')
                if ($row.observed_in_window -cne 'UNKNOWN' -or $row.status -cne 'roster_unbound' -or
                    $row.identity_verification -cne 'declared_not_authenticated') { throw 'shape' }
            }
        }
        if ($View.prs -isnot [pscustomobject]) { throw 'shape' }
        foreach ($property in $View.prs.PSObject.Properties) {
            Assert-WdMindString $property.Name '\A[0-9]+\z'
            $pr=$property.Value
            Assert-WdMindObject $pr @('pr','head','base','class','reported_ci_status','missing')
            Assert-WdMindNumber $pr.pr -Integer
            if ([string]$pr.pr -cne $property.Name) { throw 'shape' }
            Assert-WdMindString $pr.head '\A(?:UNKNOWN|[0-9a-f]{40})\z'
            Assert-WdMindString $pr.base '\A(?:UNKNOWN|[0-9a-f]{40})\z'
            if (@('UNKNOWN','a','b','on-allowlist') -cnotcontains $pr.class -or
                @('UNKNOWN','reported_green','reported_not_green') -cnotcontains $pr.reported_ci_status -or
                $pr.missing -isnot [array]) { throw 'shape' }
            foreach ($missing in $pr.missing) { if ($missing -cne 'gate_eligibility_UNKNOWN') { throw 'shape' } }
        }
    }
    function Assert-WdMindReceiptDirectory([string] $Path) {
        $current=$Path
        while ($current) {
            $attributes=[IO.File]::GetAttributes($current)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                ($attributes -band [IO.FileAttributes]::Directory) -eq 0) { throw 'receipt_path' }
            $parent=[IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }
            $current=$parent
        }
    }
    function Assert-WdMindReceiptTarget([string] $Path) {
        try { $attributes=[IO.File]::GetAttributes($Path) }
        catch [IO.FileNotFoundException] { return }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($attributes -band [IO.FileAttributes]::Directory) -ne 0) { throw 'receipt_target' }
    }
    function Get-WdMindReceiptAudit([string] $Path) {
        # Receipt paths are persistent fully qualified C: directories. Refuse
        # lexical aliases before normalization, including parent hops and ADS.
        if ($Path -cnotmatch '\A[Cc]:[\\/]' -or $Path.Substring(3) -match '[:*?"<>|]' -or
            @($Path.Substring(3).Split([char[]]'\/') | Where-Object {
                $_ -eq '.' -or $_ -eq '..' -or $_ -match '[. ]$'
            }).Count -ne 0) { throw 'receipt_path' }
        $directory=[IO.Path]::GetFullPath($Path)
        Assert-WdMindReceiptDirectory $directory
        return Join-Path $directory '.codex-audit'
    }
    try {
        if ($Worktree) { [void](Get-WdMindReceiptAudit $Worktree) }
        if (@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5') -cnotcontains $Lane) { throw 'lane' }
        $root = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
        $manifestPath = Join-Path $root 'deployment-manifest.json'
        $manifest = Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304
        $deployment = $manifest.Text | ConvertFrom-Json -ErrorAction Stop
        $paths = @('wd-fleet.json','Get-WdMindReadConfig.ps1','Invoke-WdBridgePython.ps1',
            'tools-bootstrap/tools/wd_bridge_mind_session.py')
        $snapshots = @{}
        foreach ($relative in $paths) {
            $pin = $deployment.files.PSObject.Properties[$relative]
            if ($null -eq $pin -or $pin.Value -isnot [string]) { throw 'pin' }
            $snapshots[$relative] = Read-WdMindPinnedSnapshot -Path (Join-Path $root $relative) -Pin $pin.Value
        }
        # Evaluate the very helper bytes whose digest was checked above, not a second file read.
        . ([scriptblock]::Create([string]$snapshots['Get-WdMindReadConfig.ps1'].Text))
        $fleetPath = Join-Path $root 'wd-fleet.json'
        $settings = Get-WdMindReadConfig -FleetPath $fleetPath -FleetSha256 $snapshots['wd-fleet.json'].Hash
        if ($null -eq $settings) {
            $result.outcome='off';$result.reason='not_configured'
        } else {
            if ([string]$settings.WD_MIND_READ_ENABLED -cne '1' -or
                [string]$settings.WD_MIND_FLEET -cne $fleetPath -or
                [string]$settings.WD_MIND_FLEET_SHA256 -cne $snapshots['wd-fleet.json'].Hash) { throw 'config' }
            $wrapper = Join-Path $root 'Invoke-WdBridgePython.ps1'
            $wrapperPin = $snapshots['Invoke-WdBridgePython.ps1'].Hash
            # Recheck the external manifest anchor before crossing into another process.
            [void](Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304)
            $command = '$ErrorActionPreference="Stop"; if((Get-FileHash -LiteralPath ' +
                (ConvertTo-WdMindLiteral $wrapper) + ' -Algorithm SHA256).Hash.ToLowerInvariant() -cne ' +
                (ConvertTo-WdMindLiteral $wrapperPin) + '){exit 2}; $global:LASTEXITCODE=0; & ' +
                (ConvertTo-WdMindLiteral $wrapper) + ' tools/wd_bridge_mind_session.py -VerifyPackage --latest ' +
                (ConvertTo-WdMindLiteral ([string]$settings.WD_MIND_LATEST)) + ' --audit-root ' +
                (ConvertTo-WdMindLiteral ([string]$settings.WD_MIND_AUDIT_ROOT)) + ' --fleet ' +
                (ConvertTo-WdMindLiteral $fleetPath) + ' --fleet-sha256 ' +
                (ConvertTo-WdMindLiteral $snapshots['wd-fleet.json'].Hash) + '; exit $LASTEXITCODE'
            $start = New-Object Diagnostics.ProcessStartInfo
            $shell = if ($PSVersionTable.PSEdition -ceq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
            $start.FileName = Join-Path $PSHOME $shell
            $start.Arguments = '-NoProfile -NonInteractive -EncodedCommand ' +
                [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
            $start.WorkingDirectory = if ($Worktree) { [IO.Path]::GetFullPath($Worktree) } else { $root }
            $start.UseShellExecute=$false;$start.CreateNoWindow=$true
            $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
            $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false,$true)
            $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false,$true)
            foreach ($name in @($start.EnvironmentVariables.Keys)) {
                if (([string]$name).StartsWith('WD_MIND_',[StringComparison]::OrdinalIgnoreCase)) {
                    $start.EnvironmentVariables.Remove([string]$name)
                }
            }
            # A read-only projection does not need the bridge writer's ownership secret.
            $start.EnvironmentVariables.Remove('AGENT_BRIDGE_OWNER_TOKEN')
            $start.EnvironmentVariables['WD_MIND_READ_ENABLED']='1'
            $start.EnvironmentVariables['WD_BRIDGE_BUNDLE_ROOT']=$root
            $start.EnvironmentVariables['WD_BRIDGE_PYTHON_WRAPPER']=$wrapper
            $start.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$ExpectedManifestHash.ToUpperInvariant()
            $start.EnvironmentVariables['AGENT_BRIDGE_AGENT']=$Lane
            $clock = [Diagnostics.Stopwatch]::StartNew()
            $process = [Diagnostics.Process]::Start($start)
            if ($null -eq $process) { throw 'start' }
            # Each pending read owns at most 4096 chars; accumulated stdout <=64 KiB,
            # stderr <=4 KiB. Never ReadToEndAsync on a potentially unbounded child.
            $outBuffer=New-Object char[] 4096;$errBuffer=New-Object char[] 4096
            $outText=New-Object Text.StringBuilder;$errChars=0
            $outRead=$process.StandardOutput.ReadAsync($outBuffer,0,$outBuffer.Length)
            $errRead=$process.StandardError.ReadAsync($errBuffer,0,$errBuffer.Length)
            $outDone=$false;$errDone=$false;$completed=$false
            $result.cleanup='unknown'
            while ($true) {
                if ($clock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)) {
                    $result.outcome='unknown';$result.reason='timeout';break
                }
                if (-not $outDone -and $outRead.IsCompleted) {
                    $count=$outRead.Result
                    if ($count -eq 0) { $outDone=$true }
                    else {
                        if ($outText.Length+$count -gt 65536) { $result.reason='output_over_budget';break }
                        [void]$outText.Append($outBuffer,0,$count)
                        $outRead=$process.StandardOutput.ReadAsync($outBuffer,0,$outBuffer.Length)
                    }
                }
                if (-not $errDone -and $errRead.IsCompleted) {
                    $count=$errRead.Result
                    if ($count -eq 0) { $errDone=$true }
                    else {
                        $errChars+=$count
                        if ($errChars -gt 4096) { $result.reason='output_over_budget';break }
                        $errRead=$process.StandardError.ReadAsync($errBuffer,0,$errBuffer.Length)
                    }
                }
                if ($outDone -and $errDone -and $process.HasExited) { $completed=$true;break }
                [Threading.Thread]::Sleep(5)
            }
            if ($completed) {
                $result.cleanup='wrapper_exited'
                if ($process.ExitCode -ne 0) { $result.reason='reader_refused' }
                else {
                    $json = $outText.ToString().Trim()
                    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 65536) { throw 'output' }
                    $jsonArgs=@{InputObject=$json;ErrorAction='Stop'}
                    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonArgs.DateKind='String' }
                    $view=ConvertFrom-Json @jsonArgs
                    if ($view -isnot [pscustomobject]) { throw 'shape' }
                    if ($view.PSObject.Properties['schema'] -and $view.schema -ceq 'wd.mind-direct-status.v1') {
                        $fleet = $snapshots['wd-fleet.json'].Text | ConvertFrom-Json -ErrorAction Stop
                        Assert-WdMindDirectStatus $view $Lane $snapshots['wd-fleet.json'].Hash $fleet
                    } else {
                    $keys=@('schema','authority','scope','lane','as_of_utc','binding','pointer','freshness',
                        'identity','source','lanes','prs','refusal')
                    $extraKeys=@('fleet_sha256','source_event_refs','status_observation')
                    $presentExtra=@($extraKeys | Where-Object { $null -ne $view.PSObject.Properties[$_] })
                    if ($presentExtra.Count -ne 0) {
                        if ($presentExtra.Count -ne $extraKeys.Count) { throw 'shape' }
                        $keys += $extraKeys
                    }
                    if ($view -isnot [pscustomobject] -or @($view.PSObject.Properties).Count -ne $keys.Count -or
                        @($view.PSObject.Properties.Name | Where-Object {$_ -cnotin $keys}).Count -ne 0 -or
                        $view.schema -cne 'wd.bridge-mind-lane-read.v1' -or $view.authority -cne 'none' -or
                        $view.scope -cne 'read_only_lane_view_not_gate' -or $view.lane -cne $Lane -or
                        $null -ne $view.refusal -or $view.freshness -isnot [pscustomobject]) { throw 'shape' }
                    foreach ($name in @('schema','authority','scope','lane','as_of_utc')) {
                        if ($view.PSObject.Properties[$name].Value -isnot [string]) { throw 'shape' }
                    }
                    foreach ($name in @('binding','pointer','identity','source','lanes','prs')) {
                        if ($view.PSObject.Properties[$name].Value -isnot [pscustomobject]) { throw 'shape' }
                    }
                    Assert-WdMindNestedView $view
                    if ($presentExtra.Count -ne 0) {
                        $fleet = $snapshots['wd-fleet.json'].Text | ConvertFrom-Json -ErrorAction Stop
                        Assert-WdMindDirectStatus $view.status_observation $Lane $snapshots['wd-fleet.json'].Hash $fleet
                        $nestedRefs=@($view.status_observation.facts | ForEach-Object { $_.event_ref })
                        if ($view.fleet_sha256 -isnot [string] -or
                            $view.fleet_sha256 -cne $view.status_observation.fleet_sha256 -or
                            $view.source_event_refs -isnot [array] -or
                            $view.source_event_refs.Count -ne $nestedRefs.Count) { throw 'shape' }
                        for ($i=0; $i -lt $nestedRefs.Count; $i++) {
                            if ($view.source_event_refs[$i] -isnot [string] -or
                                $view.source_event_refs[$i] -cne $nestedRefs[$i]) { throw 'shape' }
                        }
                    }
                    $age=$view.freshness.age_seconds;$maxAge=$view.freshness.max_age_seconds
                    if (($age -isnot [int] -and $age -isnot [long] -and $age -isnot [double]) -or
                        ($maxAge -isnot [int] -and $maxAge -isnot [long] -and $maxAge -isnot [double]) -or
                        $view.freshness.status -cne 'fresh' -or $age -lt 0 -or $maxAge -le 0 -or
                        $maxAge -gt 900 -or $age -gt $maxAge -or [double]::IsNaN([double]$age) -or
                        [double]::IsNaN([double]$maxAge)) { throw 'shape' }
                    }
                    # Forward only the validated object. The original text can
                    # retain duplicate members that the parser already discarded.
                    $json=ConvertTo-Json -InputObject $view -Depth 16 -Compress
                    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 65536) { throw 'output' }
                    $sha=[Security.Cryptography.SHA256]::Create()
                    try {$result.view_sha256=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($json))).Replace('-','').ToLowerInvariant()}
                    finally {$sha.Dispose()}
                    $result.view_json=$json;$result.outcome='success';$result.reason='status_projection'
                }
            }
        }
    } catch {
        $result.outcome='refused';$result.reason='startup_read_refused'
        $result.view_json='';$result.view_sha256=''
    } finally {
        # Never kill or retry. An unfinished wrapper/pipe remains explicitly UNKNOWN.
        if ($null -ne $process) { $process.Dispose() }
    }
    if ($Worktree) {
        try {
            $audit=Get-WdMindReceiptAudit $Worktree
            if (-not [IO.Directory]::Exists($audit)) { [void][IO.Directory]::CreateDirectory($audit) }
            Assert-WdMindReceiptDirectory $audit
            $path=Join-Path $audit 'wd-mind-startup-view.json'
            Assert-WdMindReceiptTarget $path
            $record=[ordered]@{schema=$result.schema;lane=$Lane;outcome=$result.outcome;reason=$result.reason;
                authority='none';view_sha256=$result.view_sha256;cleanup=$result.cleanup;
                manifest_sha256=$ExpectedManifestHash.ToLowerInvariant();observed_utc=[DateTime]::UtcNow.ToString('o')}
            if ($result.outcome -ceq 'success' -and $view.schema -ceq 'wd.mind-direct-status.v1') {
                $record.fleet_sha256=$view.fleet_sha256
                $record.source_event_refs=@($view.facts | ForEach-Object { $_.event_ref })
                $record.observation_status=$view.observation_status
            } elseif ($result.outcome -ceq 'success' -and $null -ne $view.PSObject.Properties['status_observation']) {
                $record.fleet_sha256=$view.fleet_sha256
                $record.source_event_refs=@($view.source_event_refs)
                $record.observation_status=$view.status_observation.observation_status
            }
            $temp=$path+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
            $stream=[IO.File]::Open($temp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try {
                $bytes=[Text.Encoding]::ASCII.GetBytes(($record|ConvertTo-Json -Compress))
                $stream.Write($bytes,0,$bytes.Length)
            } finally { $stream.Dispose() }
            Assert-WdMindReceiptDirectory $audit
            Assert-WdMindReceiptTarget $path
            # File APIs cannot reinterpret a directory target as a destination
            # folder, unlike Move-Item. Replace atomically only for a plain file.
            if ([IO.File]::Exists($path)) { [IO.File]::Replace($temp,$path,$null) }
            else { [IO.File]::Move($temp,$path) }
            $result.record_written=$true
        } catch {
            $result.outcome='unknown';$result.reason='record_write';$result.view_json='';$result.view_sha256=''
        }
    }
    return [pscustomobject]$result
}

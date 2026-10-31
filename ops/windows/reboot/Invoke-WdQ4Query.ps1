#requires -Version 5.1
<#
Synthetic candidate, default off. The trusted caller MUST independently pin this
wrapper and supply BundleRoot + expected commit + manifest hash. Parameters and
hash equality do not authenticate that caller or authorize memory/model access.
No WD_* environment value is an anchor. No install or environment mutation.
Optional resolved_inputs in the anchored fleet binds three explicit snapshots.
Without it output stays hash-only UNRESOLVED. A pin is not source/policy approval;
no real caller identity, authorized shared store or rollout is established here.
Input is raw UTF-8 stdin only. The watchdog covers the child transport, NOT
synchronous preflight, stdin waiting, process creation, or the whole call.
Same-user file/check-use races and OS-level logging/privacy remain outside scope.
#>
[CmdletBinding(PositionalBinding=$false)]
param(
    [string] $BundleRoot = '',
    [string] $ExpectedBundleCommit = '',
    [string] $ExpectedManifestSha256 = '',
    [double] $TimeoutSeconds = 30
)
$script:WdQ4WrapperPath=$PSCommandPath
$script:WdQ4PinnedJobHash=$null
$script:WdQ4PinnedJobBlock=$null

function ConvertFrom-WdQ4StrictJson {
    param([string] $Text)
    # Two independently manifest-pinned entrypoints need this bootstrap guard:
    # wrapper before it may load any helper, and standalone config helper.
    # Tests require their bodies to remain identical. Framework JSON parsing
    # supplies grammar; this raw-token pass forbids per-object key collisions.
    if (-not $Text -or $Text.Length -gt 1048576 -or
        -not $Text.TrimStart().StartsWith('{') -or -not $Text.TrimEnd().EndsWith('}')) {
        throw 'q4_json_refused'
    }
    $parsed=ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    if ($parsed -isnot [pscustomobject]) { throw 'q4_json_refused' }
    $pattern='"(?:\\["\\/bfnrt]|\\u[0-9a-fA-F]{4}|[^"\\\x00-\x1f])*"|[{}\[\],:]|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|[ \t\r\n]+|(?<bad>.)'
    $tokens=[regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline)
    $stack=[Collections.Generic.List[object]]::new()
    $count=0
    foreach ($token in $tokens) {
        $count++
        if ($count -gt 131072 -or $token.Groups['bad'].Success) { throw 'q4_json_refused' }
        $value=$token.Value
        if ($value -ceq '{' -or $value -ceq '[') {
            if ($stack.Count -ge 64) { throw 'q4_json_refused' }
            $stack.Add([pscustomobject]@{Object=($value -ceq '{');ExpectKey=$true;
                Keys=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)})
        } elseif ($value -ceq '}' -or $value -ceq ']') {
            if ($stack.Count -eq 0 -or
                $stack[$stack.Count-1].Object -ne ($value -ceq '}')) { throw 'q4_json_refused' }
            $stack.RemoveAt($stack.Count-1)
        } elseif ($value -ceq ',') {
            if ($stack.Count -eq 0) { throw 'q4_json_refused' }
            if ($stack[$stack.Count-1].Object) { $stack[$stack.Count-1].ExpectKey=$true }
        } elseif ($value.StartsWith('"') -and $stack.Count -gt 0) {
            $frame=$stack[$stack.Count-1]
            if ($frame.Object -and $frame.ExpectKey) {
                # Decode JSON escapes as a VALUE under one fixed key, never as
                # attacker-chosen property names before collision detection.
                $decoded=ConvertFrom-Json -InputObject ('{"key":'+$value+'}') -ErrorAction Stop
                if ($decoded.key -isnot [string] -or -not $frame.Keys.Add($decoded.key)) {
                    throw 'q4_json_duplicate_key'
                }
                $frame.ExpectKey=$false
            }
        }
    }
    if ($stack.Count -ne 0) { throw 'q4_json_refused' }
    return $parsed
}

function Assert-WdQ4LocalPath {
    param([string] $Path, [bool] $Directory = $false)
    if (-not $Path -or $Path -cnotmatch '^[A-Za-z]:\\' -or
        $Path.Substring(2).Contains(':') -or $Path.Contains('/') -or
        $Path -match '[\x00-\x1f<>|"?*]') { throw 'binding_refused' }
    foreach ($part in $Path.Substring(3).Split('\')) {
        if (-not $part -or $part -in @('.', '..') -or $part.EndsWith('.') -or
            $part.StartsWith(' ') -or $part.EndsWith(' ') -or
            $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
            throw 'binding_refused'
        }
    }
    $full = [IO.Path]::GetFullPath($Path)
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($full))
    if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) { throw 'binding_refused' }
    $current = $full
    while ($current) {
        $attrs = [IO.File]::GetAttributes($current)
        if (($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'binding_refused' }
        if ($current -ceq $full) {
            if ((($attrs -band [IO.FileAttributes]::Directory) -ne 0) -ne $Directory) { throw 'binding_refused' }
        } elseif (($attrs -band [IO.FileAttributes]::Directory) -eq 0) { throw 'binding_refused' }
        $current = [IO.Path]::GetDirectoryName($current)
    }
    return $full
}

function Read-WdQ4Snapshot {
    param([string] $Path, [int] $Limit = 16777216)
    $full = Assert-WdQ4LocalPath -Path $Path
    $s = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($s.Length -gt $Limit) { throw 'binding_refused' }
        $bytes = New-Object byte[] ([int]$s.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $s.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) { throw 'binding_refused' }
            $offset += $count
        }
        if ($s.ReadByte() -ne -1) { throw 'binding_refused' }
    } finally { $s.Dispose() }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '') }
    finally { $sha.Dispose() }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    return [pscustomobject]@{Path=$full;Hash=$hash;Text=$utf8.GetString($bytes).TrimStart([char]0xFEFF);Bytes=$bytes}
}

function Get-WdQ4RosterPairs {
    param($Document, [bool] $Snapshot = $false)
    $lanes=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
    if ($Document -isnot [pscustomobject] -or $Document.lanes -isnot [Array] -or
        $Document.tools_supervisor -isnot [pscustomobject]) { throw 'roster_fleet_mismatch' }
    if ($Snapshot) {
        $keys=[string[]]@($Document.PSObject.Properties.Name)
        [Array]::Sort($keys,[StringComparer]::Ordinal)
        if (($keys -join ',') -cne 'lanes,schema,tools_supervisor' -or
            $Document.schema -isnot [string] -or $Document.schema -cne 'wd.q4-roster-snapshot.v1') { throw 'roster_fleet_mismatch' }
    }
    $pairs=[Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
    $uuids=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($entry in (@($Document.lanes)+@($Document.tools_supervisor))) {
        if ($entry -isnot [pscustomobject] -or $entry.agent -isnot [string]) { throw 'roster_fleet_mismatch' }
        if ($lanes -cnotcontains $entry.agent) {
            if ($Snapshot) { throw 'roster_fleet_mismatch' }
            continue
        }
        if ($Snapshot) {
            $keys=[string[]]@($entry.PSObject.Properties.Name)
            [Array]::Sort($keys,[StringComparer]::Ordinal)
            if (($keys -join ',') -cne 'agent,agent_uuid' -and
                (($keys -join ',') -cne 'agent,agent_uuid,role' -or $entry.role -isnot [string])) { throw 'roster_fleet_mismatch' }
        }
        if ($entry.agent_uuid -isnot [string] -or
            $entry.agent_uuid -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z' -or
            $pairs.ContainsKey($entry.agent) -or -not $uuids.Add($entry.agent_uuid)) { throw 'roster_fleet_mismatch' }
        $pairs.Add($entry.agent,$entry.agent_uuid)
    }
    if ($pairs.Count -ne $lanes.Count) { throw 'roster_fleet_mismatch' }
    return ,$pairs
}

function Get-WdQ4QueryPlan {
    param([string] $BundleRoot, [string] $ExpectedBundleCommit,
          [string] $ExpectedManifestSha256, [double] $TimeoutSeconds = 30)
    if (-not $BundleRoot -or $ExpectedBundleCommit -cnotmatch '^[0-9a-f]{40}$' -or
        $ExpectedManifestSha256 -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'external_anchor_unavailable' }
    if ([double]::IsNaN($TimeoutSeconds) -or [double]::IsInfinity($TimeoutSeconds) -or
        $TimeoutSeconds -le 0 -or $TimeoutSeconds -gt 1800) { throw 'binding_refused' }
    $root = Assert-WdQ4LocalPath -Path $BundleRoot -Directory $true
    $manifest = Read-WdQ4Snapshot (Join-Path $root 'deployment-manifest.json')
    if ($manifest.Hash -cne $ExpectedManifestSha256.ToUpperInvariant()) { throw 'binding_refused' }
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility') -ErrorAction Stop
    $deployment = ConvertFrom-WdQ4StrictJson -Text $manifest.Text
    if ($deployment.schema_version -ne 1 -or $deployment.source_commit -cne $ExpectedBundleCommit -or
        $null -eq $deployment.files) { throw 'binding_refused' }
    $snapshots = @{}
    foreach ($name in @('Invoke-WdQ4Query.ps1', 'BridgeCodeContext.ps1', 'Get-WdQ4ReadConfig.ps1',
                        'wd-fleet.json', 'bridge-code-files.json')) {
        $pin = $deployment.files.PSObject.Properties[$name]
        if ($null -eq $pin -or [string]$pin.Value -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'binding_refused' }
        $snapshot = Read-WdQ4Snapshot (Join-Path $root $name)
        if ($snapshot.Hash -cne ([string]$pin.Value).ToUpperInvariant()) { throw 'binding_refused' }
        $snapshots[$name] = $snapshot
    }
    if ((Read-WdQ4Snapshot $script:WdQ4WrapperPath).Hash -cne $snapshots['Invoke-WdQ4Query.ps1'].Hash) {
        throw 'binding_refused'
    }
    # Execute exactly the verified helper snapshots. Do not initialize/export
    # BridgeCodeContext discovery variables or run its import smoke.
    . ([scriptblock]::Create($snapshots['BridgeCodeContext.ps1'].Text))
    . ([scriptblock]::Create($snapshots['Get-WdQ4ReadConfig.ps1'].Text))
    $cfg = Get-WdQ4ReadConfig -FleetPath $snapshots['wd-fleet.json'].Path -FleetSha256 $snapshots['wd-fleet.json'].Hash
    if ($null -eq $cfg) { return [pscustomobject]@{Enabled=$false} }
    # No process is started unless the job helper is itself in the externally
    # pinned deployment manifest; execute the verified bytes, not a later read.
    $jobPin=$deployment.files.PSObject.Properties['WdQ4ProcessJob.ps1']
    if ($null -eq $jobPin -or [string]$jobPin.Value -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'binding_refused' }
    $jobSnapshot=Read-WdQ4Snapshot (Join-Path $root 'WdQ4ProcessJob.ps1') 1048576
    if ($jobSnapshot.Hash -cne ([string]$jobPin.Value).ToUpperInvariant()) { throw 'binding_refused' }
    $null = ConvertFrom-WdQ4StrictJson -Text $snapshots['bridge-code-files.json'].Text
    $package = Get-WdBridgeCodePackageDefinition -Path $snapshots['bridge-code-files.json'].Path
    if ($package.Hash -cne $snapshots['bridge-code-files.json'].Hash -or
        $cfg.WD_Q4_CODE_COMMIT -cne $ExpectedBundleCommit -or
        -not $cfg.WD_Q4_CODE_MANIFEST.Equals($package.Path, [StringComparison]::OrdinalIgnoreCase) -or
        $cfg.WD_Q4_CODE_MANIFEST_SHA256.ToUpperInvariant() -cne $package.Hash) { throw 'binding_refused' }
    foreach ($relative in @('tools/wd_q4_agent_query.py', 'sandbox/mind/step2/runtime_launcher.py',
                            'sandbox/mind/step2/runtime_bootstrap.py', 'sandbox/mind/step2/runtime_manifest.py')) {
        if (@($package.Definition.python_files) -cnotcontains $relative) { throw 'binding_refused' }
    }
    # Reuse the fail-closed package verifier, including unexpected files,
    # bytecode and reparse checks. It executes no Python or registered tool.
    $integrity = Assert-WdBridgeCodePackageIntegrity -BundleRoot $root -Deployment $deployment -Definition $package.Definition
    $python = Assert-WdQ4LocalPath -Path $cfg.WD_Q4_PYTHON
    $pythonBinding = $deployment.bridge_code_package
    if ($null -eq $pythonBinding -or
        -not $python.Equals([string]$pythonBinding.python_executable, [StringComparison]::OrdinalIgnoreCase) -or
        $cfg.WD_Q4_PYTHON_SHA256 -cne ([string]$pythonBinding.python_executable_sha256).ToLowerInvariant() -or
        (Get-WdBridgeCodeFileSha256 -Path $python) -cne $cfg.WD_Q4_PYTHON_SHA256.ToUpperInvariant()) {
        throw 'binding_refused'
    }
    $scratch=Assert-WdQ4LocalPath -Path $cfg.WD_Q4_SCRATCH_ROOT -Directory $true
    foreach ($other in @($root,$integrity.CodeRoot,$cfg.WD_Q4_RUNTIME_ROOT,$cfg.WD_Q4_MEMORY_ROOT,
                         $cfg.WD_Q4_RUNTIME_MANIFEST,$cfg.WD_Q4_DESCRIPTOR,$cfg.WD_Q4_LOCK_FILE)) {
        $left=$scratch.TrimEnd('\');$right=$other.TrimEnd('\')
        if ($left.Equals($right,[StringComparison]::OrdinalIgnoreCase) -or
            $left.StartsWith($right+'\',[StringComparison]::OrdinalIgnoreCase) -or
            $right.StartsWith($left+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'binding_refused' }
    }
    $bootstrap = 'sandbox/mind/step2/runtime_bootstrap.py'
    $verifier = 'sandbox/mind/step2/runtime_manifest.py'
    $binding = [ordered]@{
        interpreter=$python;interpreter_sha256=$cfg.WD_Q4_PYTHON_SHA256
        bootstrap=(Join-Path $integrity.CodeRoot $bootstrap.Replace('/', '\'))
        bootstrap_sha256=([string]$deployment.files.PSObject.Properties['tools-bootstrap/'+$bootstrap].Value).ToLowerInvariant()
        verifier_sha256=([string]$deployment.files.PSObject.Properties['tools-bootstrap/'+$verifier].Value).ToLowerInvariant()
        runtime_root=$cfg.WD_Q4_RUNTIME_ROOT;runtime_manifest=$cfg.WD_Q4_RUNTIME_MANIFEST
        runtime_manifest_sha256=$cfg.WD_Q4_RUNTIME_MANIFEST_SHA256
        runtime_generation=$cfg.WD_Q4_RUNTIME_GENERATION;runtime_revision=$cfg.WD_Q4_RUNTIME_REVISION
        memory=$cfg.WD_Q4_MEMORY_ROOT;descriptor=$cfg.WD_Q4_DESCRIPTOR
        descriptor_sha256=$cfg.WD_Q4_DESCRIPTOR_SHA256;lock_file=$cfg.WD_Q4_LOCK_FILE
        lock_timeout=$cfg.WD_Q4_LOCK_TIMEOUT;timeout=$TimeoutSeconds;scratch_root=$scratch
    }
    if ((Read-WdQ4Snapshot $binding.descriptor 65536).Hash -cne $binding.descriptor_sha256.ToUpperInvariant() -or
        (Read-WdQ4Snapshot $binding.runtime_manifest).Hash -cne $binding.runtime_manifest_sha256.ToUpperInvariant() -or
        (Read-WdQ4Snapshot (Join-Path $root 'deployment-manifest.json')).Hash -cne $manifest.Hash) {
        throw 'binding_refused'
    }
    $resolvedInputs=$null;$resolvedPins=$null
    if ($null -ne $cfg.PSObject.Properties['WD_Q4_RESOLVED_MEMORY_PATH']) {
        if (@($package.Definition.python_files) -cnotcontains 'tools/wd_q4_resolved_child.py') {
            throw 'binding_refused'
        }
        $resolvedInputs=@{};$resolvedPins=@{}
        foreach ($name in @('memory','projection','policy','roster')) {
            $pathKey='WD_Q4_RESOLVED_'+$name.ToUpperInvariant()+'_PATH'
            $hashKey='WD_Q4_RESOLVED_'+$name.ToUpperInvariant()+'_SHA256'
            $limit=if($name -ceq 'policy'){65536}elseif($name -ceq 'roster'){1048576}else{16777216}
            $snapshot=Read-WdQ4Snapshot $cfg.$pathKey $limit
            if ($snapshot.Hash -cne $cfg.$hashKey.ToUpperInvariant()) { throw 'binding_refused' }
            $resolvedInputs[$name]=$snapshot.Bytes
            $resolvedPins[$name+'_sha256']=$cfg.$hashKey
        }
        # Independent immutable roster avoids a memory<->fleet digest cycle.
        # Compare declared identity VALUES against the separately pinned fleet;
        # equality is consistency, never authentication or live lane proof.
        try {
            $rosterDocument=ConvertFrom-WdQ4StrictJson -Text ([Text.UTF8Encoding]::new($false,$true).GetString($resolvedInputs.roster))
            $rosterPairs=Get-WdQ4RosterPairs -Document $rosterDocument -Snapshot $true
            $fleetDocument=ConvertFrom-WdQ4StrictJson -Text $snapshots['wd-fleet.json'].Text
            $fleetPairs=Get-WdQ4RosterPairs -Document $fleetDocument
            foreach($name in $rosterPairs.Keys) {
                if ($rosterPairs[$name] -cne $fleetPairs[$name]) { throw 'roster_fleet_mismatch' }
            }
        } catch { throw 'roster_fleet_mismatch' }
    }
    return [pscustomobject]@{Enabled=$true;CodeRoot=$integrity.CodeRoot;Config=$binding;
        ResolvedInputs=$resolvedInputs;ResolvedPins=$resolvedPins;
        JobHelperBytes=$jobSnapshot.Bytes;JobHelperSha256=$jobSnapshot.Hash}
}

function Read-WdQ4QueryBytes {
    param([IO.Stream] $InputStream)
    if ($null -eq $InputStream) { $InputStream = [Console]::OpenStandardInput() }
    $buffer = New-Object byte[] 65537
    $offset = 0
    while ($offset -lt $buffer.Length) {
        $count = $InputStream.Read($buffer, $offset, $buffer.Length - $offset)
        if ($count -le 0) { break }
        $offset += $count
    }
    if ($offset -gt 65536) { throw 'invalid_query' }
    $bytes = New-Object byte[] $offset
    [Array]::Copy($buffer, $bytes, $offset)
    return ,$bytes
}

function ConvertTo-WdQ4Argument {
    param([string] $Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-WdQ4QueryTransport {
    param($Plan, [byte[]] $Query)
    # Fixed adapter: only metadata is in argv. No environment-based channel;
    # the dormant public CLI stays untouched. Query stays on the stdin pipe.
    $adapter = @'
import io,json,struct,sys
sys.path.insert(0,sys.argv[1])
resolved=False
try:
 from tools.wd_q4_agent_query import VerifiedQ4Config,query_with_verified_config
 config=VerifiedQ4Config(**json.loads(sys.argv[2]))
 pins=json.loads(sys.argv[3])
 resolved=pins is not None
 if resolved:
  from tools.wd_q4_resolved_child import run_resolved
  if type(pins) is not dict or set(pins)!={"memory_sha256","projection_sha256","policy_sha256","roster_sha256"}: raise ValueError()
  def take(limit):
   header=sys.stdin.buffer.read(4)
   if len(header)!=4: raise ValueError()
   size=struct.unpack(">I",header)[0]
   if size>limit: raise ValueError()
   data=sys.stdin.buffer.read(size)
   if len(data)!=size: raise ValueError()
   return data
  memory=take(16777216);projection=take(16777216);policy=take(65536);roster=take(1048576);query=take(65536)
  if sys.stdin.buffer.read(1)!=b"": raise ValueError()
  code=run_resolved(config,io.BytesIO(query),sys.stdout,
    memory_bytes=memory,memory_sha256=pins["memory_sha256"],
    projection_bytes=projection,projection_sha256=pins["projection_sha256"],
    policy_bytes=policy,policy_sha256=pins["policy_sha256"],
    roster_bytes=roster,roster_sha256=pins["roster_sha256"])
  sys.exit(code)
 result=query_with_verified_config(config,sys.stdin.buffer)
 result=dict(result,schema="wd.q4-agent-wrapper.v1")
 print(json.dumps(result,sort_keys=True),flush=True)
 sys.exit(2 if result["status"]=="REFUSED" else 0)
except Exception:
 if resolved:
  print('{"schema":"wd.q4-resolved-child.v1","status":"REFUSED","reason":"resolved_refused"}',flush=True)
 else:
  print('{"schema":"wd.q4-agent-wrapper.v1","status":"REFUSED","reason":"runtime_refused","results":[]}',flush=True)
 sys.exit(2)
'@
    [byte[]]$stdinBytes=$Query
    if ($null -ne $Plan.ResolvedPins) {
        $frame=[IO.MemoryStream]::new()
        try {
            $parts=[Collections.Generic.List[byte[]]]::new()
            foreach ($name in @('memory','projection','policy','roster')) {
                [byte[]]$part=$Plan.ResolvedInputs.$name
                $limit=if($name -ceq 'policy'){65536}elseif($name -ceq 'roster'){1048576}else{16777216}
                if ($null -eq $part -or $part.Length -gt $limit) { throw 'binding_refused' }
                $parts.Add($part)
            }
            if ($Query.Length -gt 65536) { throw 'invalid_query' }
            $parts.Add($Query)
            foreach ($part in $parts) {
                $header=[BitConverter]::GetBytes([uint32]$part.Length)
                if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($header) }
                $frame.Write($header,0,4)
                $frame.Write($part,0,$part.Length)
            }
            $stdinBytes=$frame.ToArray()
        } finally { $frame.Dispose() }
    }
    if ($Plan.JobHelperBytes -isnot [byte[]] -or $Plan.JobHelperBytes.Length -gt 1048576 -or
        [string]$Plan.JobHelperSha256 -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'binding_refused' }
    $sha=[Security.Cryptography.SHA256]::Create()
    try { $actual=[BitConverter]::ToString($sha.ComputeHash($Plan.JobHelperBytes)).Replace('-','') }
    finally { $sha.Dispose() }
    if ($actual -cne ([string]$Plan.JobHelperSha256).ToUpperInvariant()) { throw 'binding_refused' }
    $pinsJson=if($null -eq $Plan.ResolvedPins){'null'}else{ConvertTo-Json -InputObject $Plan.ResolvedPins -Depth 4 -Compress}
    $arguments = @('-I', '-S', '-B', '-c', $adapter, $Plan.CodeRoot,
                   ($Plan.Config | ConvertTo-Json -Depth 6 -Compress), $pinsJson)
    $seconds=[int][Math]::Floor([double]$Plan.Config.timeout)
    if ($seconds -lt 1 -or $seconds -gt 1800) { throw 'runtime_refused' }
    if ($null -eq $script:WdQ4PinnedJobBlock) {
        $helperText=[Text.UTF8Encoding]::new($false,$true).GetString($Plan.JobHelperBytes).TrimStart([char]0xFEFF)
        . ([scriptblock]::Create($helperText))
        $script:WdQ4PinnedJobBlock=${function:Invoke-WdQ4ProcessJob}
        if ($null -eq $script:WdQ4PinnedJobBlock) { throw 'binding_refused' }
        $script:WdQ4PinnedJobHash=$actual
    } elseif ($script:WdQ4PinnedJobHash -cne $actual) { throw 'binding_refused' }
    $process=& $script:WdQ4PinnedJobBlock -Executable $Plan.Config.interpreter -Arguments ([string[]]$arguments) `
        -WorkingDirectory $Plan.CodeRoot -ScratchRoot $Plan.Config.scratch_root -InputBytes $stdinBytes `
        -TimeoutSeconds $seconds -MaxOutputBytes 262144
    if ($null -eq $process -or $process.Stdout -isnot [byte[]] -or $process.Stdout.Length -gt 262144) {
        throw 'runtime_refused'
    }
    $utf8=[Text.UTF8Encoding]::new($false,$true)
    $json=$utf8.GetString($process.Stdout).Trim()
        # This protocol carries ASCII tokens/hashes, not arbitrary text. Reject
        # escapes before PS5 JSON can fold case/duplicate properties or arrays.
        if (-not $json.StartsWith('{') -or -not $json.EndsWith('}') -or
            $json -match '[^A-Za-z0-9_.,:{}\[\]"\s-]') { throw 'runtime_refused' }
        $keyMatches=[regex]::Matches($json,'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
        $keyNames=@($keyMatches|ForEach-Object {$_.Groups[1].Value})
        $rootKeys=@('schema','status','reason','scope','descriptor_sha256','memory_sha256','results')
        $itemKeys=@('ref_sha256','doc_id_sha256','ref_kind','canonical_event')
        if ($null -ne $Plan.ResolvedPins) { $itemKeys+=@('stored_text') }
        foreach($key in $keyNames){
            if($rootKeys -cnotcontains $key -and $itemKeys -cnotcontains $key){throw 'runtime_refused'}
        }
        foreach($key in $rootKeys){
            if(@($keyNames|Where-Object {$_ -ceq $key}).Count -gt 1){throw 'runtime_refused'}
        }
        $data=ConvertFrom-WdQ4StrictJson -Text $json
        $resultCount=if($data.status -ceq 'REFUSED'){0}else{@($data.results).Count}
        foreach($key in $itemKeys){
            if(@($keyNames|Where-Object {$_ -ceq $key}).Count -ne $resultCount){throw 'runtime_refused'}
        }
        if ($data -isnot [pscustomobject] -or $data.schema -isnot [string] -or
            $data.schema -cne $(if($null -ne $Plan.ResolvedPins){'wd.q4-resolved-child.v1'}else{'wd.q4-agent-wrapper.v1'}) -or $data.status -isnot [string] -or
            $data.status -cnotin @('ANSWER','ABSTAIN','DEGRADED','REFUSED') -or
            ($data.status -ceq 'REFUSED') -ne ($process.ExitCode -eq 2) -or
            $process.ExitCode -notin @(0,2)) { throw 'runtime_refused' }
        return $data
}

function Test-WdQ4Hash {
    param($Value)
    return ($Value -is [string] -and $Value -cmatch '^[0-9a-f]{64}$')
}

function ConvertFrom-WdQ4Result {
    param($Result, [string] $ExpectedDescriptor)
    if ($Result -isnot [pscustomobject] -or $Result.schema -isnot [string] -or
        $Result.schema -cne 'wd.q4-agent-wrapper.v1' -or $Result.status -isnot [string] -or
        $Result.status -cnotin @('ANSWER','ABSTAIN','DEGRADED','REFUSED')) { throw 'runtime_refused' }
    $names=[string[]]@($Result.PSObject.Properties.Name)
    [Array]::Sort($names,[StringComparer]::Ordinal)
    if ($Result.status -ceq 'REFUSED') {
        if (($names -join ',') -cne 'reason,results,schema,status' -or
            $Result.reason -isnot [string] -or $Result.results -isnot [Array] -or
            $Result.results.Count -ne 0) { throw 'runtime_refused' }
        return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason='runtime_refused';results=@()}
    }
    if (($names -join ',') -cne 'descriptor_sha256,memory_sha256,results,schema,scope,status' -or
        $Result.scope -isnot [string] -or $Result.scope -cne 'wd.q4-shared-projected-events.v1' -or
        -not (Test-WdQ4Hash $ExpectedDescriptor) -or -not (Test-WdQ4Hash $Result.descriptor_sha256) -or
        $Result.descriptor_sha256 -cne $ExpectedDescriptor -or
        -not (Test-WdQ4Hash $Result.memory_sha256) -or $Result.results -isnot [Array] -or
        $Result.results.Count -gt 100 -or
        ($Result.status -ceq 'ANSWER' -and $Result.results.Count -eq 0) -or
        ($Result.status -ceq 'ABSTAIN' -and $Result.results.Count -ne 0)) { throw 'runtime_refused' }
    $items=@()
    foreach ($item in $Result.results) {
        if ($item -isnot [pscustomobject]) { throw 'runtime_refused' }
        $keys=[string[]]@($item.PSObject.Properties.Name)
        [Array]::Sort($keys,[StringComparer]::Ordinal)
        if (($keys -join ',') -cne 'canonical_event,doc_id_sha256,ref_kind,ref_sha256' -or
            $item.ref_kind -isnot [string] -or $item.ref_kind -cne 'sha256_of_projected_metadata' -or
            $item.canonical_event -isnot [bool] -or $item.canonical_event -ne $false -or
            -not (Test-WdQ4Hash $item.ref_sha256) -or -not (Test-WdQ4Hash $item.doc_id_sha256)) {
            throw 'runtime_refused'
        }
        $items+=@{ref_sha256=$item.ref_sha256;doc_id_sha256=$item.doc_id_sha256;
                  ref_kind='sha256_of_projected_metadata';canonical_event=$false}
    }
    return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status=$Result.status;scope=$Result.scope;
        descriptor_sha256=$Result.descriptor_sha256;memory_sha256=$Result.memory_sha256;
        content_mode='hash_only_unresolved';results=$items}
}


function ConvertFrom-WdQ4ResolvedResult {
    param($Result, $Plan)
    # Validate again outside transport so a replaced/malformed transport cannot
    # skip schema checks. These pins attest caller-selected bytes, not identity.
    # TRUSTED-CHILD LIMIT: this outer boundary does not independently rederive
    # stored_text from projection bytes. The package-pinned run_resolved and
    # query_and_resolve own that binding; caller must approve source and policy.
    # Shape/pin checks alone never authenticate projected content or its author.
    if ($null -eq $Plan.ResolvedPins -or $Result -isnot [pscustomobject] -or
        $Result.schema -isnot [string] -or $Result.schema -cne 'wd.q4-resolved-child.v1' -or
        $Result.status -isnot [string] -or
        $Result.status -cnotin @('ANSWER','ABSTAIN','DEGRADED','REFUSED')) { throw 'runtime_refused' }
    foreach ($name in @('memory_sha256','projection_sha256','policy_sha256','roster_sha256')) {
        if (-not (Test-WdQ4Hash $Plan.ResolvedPins.$name) -or $Plan.ResolvedPins.$name.Length -ne 64) { throw 'binding_refused' }
    }
    $keys=[string[]]@($Result.PSObject.Properties.Name)
    [Array]::Sort($keys,[StringComparer]::Ordinal)
    if ($Result.status -ceq 'REFUSED') {
        if (($keys -join ',') -cne 'reason,schema,status' -or $Result.reason -isnot [string]) {
            throw 'runtime_refused'
        }
        return [pscustomobject]@{schema='wd.q4-agent-wrapper.v2';status='REFUSED';
            reason='resolved_refused';results=@()}
    }
    if (($keys -join ',') -cne 'results,schema,status' -or $Result.results -isnot [Array] -or
        $Result.results.Count -gt 100 -or
        ($Result.status -ceq 'ANSWER' -and $Result.results.Count -eq 0) -or
        ($Result.status -ceq 'ABSTAIN' -and $Result.results.Count -ne 0)) { throw 'runtime_refused' }
    if ([Text.Encoding]::UTF8.GetByteCount(($Result|ConvertTo-Json -Depth 8 -Compress)) -gt 32768) {
        throw 'runtime_refused'
    }
    $items=@()
    foreach ($item in $Result.results) {
        if ($item -isnot [pscustomobject]) { throw 'runtime_refused' }
        $names=[string[]]@($item.PSObject.Properties.Name)
        [Array]::Sort($names,[StringComparer]::Ordinal)
        if (($names -join ',') -cne 'canonical_event,doc_id_sha256,ref_kind,ref_sha256,stored_text' -or
            $item.stored_text -isnot [string] -or $item.stored_text -cnotmatch '^[A-Za-z0-9._: -]{1,320}\z' -or
            $item.ref_kind -isnot [string] -or $item.ref_kind -cne 'sha256_of_projected_metadata' -or
            $item.canonical_event -isnot [bool] -or $item.canonical_event -ne $false -or
            -not (Test-WdQ4Hash $item.ref_sha256) -or $item.ref_sha256.Length -ne 64 -or
            -not (Test-WdQ4Hash $item.doc_id_sha256) -or $item.doc_id_sha256.Length -ne 64) {
            throw 'runtime_refused'
        }
        $items+=@{ref_sha256=$item.ref_sha256;doc_id_sha256=$item.doc_id_sha256;
            stored_text=$item.stored_text;ref_kind='sha256_of_projected_metadata';canonical_event=$false}
    }
    return [pscustomobject]@{schema='wd.q4-agent-wrapper.v2';status=$Result.status;
        content_mode='sanitized_projected_metadata';scope='wd.q4-shared-projected-events.v1';
        memory_sha256=$Plan.ResolvedPins.memory_sha256;projection_sha256=$Plan.ResolvedPins.projection_sha256;
        policy_sha256=$Plan.ResolvedPins.policy_sha256;roster_sha256=$Plan.ResolvedPins.roster_sha256;results=$items}
}

function Invoke-WdQ4Query {
    param([string] $BundleRoot='', [string] $ExpectedBundleCommit='',
          [string] $ExpectedManifestSha256='', [double] $TimeoutSeconds=30,
          [IO.Stream] $InputStream)
    try {
        try {
            $plan=Get-WdQ4QueryPlan -BundleRoot $BundleRoot -ExpectedBundleCommit $ExpectedBundleCommit -ExpectedManifestSha256 $ExpectedManifestSha256 -TimeoutSeconds $TimeoutSeconds
        } catch {
            $reason=if($_.Exception.Message -ceq 'external_anchor_unavailable'){'external_anchor_unavailable'}elseif($_.Exception.Message -ceq 'roster_fleet_mismatch'){'roster_fleet_mismatch'}else{'binding_refused'}
            return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason=$reason;results=@()}
        }
        if (-not $plan.Enabled) {
            return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason='disabled';results=@()}
        }
        try { $query=Read-WdQ4QueryBytes -InputStream $InputStream }
        catch { return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason='invalid_query';results=@()} }
        $result=Invoke-WdQ4QueryTransport $plan $query
        if ($null -ne $plan.ResolvedPins) { return ConvertFrom-WdQ4ResolvedResult -Result $result -Plan $plan }
        return ConvertFrom-WdQ4Result -Result $result -ExpectedDescriptor $plan.Config.descriptor_sha256
    } catch {
        return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason='runtime_refused';results=@()}
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $answer=Invoke-WdQ4Query -BundleRoot $BundleRoot -ExpectedBundleCommit $ExpectedBundleCommit -ExpectedManifestSha256 $ExpectedManifestSha256 -TimeoutSeconds $TimeoutSeconds
    $answer|ConvertTo-Json -Depth 8 -Compress
    if ($answer.status -ceq 'REFUSED') { exit 2 }
    exit 0
}


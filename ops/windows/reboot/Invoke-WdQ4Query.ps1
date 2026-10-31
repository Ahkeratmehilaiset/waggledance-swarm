#requires -Version 5.1
<#
Synthetic candidate, default off. The trusted caller MUST independently pin this
wrapper and supply BundleRoot + expected commit + manifest hash. Parameters and
hash equality do not authenticate that caller or authorize memory/model access.
No WD_* environment value is an anchor. No install or environment mutation.
Output is hash-only, UNRESOLVED: no projection bytes/task-policy trust channel
is invented here. This is not agent-usable resolved Mind content or rollout.
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
    return [pscustomobject]@{Path=$full;Hash=$hash;Text=$utf8.GetString($bytes).TrimStart([char]0xFEFF)}
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
    $deployment = $manifest.Text | ConvertFrom-Json -ErrorAction Stop
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
        lock_timeout=$cfg.WD_Q4_LOCK_TIMEOUT;timeout=$TimeoutSeconds
    }
    if ((Read-WdQ4Snapshot $binding.descriptor 65536).Hash -cne $binding.descriptor_sha256.ToUpperInvariant() -or
        (Read-WdQ4Snapshot $binding.runtime_manifest).Hash -cne $binding.runtime_manifest_sha256.ToUpperInvariant() -or
        (Read-WdQ4Snapshot (Join-Path $root 'deployment-manifest.json')).Hash -cne $manifest.Hash) {
        throw 'binding_refused'
    }
    return [pscustomobject]@{Enabled=$true;CodeRoot=$integrity.CodeRoot;Config=$binding}
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
import json,sys
sys.path.insert(0,sys.argv[1])
try:
 from tools.wd_q4_agent_query import VerifiedQ4Config,query_with_verified_config
 config=VerifiedQ4Config(**json.loads(sys.argv[2]))
 result=query_with_verified_config(config,sys.stdin.buffer)
 result=dict(result,schema="wd.q4-agent-wrapper.v1")
 print(json.dumps(result,sort_keys=True),flush=True)
 sys.exit(2 if result["status"]=="REFUSED" else 0)
except Exception:
 print('{"schema":"wd.q4-agent-wrapper.v1","status":"REFUSED","reason":"runtime_refused","results":[]}',flush=True)
 sys.exit(2)
'@
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Plan.Config.interpreter
    $arguments = @('-I', '-S', '-B', '-c', $adapter, $Plan.CodeRoot,
                   ($Plan.Config | ConvertTo-Json -Depth 6 -Compress))
    $start.Arguments = (@($arguments | ForEach-Object {ConvertTo-WdQ4Argument ([string]$_)}) -join ' ')
    $start.UseShellExecute=$false
    $start.CreateNoWindow=$true
    $start.RedirectStandardInput=$true
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    $start.WorkingDirectory=$Plan.CodeRoot
    $start.EnvironmentVariables.Clear()
    foreach ($name in @('SYSTEMROOT','WINDIR')) {
        $value=[Environment]::GetEnvironmentVariable($name, 'Process')
        if ($value) { $start.EnvironmentVariables[$name]=$value }
    }
    $process=[Diagnostics.Process]::new()
    $process.StartInfo=$start
    $output=[IO.MemoryStream]::new()
    $started=$false
    try {
        if (-not $process.Start()) { throw 'runtime_refused' }
        $started=$true
        $clock=[Diagnostics.Stopwatch]::StartNew()
        $outBuffer=New-Object byte[] 8192
        $errBuffer=New-Object byte[] 8192
        $outTask=$process.StandardOutput.BaseStream.ReadAsync($outBuffer,0,8192)
        $errTask=$process.StandardError.BaseStream.ReadAsync($errBuffer,0,8192)
        $write=$process.StandardInput.BaseStream.WriteAsync($Query,0,$Query.Length)
        if (-not $write.Wait([int]($Plan.Config.timeout*1000))) { throw 'runtime_refused' }
        $process.StandardInput.BaseStream.Close()
        $total=0
        while ($null -ne $outTask -or $null -ne $errTask) {
            if ($clock.Elapsed.TotalSeconds -ge $Plan.Config.timeout) { throw 'runtime_refused' }
            foreach ($which in @('out','err')) {
                $task=if($which -ceq 'out'){$outTask}else{$errTask}
                if ($null -eq $task -or -not $task.IsCompleted) { continue }
                $count=$task.GetAwaiter().GetResult()
                $total += $count
                if ($total -gt 262144) { throw 'runtime_refused' }
                if ($which -ceq 'out') {
                    if ($count -eq 0) { $outTask=$null }
                    else {
                        $output.Write($outBuffer,0,$count)
                        $outTask=$process.StandardOutput.BaseStream.ReadAsync($outBuffer,0,8192)
                    }
                } else {
                    if ($count -eq 0) { $errTask=$null }
                    else { $errTask=$process.StandardError.BaseStream.ReadAsync($errBuffer,0,8192) }
                }
            }
            [Threading.Thread]::Sleep(5)
        }
        $remaining=[int][Math]::Max(0,($Plan.Config.timeout-$clock.Elapsed.TotalSeconds)*1000)
        if (-not $process.WaitForExit($remaining)) { throw 'runtime_refused' }
        $utf8=[Text.UTF8Encoding]::new($false,$true)
        $json=$utf8.GetString($output.ToArray()).Trim()
        # This protocol carries ASCII tokens/hashes, not arbitrary text. Reject
        # escapes before PS5 JSON can fold case/duplicate properties or arrays.
        if (-not $json.StartsWith('{') -or -not $json.EndsWith('}') -or
            $json -match '[^A-Za-z0-9_.,:{}\[\]"\s-]') { throw 'runtime_refused' }
        $keyMatches=[regex]::Matches($json,'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
        $keyNames=@($keyMatches|ForEach-Object {$_.Groups[1].Value})
        $rootKeys=@('schema','status','reason','scope','descriptor_sha256','memory_sha256','results')
        $itemKeys=@('ref_sha256','doc_id_sha256','ref_kind','canonical_event')
        foreach($key in $keyNames){
            if($rootKeys -cnotcontains $key -and $itemKeys -cnotcontains $key){throw 'runtime_refused'}
        }
        foreach($key in $rootKeys){
            if(@($keyNames|Where-Object {$_ -ceq $key}).Count -gt 1){throw 'runtime_refused'}
        }
        $data=$json|ConvertFrom-Json -ErrorAction Stop
        foreach($key in $itemKeys){
            if(@($keyNames|Where-Object {$_ -ceq $key}).Count -ne @($data.results).Count){throw 'runtime_refused'}
        }
        if ($data -isnot [pscustomobject] -or $data.schema -isnot [string] -or
            $data.schema -cne 'wd.q4-agent-wrapper.v1' -or $data.status -isnot [string] -or
            $data.status -cnotin @('ANSWER','ABSTAIN','DEGRADED','REFUSED') -or
            ($data.status -ceq 'REFUSED') -ne ($process.ExitCode -eq 2) -or
            $process.ExitCode -notin @(0,2)) { throw 'runtime_refused' }
        return $data
    } finally {
        if ($started) { try { if(-not $process.HasExited){$process.Kill()} } catch {} }
        $process.Dispose()
        $output.Dispose()
    }
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

function Invoke-WdQ4Query {
    param([string] $BundleRoot='', [string] $ExpectedBundleCommit='',
          [string] $ExpectedManifestSha256='', [double] $TimeoutSeconds=30,
          [IO.Stream] $InputStream)
    try {
        try {
            $plan=Get-WdQ4QueryPlan -BundleRoot $BundleRoot -ExpectedBundleCommit $ExpectedBundleCommit -ExpectedManifestSha256 $ExpectedManifestSha256 -TimeoutSeconds $TimeoutSeconds
        } catch {
            $reason=if($_.Exception.Message -ceq 'external_anchor_unavailable'){'external_anchor_unavailable'}else{'binding_refused'}
            return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason=$reason;results=@()}
        }
        if (-not $plan.Enabled) {
            return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason='disabled';results=@()}
        }
        try { $query=Read-WdQ4QueryBytes -InputStream $InputStream }
        catch { return [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='REFUSED';reason='invalid_query';results=@()} }
        $result=Invoke-WdQ4QueryTransport $plan $query
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


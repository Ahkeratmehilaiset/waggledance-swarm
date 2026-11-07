#requires -Version 5.1
<# Fixed on-demand continuation entry. Default OFF. No arbitrary executable or module selector.
Pins are caller-supplied, not authentication. Process-tree exit and same-user races are not proven. #>
[CmdletBinding(PositionalBinding=$false)]
param(
    [switch] $Run,
    [string] $BundleRoot='', [string] $ExpectedCommit='', [string] $ExpectedManifestSha256='',
    [string] $ConfigPath='', [string] $ExpectedConfigSha256='',
    [string] $RuntimePath='', [string] $ExpectedRuntimeSha256='',
    [string] $ScratchRoot='', [string] $ExpectedHostSha256='',
    [ValidateRange(1,1800)] [int] $TimeoutSeconds=1800
)
$ErrorActionPreference='Stop'
if (-not $Run) { [Console]::Out.WriteLine('{"status":"OFF"}'); return }
$started=$false
try {
    if ($PSVersionTable.PSEdition -ceq 'Desktop') {
        Import-Module -Name (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
    }
    foreach ($pin in @($ExpectedManifestSha256,$ExpectedConfigSha256,$ExpectedRuntimeSha256,$ExpectedHostSha256)) {
        if ($pin -cnotmatch '^[0-9a-f]{64}$') { throw 'pin' }
    }
    if ($ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'commit' }
    function CheckedPath([string]$Path, [bool]$Directory=$false) {
        if ($Path -cnotmatch '^C:\\' -or $Path.Contains('%') -or $Path -match '(^|\\)\.\.?($|\\)') { throw 'path' }
        $full=[IO.Path]::GetFullPath($Path)
        $item=Get-Item -LiteralPath $full -Force -ErrorAction Stop
        if ([bool]$item.PSIsContainer -ne $Directory) { throw 'type' }
        $walk=$item
        while ($null -ne $walk) {
            if (($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'link' }
            if ($walk.PSIsContainer) { $walk=$walk.Parent } else { $walk=$walk.Directory }
        }
        return $full
    }
    function HashMatches([string]$Path,[string]$Pin) {
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            $hasher=[Security.Cryptography.SHA256]::Create()
            try { $actual=[BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-','') }
            finally { $hasher.Dispose() }
        } finally { $stream.Dispose() }
        return $actual -ceq $Pin.ToUpperInvariant()
    }
    $bundle=CheckedPath $BundleRoot $true
    $config=CheckedPath $ConfigPath
    $runtime=CheckedPath $RuntimePath
    $manifest=CheckedPath (Join-Path $bundle 'deployment-manifest.json')
    $bootstrap=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_continuation_runtime_bootstrap.py')
    $cli=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_production_continuation_cli.py')
    $helper=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_live_update_bootstrap.py')
    $jobHelper=CheckedPath (Join-Path $bundle 'WdQ4ProcessJob.ps1')
    $worker=CheckedPath (Join-Path $bundle 'Invoke-WdQ4ContinuationWorker.ps1')
    if (-not (HashMatches $manifest $ExpectedManifestSha256) -or -not (HashMatches $config $ExpectedConfigSha256) -or
        -not (HashMatches $runtime $ExpectedRuntimeSha256)) { throw 'pin' }
    $manifestData=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $runtimeData=Get-Content -LiteralPath $runtime -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $configData=Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    if ([int]$manifestData.schema_version -ne 1 -or [string]$manifestData.source_commit -cne $ExpectedCommit -or
        [string]$runtimeData.schema -cne 'wd.q4-genesis-runtime.v1' -or
        [string]$configData.schema -cnotin @('wd.q4-continuation-cli.v1','wd.q4-continuation-cli.v2','wd.q4-continuation-cli.v3') -or
        [string]$runtimeData.manifest_sha256 -cne $ExpectedManifestSha256 -or
        [string]$configData.manifest_sha256 -cne $ExpectedManifestSha256 -or
        -not [IO.Path]::GetFullPath([string]$runtimeData.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFullPath([string]$configData.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase)) { throw 'binding' }
    $v3=[string]$configData.schema -ceq 'wd.q4-continuation-cli.v3'
    $v2=[string]$configData.schema -ceq 'wd.q4-continuation-cli.v2' -or $v3
    if ($v3 -and ($null -eq $configData.PSObject.Properties['watermark_previous_record_sha256'] -or
        ($null -ne $configData.watermark_previous_record_sha256 -and
         [string]$configData.watermark_previous_record_sha256 -cnotmatch '^[0-9a-f]{64}$'))) { throw 'pin' }
    if ($v2) {
        foreach ($value in @($configData.cutoff_utc,$configData.previous_cutoff_utc)) {
            if ($value -isnot [string] -or $value -cnotmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$') { throw 'cutoff' }
        }
        $format="yyyy-MM-dd'T'HH:mm:ss'Z'"
        $newCutoff=[DateTime]::ParseExact($configData.cutoff_utc,$format,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::AssumeUniversal)
        $previousCutoff=[DateTime]::ParseExact($configData.previous_cutoff_utc,$format,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::AssumeUniversal)
        if ($newCutoff -lt $previousCutoff) { throw 'cutoff' }
    }
    foreach ($entry in @(@('tools-bootstrap/tools/wd_q4_continuation_runtime_bootstrap.py',$bootstrap),
                          @('tools-bootstrap/tools/wd_q4_production_continuation_cli.py',$cli),
                          @('tools-bootstrap/tools/wd_q4_live_update_bootstrap.py',$helper),
                          @('WdQ4ProcessJob.ps1',$jobHelper), @('Invoke-WdQ4ContinuationWorker.ps1',$worker))) {
        $pin=[string]$manifestData.files.PSObject.Properties[$entry[0]].Value
        if ($pin -cnotmatch '^[0-9A-Fa-f]{64}$' -or -not (HashMatches $entry[1] $pin)) { throw 'code' }
    }
    $python=CheckedPath ([string]$runtimeData.interpreter_path)
    $pythonPin=[string]$runtimeData.interpreter_sha256
    if ($pythonPin -cnotmatch '^[0-9a-f]{64}$' -or -not (HashMatches $python $pythonPin)) { throw 'python' }
    $hostPath=CheckedPath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    if (-not (HashMatches $hostPath $ExpectedHostSha256)) { throw 'host' }
    $scratch=CheckedPath $ScratchRoot $true
    # Final-name identity defeats 8.3 aliases in addition to the reparse walk.
    if ($null -ne ('WdQ4ContinuationDirectoryV1' -as [type])) { throw 'type' }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class WdQ4ContinuationDirectoryV1 {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern SafeFileHandle CreateFile(string n,uint a,uint s,IntPtr p,uint d,uint f,IntPtr t);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder p,uint n,uint flags);
  public static string Final(string path) { using(var h=CreateFile(path,0,7,IntPtr.Zero,3,0x02000000,IntPtr.Zero)) {
    if(h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
    var b=new StringBuilder(32768); uint n=GetFinalPathNameByHandle(h,b,(uint)b.Capacity,0);
    if(n==0 || n>=b.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error()); return b.ToString().TrimEnd('\\'); } }
}
'@ -ErrorAction Stop
    # Only the independently hash-bound route may identify protected paths.
    $tuple=CheckedPath ([string]$configData.tuple_path)
    $tuplePin=[string]$configData.tuple_sha256
    if ($tuplePin -cnotmatch '^[0-9a-f]{64}$' -or -not (HashMatches $tuple $tuplePin) -or
        (Get-Item -LiteralPath $tuple).Length -gt 65536) { throw 'tuple' }
    $tupleData=Get-Content -LiteralPath $tuple -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $routeBytes=[Convert]::FromBase64String([string]$tupleData.route.bytes_b64)
    if ($routeBytes.Length -eq 0 -or $routeBytes.Length -gt 65536 -or
        [string]$tupleData.route.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'route' }
    $routeHash=[Security.Cryptography.SHA256]::Create()
    try { $routePin=[BitConverter]::ToString($routeHash.ComputeHash($routeBytes)).Replace('-','').ToLowerInvariant() }
    finally { $routeHash.Dispose() }
    if ($routePin -cne [string]$tupleData.route.sha256) { throw 'route' }
    $routeText=[Text.UTF8Encoding]::new($false,$true).GetString($routeBytes)
    $routeData=$routeText | ConvertFrom-Json -ErrorAction Stop
    # Resolve actual Windows names so 8.3 aliases cannot put private inputs
    # inside the checkpoint tree that is published to readers.
    $checkpoint=CheckedPath ([string]$routeData.checkpoint_root) $true
    $tupleFinal=[WdQ4ContinuationDirectoryV1]::Final($tuple)
    $checkpointFinal=[WdQ4ContinuationDirectoryV1]::Final($checkpoint)
    if ($tupleFinal.Equals($checkpointFinal,[StringComparison]::OrdinalIgnoreCase) -or
        $tupleFinal.StartsWith($checkpointFinal+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'private tuple in published tree' }
    $scratchFinal=[WdQ4ContinuationDirectoryV1]::Final($scratch)
    $protected=@($bundle,(Split-Path -Parent $python),(Split-Path -Parent $hostPath),
        (Split-Path -Parent $config),(Split-Path -Parent $runtime),
        (Split-Path -Parent $tuple), (Split-Path -Parent ([string]$configData.policy_path)),
        (Split-Path -Parent ([string]$configData.public_policy_path)),
        (Split-Path -Parent ([string]$routeData.source)),
        (Split-Path -Parent ([string]$routeData.generation_path)),
        [string]$routeData.checkpoint_root,[string]$routeData.private_state_root,
        [string]$routeData.trusted_model_root,[string]$runtimeData.runtime_root)
    foreach ($root in $protected) {
        $full=CheckedPath $root $true
        $other=[WdQ4ContinuationDirectoryV1]::Final($full)
        if ($scratchFinal.Equals($other,[StringComparison]::OrdinalIgnoreCase) -or
            $scratchFinal.StartsWith($other+'\',[StringComparison]::OrdinalIgnoreCase) -or
            $other.StartsWith($scratchFinal+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'scratch' }
    }
    . $jobHelper
    $jobArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$worker,
        '-Manifest',$manifest,'-ManifestSha256',$ExpectedManifestSha256,'-Bootstrap',$bootstrap,
        '-BootstrapSha256',([string]$manifestData.files.'tools-bootstrap/tools/wd_q4_continuation_runtime_bootstrap.py').ToLowerInvariant(),
        '-Python',$python,'-PythonSha256',$pythonPin,'-Config',$config,'-ConfigSha256',$ExpectedConfigSha256,
        '-Runtime',$runtime,'-RuntimeSha256',$ExpectedRuntimeSha256)
    $started=$true
    $job=Invoke-WdQ4ProcessJob -Executable $hostPath -Arguments $jobArgs -WorkingDirectory $bundle `
        -ScratchRoot $scratch -InputBytes ([byte[]]@()) -TimeoutSeconds $TimeoutSeconds -MaxOutputBytes 4096 -DetailedOutcome
    if ([string]$job.Outcome -cne 'completed' -or [string]$job.Cleanup -cne 'confirmed' -or
        $null -eq $job.ExitCode -or $null -eq $job.Stdout) { throw 'job' }
    $code=[int]$job.ExitCode
    $text=[Text.UTF8Encoding]::new($false,$true).GetString([byte[]]$job.Stdout)
    $lines=@($text.TrimEnd("`r","`n"))
    $outputLimit=if ($v3) { 1024 } else { 512 }
    if ($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt $outputLimit) { throw 'output' }
    $answer=[string]$lines[0] | ConvertFrom-Json -ErrorAction Stop
    $keys=@($answer.PSObject.Properties.Name)
    $allowed=@('status','pointer_sha256','runner_receipt_sha256')
    if ($v2) { $allowed+=@('cutoff_utc','previous_cutoff_utc','route_sha256','cutoff_monotonicity') }
    if ($v3) { $allowed+='watermark_record_sha256' }
    if ($keys.Count -lt 1 -or $keys.Count -gt $allowed.Count -or
        @($keys | Where-Object { $_ -cnotin $allowed }).Count -ne 0 -or
        [string]$answer.status -cnotin @('partial_ready_prefix','caught_up_to_cutoff','DEFERRED','REFUSED','UNKNOWN')) { throw 'output' }
    foreach ($name in @('pointer_sha256','runner_receipt_sha256')) {
        if ($keys -ccontains $name -and [string]$answer.$name -cnotmatch '^[0-9a-f]{64}$') { throw 'output' }
    }
    $success=[string]$answer.status -cin @('partial_ready_prefix','caught_up_to_cutoff','DEFERRED')
    $expectedKeys=3
    if ($v2) { $expectedKeys=7 }
    if ($v3) { $expectedKeys=8 }
    if (($success -and ($code -ne 0 -or $keys.Count -ne $expectedKeys -or
            $keys -cnotcontains 'pointer_sha256' -or $keys -cnotcontains 'runner_receipt_sha256')) -or
        (-not $success -and ($keys.Count -ne 1 -or
            -not (($code -eq 2 -and [string]$answer.status -ceq 'REFUSED') -or
                  ($code -eq 3 -and [string]$answer.status -ceq 'UNKNOWN'))))) { throw 'output' }
    if ($success -and $v2) {
        if ($keys -cnotcontains 'cutoff_utc' -or $keys -cnotcontains 'previous_cutoff_utc' -or
            $keys -cnotcontains 'route_sha256' -or $keys -cnotcontains 'cutoff_monotonicity' -or
            [string]$answer.cutoff_utc -cne [string]$configData.cutoff_utc -or
            [string]$answer.previous_cutoff_utc -cne [string]$configData.previous_cutoff_utc -or
            [string]$answer.route_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            [string]$answer.cutoff_monotonicity -cne $(if ($v3) { 'COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN' } else { 'CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN' })) { throw 'output' }
    }
    if ($success -and $v3) {
        $recordPin=[string]$answer.watermark_record_sha256
        if ($recordPin -cnotmatch '^[0-9a-f]{64}$') { throw 'record' }
        $private=CheckedPath ([string]$routeData.private_state_root) $true
        $head=CheckedPath (Join-Path $private 'q4-cutoff-current.json')
        $record=CheckedPath (Join-Path $private ('q4-cutoff-record-'+$recordPin+'.json'))
        $activePointer=CheckedPath (Join-Path $checkpoint 'q4-latest.json')
        $expectedHead='{"record_sha256":"'+$recordPin+'","schema":"wd.q4-cutoff-head.v1"}'
        $headHasher=[Security.Cryptography.SHA256]::Create()
        try { $headPin=[BitConverter]::ToString($headHasher.ComputeHash([Text.Encoding]::ASCII.GetBytes($expectedHead))).Replace('-','').ToLowerInvariant() }
        finally { $headHasher.Dispose() }
        if ((Get-Item -LiteralPath $head).Length -gt 4096 -or (Get-Item -LiteralPath $record).Length -gt 8192 -or
            (Get-Item -LiteralPath $activePointer).Length -gt 16384 -or
            -not (HashMatches $head $headPin) -or -not (HashMatches $record $recordPin) -or
            -not (HashMatches $activePointer ([string]$answer.pointer_sha256)) -or
            (Test-Path -LiteralPath (Join-Path $private 'q4-cutoff.pending.json'))) { throw 'record' }
        $headData=Get-Content -LiteralPath $head -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $recordData=Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $recordKeys=@($recordData.PSObject.Properties.Name)
        $expectedRecordKeys=@('schema','previous_record_sha256','cutoff_utc','attempt','route_sha256','runner_receipt_sha256','pointer_sha256')
        if (@($headData.PSObject.Properties.Name).Count -ne 2 -or [string]$headData.schema -cne 'wd.q4-cutoff-head.v1' -or
            [string]$headData.record_sha256 -cne $recordPin -or $recordKeys.Count -ne 7 -or
            @($recordKeys | Where-Object { $_ -cnotin $expectedRecordKeys }).Count -ne 0 -or
            [string]$recordData.schema -cne 'wd.q4-cutoff-record.v1' -or
            $recordData.previous_record_sha256 -cne $configData.watermark_previous_record_sha256 -or
            [string]$recordData.cutoff_utc -cne [string]$configData.cutoff_utc -or
            [string]$recordData.attempt -cnotmatch '^[0-9a-f]{64}$' -or
            [string]$recordData.route_sha256 -cne [string]$answer.route_sha256 -or
            [string]$recordData.runner_receipt_sha256 -cne [string]$answer.runner_receipt_sha256 -or
            [string]$recordData.pointer_sha256 -cne [string]$answer.pointer_sha256 -or
            -not (HashMatches $head $headPin) -or -not (HashMatches $record $recordPin) -or
            -not (HashMatches $activePointer ([string]$answer.pointer_sha256))) { throw 'record' }
    }
    [Console]::Out.WriteLine(($answer | ConvertTo-Json -Compress))
    exit $code
} catch {
    if ($started) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
}

#requires -Version 5.1
<# Fixed on-demand orchestrator entry. Default OFF. No arbitrary executable or module selector.
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
    $bootstrap=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_continuation_orchestrator_bootstrap.py')
    $cli=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_continuation_orchestrator_cli.py')
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
        [string]$configData.schema -cne 'wd.q4-continuation-orchestrator-cli.v1' -or
        [string]$runtimeData.manifest_sha256 -cne $ExpectedManifestSha256 -or
        [string]$configData.expected_manifest_sha256 -cne $ExpectedManifestSha256 -or
        [string]$configData.expected_commit -cne $ExpectedCommit -or
        [string]$configData.runtime_sha256 -cne $ExpectedRuntimeSha256 -or
        -not [IO.Path]::GetFullPath([string]$configData.runtime_path).Equals($runtime,[StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFullPath([string]$runtimeData.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFullPath([string]$configData.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase)) { throw 'binding' }
    $v3=$true; $v2=$true
    if ($configData.cutoff_utc -isnot [string] -or $configData.cutoff_utc -cnotmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' -or
        $configData.timeout_seconds -isnot [int] -or $configData.timeout_seconds -lt 1 -or
        $TimeoutSeconds -lt ($configData.timeout_seconds + 120)) { throw 'cutoff or timeout' }
    $null=[DateTime]::ParseExact($configData.cutoff_utc,"yyyy-MM-dd'T'HH:mm:ss'Z'",[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::AssumeUniversal)
    if ([string]$configData.expected_host_sha256 -cne $ExpectedHostSha256 -or
        -not [IO.Path]::GetFullPath([string]$configData.scratch_root).Equals([IO.Path]::GetFullPath($ScratchRoot),[StringComparison]::OrdinalIgnoreCase)) { throw 'binding' }
    $template=CheckedPath ([string]$configData.template_path)
    if ([string]$configData.template_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        -not (HashMatches $template ([string]$configData.template_sha256)) -or (Get-Item -LiteralPath $template).Length -gt 65536) { throw 'template' }
    $templateData=Get-Content -LiteralPath $template -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    if ([string]$templateData.schema -cne 'wd.q4-continuation-cli.v3' -or
        [string]$templateData.manifest_sha256 -cne $ExpectedManifestSha256 -or
        -not [IO.Path]::GetFullPath([string]$templateData.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase)) { throw 'template' }
    foreach ($entry in @(@('tools-bootstrap/tools/wd_q4_continuation_orchestrator_bootstrap.py',$bootstrap),
                          @('tools-bootstrap/tools/wd_q4_continuation_orchestrator_cli.py',$cli),
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
    $tuple=CheckedPath ([string]$templateData.tuple_path)
    $tuplePin=[string]$templateData.tuple_sha256
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
        (Split-Path -Parent $tuple), (Split-Path -Parent ([string]$templateData.policy_path)),
        (Split-Path -Parent ([string]$templateData.public_policy_path)),
        (Split-Path -Parent $template),
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
    # Bind the output to the pre-job watermark rather than trusting a result's
    # self-declared predecessor. A concurrent change fails closed.
    $private=CheckedPath ([string]$routeData.private_state_root) $true
    $preHead=CheckedPath (Join-Path $private 'q4-cutoff-current.json')
    if ((Get-Item -LiteralPath $preHead).Length -gt 4096) { throw 'head' }
    $preHeadData=Get-Content -LiteralPath $preHead -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $previousRecordPin=[string]$preHeadData.record_sha256
    if (@($preHeadData.PSObject.Properties.Name).Count -ne 2 -or
        [string]$preHeadData.schema -cne 'wd.q4-cutoff-head.v1' -or
        $previousRecordPin -cnotmatch '^[0-9a-f]{64}$') { throw 'head' }
    $previousRecord=CheckedPath (Join-Path $private ('q4-cutoff-record-'+$previousRecordPin+'.json'))
    if ((Get-Item -LiteralPath $previousRecord).Length -gt 8192 -or
        -not (HashMatches $previousRecord $previousRecordPin)) { throw 'head' }
    $previousData=Get-Content -LiteralPath $previousRecord -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    if ([string]$previousData.schema -cne 'wd.q4-cutoff-record.v1' -or
        [string]$previousData.pointer_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        -not (HashMatches (CheckedPath (Join-Path $checkpoint 'q4-latest.json')) ([string]$previousData.pointer_sha256))) { throw 'head' }
    # The outer process owns a separate durable marker: the inner writer can
    # finish and release its marker while this Job still has an unknown result.
    $outerPending=Join-Path $private 'q4-continuation-orchestrator-job.pending.json'
    if (Test-Path -LiteralPath $outerPending) { $started=$true; throw 'pending' }
    $markerBytes=[Text.Encoding]::UTF8.GetBytes(([ordered]@{schema='wd.q4-orchestrator-job-pending.v1';
        config_sha256=$ExpectedConfigSha256; previous_record_sha256=$previousRecordPin} | ConvertTo-Json -Compress))
    $hash=[Security.Cryptography.SHA256]::Create()
    try { $markerPin=[BitConverter]::ToString($hash.ComputeHash($markerBytes)).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
    $started=$true
    $markerStream=[IO.File]::Open($outerPending,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $markerStream.Write($markerBytes,0,$markerBytes.Length); $markerStream.Flush($true) }
    finally { $markerStream.Dispose() }
    if (-not (HashMatches (CheckedPath $outerPending) $markerPin)) { throw 'pending' }
    . $jobHelper
    $jobArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$worker,
        '-Manifest',$manifest,'-ManifestSha256',$ExpectedManifestSha256,'-Bootstrap',$bootstrap,
        '-BootstrapSha256',([string]$manifestData.files.'tools-bootstrap/tools/wd_q4_continuation_orchestrator_bootstrap.py').ToLowerInvariant(),
        '-Python',$python,'-PythonSha256',$pythonPin,'-Config',$config,'-ConfigSha256',$ExpectedConfigSha256,
        '-Runtime',$runtime,'-RuntimeSha256',$ExpectedRuntimeSha256)
    $started=$true
    $job=Invoke-WdQ4ProcessJob -Executable $hostPath -Arguments $jobArgs -WorkingDirectory $bundle `
        -ScratchRoot $scratch -InputBytes ([byte[]]@()) -TimeoutSeconds $TimeoutSeconds -MaxOutputBytes 4096 -DetailedOutcome
    if ([string]$job.Outcome -cne 'completed' -or [string]$job.Cleanup -cne 'confirmed' -or
        $null -eq $job.ExitCode -or $null -eq $job.Stdout) { throw 'job' }
    $code=[int]$job.ExitCode
    $text=[Text.UTF8Encoding]::new($false,$true).GetString([byte[]]$job.Stdout)
    $lines=@($text.TrimEnd("`r","`n") -split '\r\n|\r|\n')
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
            [string]$answer.previous_cutoff_utc -cne [string]$previousData.cutoff_utc -or
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
            [string]$recordData.previous_record_sha256 -cne $previousRecordPin -or
            [string]$recordData.cutoff_utc -cne [string]$configData.cutoff_utc -or
            [string]$recordData.attempt -cnotmatch '^[0-9a-f]{64}$' -or
            [string]$recordData.route_sha256 -cne [string]$answer.route_sha256 -or
            [string]$recordData.runner_receipt_sha256 -cne [string]$answer.runner_receipt_sha256 -or
            [string]$recordData.pointer_sha256 -cne [string]$answer.pointer_sha256 -or
            -not (HashMatches $head $headPin) -or -not (HashMatches $record $recordPin) -or
            -not (HashMatches $activePointer ([string]$answer.pointer_sha256))) { throw 'record' }
    }
    if (-not $success) { throw 'outcome not confirmed' }
    # Persist a bound success receipt before removing the marker. If the host
    # dies before producing stdout, the recorded evidence remains available for
    # explicit reconciliation; it is not a proof of hostile-writer isolation.
    $receiptPath=Join-Path $private ('q4-orchestrator-job-result-'+$ExpectedConfigSha256+'-'+$previousRecordPin+'.json')
    $receiptBytes=[Text.Encoding]::UTF8.GetBytes(([ordered]@{schema='wd.q4-orchestrator-job-result.v1';
        config_sha256=$ExpectedConfigSha256; previous_record_sha256=$previousRecordPin; answer=$answer} | ConvertTo-Json -Depth 8 -Compress))
    $hash=[Security.Cryptography.SHA256]::Create()
    try { $receiptPin=[BitConverter]::ToString($hash.ComputeHash($receiptBytes)).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
    $receiptStream=[IO.File]::Open($receiptPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $receiptStream.Write($receiptBytes,0,$receiptBytes.Length); $receiptStream.Flush($true) }
    finally { $receiptStream.Dispose() }
    if (-not (HashMatches (CheckedPath $receiptPath) $receiptPin) -or
        -not (HashMatches (CheckedPath $outerPending) $markerPin)) { throw 'receipt' }
    Remove-Item -LiteralPath $outerPending -ErrorAction Stop
    [Console]::Out.WriteLine(($answer | ConvertTo-Json -Compress))
    exit $code
} catch {
    if ($started) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
}

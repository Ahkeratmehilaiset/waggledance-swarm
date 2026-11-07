#requires -Version 5.1
<# Fixed on-demand adoption recovery entry. Default OFF. No arbitrary executable or module selector.
Pins are caller-supplied, not authentication. Process-tree exit and same-user races are not proven. #>
[CmdletBinding(PositionalBinding=$false)]
param(
    [switch] $Run,
    [string] $BundleRoot='', [string] $ExpectedCommit='', [string] $ExpectedManifestSha256='',
    [string] $ConfigPath='', [string] $ExpectedConfigSha256='',
    [string] $RuntimePath='', [string] $ExpectedRuntimeSha256='',
    [string] $ScratchRoot='', [string] $ExpectedHostSha256='', [string] $FinalizeAttempt='',
    [ValidateRange(1,1800)] [int] $TimeoutSeconds=1800
)
$ErrorActionPreference='Stop'
if (-not $Run) { [Console]::Out.WriteLine('{"status":"OFF"}'); return }
$started=$false
$pendingStream=$null
try {
    if ($PSVersionTable.PSEdition -ceq 'Desktop') {
        Import-Module -Name (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
    }
    foreach ($pin in @($ExpectedManifestSha256,$ExpectedConfigSha256,$ExpectedRuntimeSha256,$ExpectedHostSha256)) {
        if ($pin -cnotmatch '^[0-9a-f]{64}$') { throw 'pin' }
    }
    if ($ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'commit' }
    if ($FinalizeAttempt -and $FinalizeAttempt -cnotmatch '^[0-9a-f]{64}$') { throw 'attempt' }
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
    $bootstrap=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_continuation_adopt_recovery_bootstrap.py')
    $cli=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_continuation_adopt_recovery_cli.py')
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
        [string]$configData.schema -cne 'wd.q4-adopt-recovery-cli.v1' -or
        [string]$runtimeData.manifest_sha256 -cne $ExpectedManifestSha256 -or
        [string]$configData.manifest_sha256 -cne $ExpectedManifestSha256 -or
        -not [IO.Path]::GetFullPath([string]$runtimeData.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFullPath([string]$configData.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase)) { throw 'binding' }
    foreach ($entry in @(@('tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_bootstrap.py',$bootstrap),
                          @('tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py',$cli),
                          @('tools-bootstrap/tools/wd_q4_live_update_bootstrap.py',$helper),
                          @('WdQ4ProcessJob.ps1',$jobHelper), @('Invoke-WdQ4ContinuationWorker.ps1',$worker),
                          @('Invoke-WdQ4AdoptRecovery.ps1',(CheckedPath $PSCommandPath)))) {
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
    $receipt=CheckedPath ([string]$configData.receipt_path)
    if (-not (HashMatches $receipt ([string]$configData.receipt_sha256))) { throw 'receipt' }
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
    $receiptFinal=[WdQ4ContinuationDirectoryV1]::Final($receipt)
    if ($receiptFinal.Equals($checkpointFinal,[StringComparison]::OrdinalIgnoreCase) -or $receiptFinal.StartsWith($checkpointFinal+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'private receipt in published tree' }
    $scratchFinal=[WdQ4ContinuationDirectoryV1]::Final($scratch)
    $protected=@($bundle,(Split-Path -Parent $python),(Split-Path -Parent $hostPath),
        (Split-Path -Parent $config),(Split-Path -Parent $runtime),
        (Split-Path -Parent $tuple), (Split-Path -Parent $receipt),
        (Split-Path -Parent ([string]$configData.baseline_path)),
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
    # Retain an exclusive durable attempt until a confirmed, validated result.
    $private=CheckedPath ([string]$routeData.private_state_root) $true
    $privateFinal=[WdQ4ContinuationDirectoryV1]::Final($private)
    foreach ($protectedRoot in @($bundle,$checkpoint,[string]$runtimeData.runtime_root,
            [string]$routeData.trusted_model_root,(Split-Path -Parent ([string]$routeData.source)),
            (Split-Path -Parent ([string]$routeData.generation_path)))) {
        $protectedFinal=[WdQ4ContinuationDirectoryV1]::Final((CheckedPath $protectedRoot $true))
        if ($privateFinal.Equals($protectedFinal,[StringComparison]::OrdinalIgnoreCase) -or
            $privateFinal.StartsWith($protectedFinal+'\',[StringComparison]::OrdinalIgnoreCase) -or
            $protectedFinal.StartsWith($privateFinal+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'private root' }
    }
    $pendingPath=Join-Path $private 'q4-adopt-recovery-job.pending.json'
    if ($null -ne ('WdQ4AdoptPendingV1' -as [type])) { throw 'type' }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class WdQ4AdoptPendingV1 {
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
  static extern SafeFileHandle CreateFile(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
  static extern uint GetFileAttributes(string path);
  public static bool Exists(string path) {
    uint attributes=GetFileAttributes(path);
    if(attributes!=0xFFFFFFFF) return true;
    int e=Marshal.GetLastWin32Error();
    if(e==2 || e==3) return false;
    throw new Win32Exception(e);
  }
  [DllImport("kernel32.dll",SetLastError=true)]
  static extern bool SetFileInformationByHandle(SafeFileHandle h,int info,ref byte value,uint size);
  public static SafeFileHandle OpenExisting(string path) {
    var h=CreateFile(path,0x80010000,1,IntPtr.Zero,3,0x00200080,IntPtr.Zero);
    if(h.IsInvalid) { int e=Marshal.GetLastWin32Error(); h.Dispose(); throw new Win32Exception(e); }
    return h;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct FileId { public ulong volume; [MarshalAs(UnmanagedType.ByValArray,SizeConst=16)] public byte[] identifier; }
  [DllImport("kernel32.dll",SetLastError=true)]
  static extern bool GetFileInformationByHandleEx(SafeFileHandle h,int info,out FileId id,uint size);
  public static string Identity(SafeFileHandle h) {
    FileId id;
    if(!GetFileInformationByHandleEx(h,18,out id,(uint)Marshal.SizeOf(typeof(FileId)))) throw new Win32Exception(Marshal.GetLastWin32Error());
    return id.volume.ToString("x16")+BitConverter.ToString(id.identifier).Replace("-","").ToLowerInvariant();
  }
  public static void DeleteOwned(SafeFileHandle h) {
    byte yes=1;
    if(!SetFileInformationByHandle(h,4,ref yes,1)) throw new Win32Exception(Marshal.GetLastWin32Error());
  }
}
'@ -ErrorAction Stop
    if (-not $FinalizeAttempt) {
    . $jobHelper
    $jobArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$worker,
        '-Manifest',$manifest,'-ManifestSha256',$ExpectedManifestSha256,'-Bootstrap',$bootstrap,
        '-BootstrapSha256',([string]$manifestData.files.'tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_bootstrap.py').ToLowerInvariant(),
        '-Python',$python,'-PythonSha256',$pythonPin,'-Config',$config,'-ConfigSha256',$ExpectedConfigSha256,
        '-Runtime',$runtime,'-RuntimeSha256',$ExpectedRuntimeSha256,
        '-ExpectedHostSha256',$ExpectedHostSha256)
    $started=$true
    $job=Invoke-WdQ4ProcessJob -Executable $hostPath -Arguments $jobArgs -WorkingDirectory $bundle `
        -ScratchRoot $scratch -InputBytes ([byte[]]@()) -TimeoutSeconds $TimeoutSeconds -MaxOutputBytes 4096 -DetailedOutcome
    if ([string]$job.Outcome -cne 'completed' -or [string]$job.Cleanup -cne 'confirmed' -or
        $null -eq $job.ExitCode -or $null -eq $job.Stdout) { throw 'job' }
    $code=[int]$job.ExitCode
    $text=[Text.UTF8Encoding]::new($false,$true).GetString([byte[]]$job.Stdout)
    $lines=@($text.TrimEnd("`r","`n"))
    if ($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 256) { throw 'output' }
    $answer=[string]$lines[0] | ConvertFrom-Json -ErrorAction Stop
    } else {
        # Explicit continuation of an already drained Job; never start a child.
        $started=$true
        $priorPath=Join-Path $private ('q4-adopt-recovery-job-result-'+$FinalizeAttempt+'.json')
        [void](CheckedPath $priorPath)
        $priorBytes=[IO.File]::ReadAllBytes($priorPath)
        if ($priorBytes.Length -eq 0 -or $priorBytes.Length -gt 65536) { throw 'receipt' }
        $prior=[Text.Encoding]::ASCII.GetString($priorBytes) | ConvertFrom-Json -ErrorAction Stop
        if ([string]$prior.attempt -cne $FinalizeAttempt -or [string]$prior.job_outcome -cne 'completed' -or
            [string]$prior.job_cleanup -cne 'confirmed' -or [int]$prior.exit_code -ne 0) { throw 'receipt' }
        $code=0
        $answer=[PSCustomObject]@{ status=[string]$prior.status; attempt=[string]$prior.attempt;
            marker_id=[string]$prior.marker_id; marker_sha256=[string]$prior.marker_sha256 }
    }
    $keys=@($answer.PSObject.Properties.Name)
    if ($code -ne 0 -or $keys.Count -ne 4 -or
        @('status','attempt','marker_id','marker_sha256').Where({ $_ -cnotin $keys }).Count -ne 0 -or
        [string]$answer.status -cnotin @('ADOPTED','NOT_ADOPTED','NOT_NEEDED') -or
        [string]$answer.attempt -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$answer.marker_id -cnotmatch '^[0-9a-f]{48}$' -or
        [string]$answer.marker_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'uncertain result' }
    $attempt=[string]$answer.attempt
    $privateLockPath=Join-Path $private 'prefix-private-state.lock'
    $checkpointLockPath=Join-Path $checkpoint 'q4-checkpoint.lock'
    $privateLock=$null; $checkpointLock=$null; $privateHeld=$false; $checkpointHeld=$false
    $ownerLocks=[System.Collections.Generic.List[IO.FileStream]]::new()
    try {
        # Adoption and recovery take these owner slots in this exact order.
        # They cover the live writer after its short private/checkpoint window.
        foreach ($name in @('q4-continuation-orchestrator.lock','q4-live-update.lock','q4-cutoff-owner.lock')) {
            $ownerPath=Join-Path $private $name
            if ([WdQ4AdoptPendingV1]::Exists($ownerPath)) { [void](CheckedPath $ownerPath) }
            $owner=[IO.File]::Open($ownerPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
            try {
                [void](CheckedPath $ownerPath)
                $owner.Lock(0,1)
                $ownerLocks.Add($owner)
            } catch { $owner.Dispose(); throw }
        }
        if ([WdQ4AdoptPendingV1]::Exists($privateLockPath)) { [void](CheckedPath $privateLockPath) }
        $privateLock=[IO.File]::Open($privateLockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
        [void](CheckedPath $privateLockPath)
        $privateLock.Lock(0,1); $privateHeld=$true
        if ([WdQ4AdoptPendingV1]::Exists($checkpointLockPath)) { [void](CheckedPath $checkpointLockPath) }
        $checkpointLock=[IO.File]::Open($checkpointLockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
        [void](CheckedPath $checkpointLockPath)
        $checkpointLock.Lock(0,1); $checkpointHeld=$true
        # Admission and CREATE_NEW share exactly the Python private->checkpoint
        # byte-lock window. The adoption pending is the subject of recovery and
        # is therefore allowed; all competing reservations remain UNKNOWN.
        $competing=@((Join-Path $private 'prefix-private-state.pending'),
            (Join-Path $private '.wd-q4-status-prefix-runner.pending'),
            (Join-Path $checkpoint '.wd-q4-status-prefix-runner.pending'),
            (Join-Path $private 'q4-cutoff.pending.json'),
            (Join-Path $private 'q4-continuation-orchestrator.pending.json'),
            (Join-Path $private 'q4-continuation-orchestrator-job.pending.json'),
            (Join-Path $private 'q4-live-update.pending.json'),
            (Join-Path $checkpoint 'q4-prefix-fence.json'))
        foreach ($other in $competing) {
            if ([WdQ4AdoptPendingV1]::Exists($other)) { throw 'competing pending' }
        }
        [void](CheckedPath $pendingPath)
        $pendingHandle=[WdQ4AdoptPendingV1]::OpenExisting($pendingPath)
        $pendingStream=[IO.FileStream]::new($pendingHandle,[IO.FileAccess]::Read)
        if ([WdQ4AdoptPendingV1]::Identity($pendingStream.SafeFileHandle) -cne [string]$answer.marker_id -or
            $pendingStream.Length -le 0 -or $pendingStream.Length -gt 65536) { throw 'marker identity' }
        $observed=New-Object byte[] ([int]$pendingStream.Length)
        if ($pendingStream.Read($observed,0,$observed.Length) -ne $observed.Length) { throw 'marker read' }
        $hasher=[Security.Cryptography.SHA256]::Create()
        try { $markerPin=[BitConverter]::ToString($hasher.ComputeHash($observed)).Replace('-','').ToLowerInvariant() }
        finally { $hasher.Dispose() }
        if ($markerPin -cne [string]$answer.marker_sha256) { throw 'marker digest' }
        $markerData=[Text.Encoding]::ASCII.GetString($observed) | ConvertFrom-Json -ErrorAction Stop
        if ([string]$markerData.schema -cne 'wd.q4-parent-owned-recovery.v1' -or
            [string]$markerData.attempt -cne $attempt -or [string]$markerData.commit -cne $ExpectedCommit -or
            [string]$markerData.manifest_sha256 -cne $ExpectedManifestSha256 -or
            [string]$markerData.config_sha256 -cne $ExpectedConfigSha256 -or
            [string]$markerData.runtime_sha256 -cne $ExpectedRuntimeSha256 -or
            [string]$markerData.host_sha256 -cne $ExpectedHostSha256 -or
            [string]$markerData.receipt_sha256 -cne [string]$configData.receipt_sha256 -or
            [string]$markerData.tuple_sha256 -cne [string]$configData.tuple_sha256 -or
            [string]$markerData.baseline_sha256 -cne [string]$configData.baseline_sha256 -or
            [string]$markerData.interpreter_sha256 -cne $pythonPin -or
            [string]$markerData.launcher_sha256 -cne ([string]$manifestData.files.'Invoke-WdQ4AdoptRecovery.ps1').ToLowerInvariant()) { throw 'marker binding' }
        $resultPath=Join-Path $private ('q4-adopt-recovery-job-result-'+$attempt+'.json')
        $resultBytes=[Text.Encoding]::ASCII.GetBytes(([ordered]@{
            schema='wd.q4-adopt-recovery-job-result.v1'; attempt=$attempt; marker_id=[string]$answer.marker_id;
            marker_sha256=$markerPin; manifest_sha256=$ExpectedManifestSha256; config_sha256=$ExpectedConfigSha256;
            runtime_sha256=$ExpectedRuntimeSha256; host_sha256=$ExpectedHostSha256;
            receipt_sha256=[string]$configData.receipt_sha256; tuple_sha256=[string]$configData.tuple_sha256;
            baseline_sha256=[string]$configData.baseline_sha256; interpreter_sha256=$pythonPin;
            launcher_sha256=([string]$manifestData.files.'Invoke-WdQ4AdoptRecovery.ps1').ToLowerInvariant(); commit=$ExpectedCommit;
            status=[string]$answer.status; exit_code=$code; job_outcome='completed'; job_cleanup='confirmed'
        } | ConvertTo-Json -Compress))
        if ($FinalizeAttempt) {
            if ([Convert]::ToBase64String($priorBytes) -cne [Convert]::ToBase64String($resultBytes)) { throw 'receipt mismatch' }
        } else {
            $resultStream=[IO.File]::Open($resultPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            try { $resultStream.Write($resultBytes,0,$resultBytes.Length); $resultStream.Flush($true) }
            finally { $resultStream.Dispose() }
        }
        [WdQ4AdoptPendingV1]::DeleteOwned($pendingStream.SafeFileHandle)
        $pendingStream.Dispose(); $pendingStream=$null
        if ([IO.File]::Exists($pendingPath)) { throw 'pending still exists' }
    } finally {
        if ($checkpointHeld) { $checkpointLock.Unlock(0,1) }
        if ($null -ne $checkpointLock) { $checkpointLock.Dispose() }
        if ($privateHeld) { $privateLock.Unlock(0,1) }
        if ($null -ne $privateLock) { $privateLock.Dispose() }
        for ($i=$ownerLocks.Count-1; $i -ge 0; $i--) {
            try { $ownerLocks[$i].Unlock(0,1) } finally { $ownerLocks[$i].Dispose() }
        }
    }
    [Console]::Out.WriteLine((@{ status=[string]$answer.status } | ConvertTo-Json -Compress))
    exit $code
} catch {
    if ($started) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
} finally {
    if ($null -ne $pendingStream) { $pendingStream.Dispose() }
}

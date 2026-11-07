#requires -Version 5.1
<# Fixed, opt-in staging entry. Job timeout covers the worker and its children;
   this host's initial pin checks and Job compilation are outside that timeout.
   No installation or pointer activation. UNKNOWN means do not retry blindly.
   Pins are caller assertions, not authentication or hostile-user isolation. #>
[CmdletBinding(PositionalBinding=$false)]
param(
    [switch]$Run,
    [string]$BundleRoot='', [string]$ExpectedCommit='', [string]$ExpectedManifestSha256='',
    [string]$RequestPath='', [string]$ExpectedRequestSha256='',
    [string]$RuntimePath='', [string]$ExpectedRuntimeSha256='',
    [string]$ScratchRoot='', [string]$ExpectedHostSha256='',
    [ValidateRange(1,1800)][int]$TimeoutSeconds=1800
)
$ErrorActionPreference='Stop'
if (-not $Run) { [Console]::Out.WriteLine('{"status":"OFF"}'); return }
$entered=$false
try {
    if ($PSVersionTable.PSEdition -ceq 'Desktop') {
        Import-Module -Name (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
    }
    foreach ($pin in @($ExpectedManifestSha256,$ExpectedRequestSha256,$ExpectedRuntimeSha256,$ExpectedHostSha256)) {
        if ($pin -cnotmatch '^[0-9a-f]{64}$') { throw 'pin' }
    }
    if ($ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'commit' }
    function CheckedPath([string]$Path,[bool]$Directory=$false) {
        if ($Path -notmatch '^C:\\' -or $Path.Contains('%') -or $Path -match '(^|\\)\.\.?($|\\)') { throw 'path' }
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
    function ExactHash([string]$Path,[string]$Pin) {
        if ($Pin -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'pin' }
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try { $h=[Security.Cryptography.SHA256]::Create(); try { $actual=[BitConverter]::ToString($h.ComputeHash($stream)).Replace('-','') } finally { $h.Dispose() } }
        finally { $stream.Dispose() }
        if ($actual -cne $Pin.ToUpperInvariant()) { throw 'hash' }
    }
    function PinnedBytes([string]$Path,[string]$Pin,[int]$Limit) {
        if ($Pin -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'pin' }
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($stream.Length -le 0 -or $stream.Length -gt $Limit) { throw 'size' }
            $bytes=New-Object byte[] ([int]$stream.Length); $offset=0
            while ($offset -lt $bytes.Length) {
                $n=$stream.Read($bytes,$offset,$bytes.Length-$offset); if ($n -le 0) { throw 'read' }; $offset+=$n
            }
            if ($stream.ReadByte() -ne -1) { throw 'size' }
        } finally { $stream.Dispose() }
        $h=[Security.Cryptography.SHA256]::Create()
        try { $actual=[BitConverter]::ToString($h.ComputeHash($bytes)).Replace('-','') } finally { $h.Dispose() }
        if ($actual -cne $Pin.ToUpperInvariant()) { throw 'hash' }
        return ,$bytes
    }
    function PinnedJson([string]$Path,[string]$Pin,[int]$Limit) {
        $bytes=PinnedBytes $Path $Pin $Limit
        $text=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
        return ($text | ConvertFrom-Json -ErrorAction Stop)
    }
    $bundle=CheckedPath $BundleRoot $true
    $request=CheckedPath $RequestPath; $runtime=CheckedPath $RuntimePath
    $manifest=CheckedPath (Join-Path $bundle 'deployment-manifest.json')
    $bootstrap=CheckedPath (Join-Path $bundle 'tools-bootstrap\tools\wd_q4_card_orchestrator_runtime_bootstrap.py')
    $jobHelper=CheckedPath (Join-Path $bundle 'WdQ4ProcessJob.ps1')
    $self=CheckedPath (Join-Path $bundle 'Invoke-WdQ4CardOrchestratorJob.ps1')
    if (-not [IO.Path]::GetFullPath($PSCommandPath).Equals($self,[StringComparison]::OrdinalIgnoreCase)) { throw 'self' }
    $m=PinnedJson $manifest $ExpectedManifestSha256 4194304
    $r=PinnedJson $runtime $ExpectedRuntimeSha256 65536
    $q=PinnedJson $request $ExpectedRequestSha256 1048576
    if ([int]$m.schema_version -ne 1 -or [string]$m.source_commit -cne $ExpectedCommit -or
        [string]$r.schema -cne 'wd.q4-card-orchestrator-runtime.v1' -or
        [string]$q.schema -cne 'wd.q4-card-real-candidate-request.v1' -or
        [string]$r.manifest_sha256 -cne $ExpectedManifestSha256 -or
        -not [IO.Path]::GetFullPath([string]$r.bundle_root).Equals($bundle,[StringComparison]::OrdinalIgnoreCase)) { throw 'binding' }
    foreach ($pair in @(@('Invoke-WdQ4CardOrchestratorJob.ps1',$self),
                         @('tools-bootstrap/tools/wd_q4_card_orchestrator_runtime_bootstrap.py',$bootstrap))) {
        ExactHash $pair[1] ([string]$m.files.PSObject.Properties[$pair[0]].Value)
    }
    $helperBytes=PinnedBytes $jobHelper ([string]$m.files.'WdQ4ProcessJob.ps1') 1048576
    $helperText=[Text.UTF8Encoding]::new($false,$true).GetString($helperBytes)
    $python=CheckedPath ([string]$r.interpreter_path)
    $pythonPin=[string]$r.interpreter_sha256; ExactHash $python $pythonPin
    $hostPath=CheckedPath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    ExactHash $hostPath $ExpectedHostSha256
    $snapshot=CheckedPath ([string]$q.snapshot_template.path)
    $decision=CheckedPath ([string]$q.decision.path)
    $finish=CheckedPath ([string]$q.finish.path)
    $snapshotData=PinnedJson $snapshot ([string]$q.snapshot_template.sha256) 1048576
    $finishData=PinnedJson $finish ([string]$q.finish.sha256) 1048576
    $decisionData=PinnedJson $decision ([string]$q.decision.sha256) 1048576
    if ([string]$decisionData.source_mode -cne 'operator_authorized_source') { throw 'mode' }
    $scratch=CheckedPath $ScratchRoot $true
    if ($null -ne ('WdQ4CardJobPathsV1' -as [type])) { throw 'type' }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class WdQ4CardJobPathsV1 {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern SafeFileHandle CreateFile(string n,uint a,uint s,IntPtr p,uint d,uint f,IntPtr t);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder p,uint n,uint f);
 public static string Final(string p) { using(var h=CreateFile(p,0,7,IntPtr.Zero,3,0x02000000,IntPtr.Zero)) {
  if(h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error()); var b=new StringBuilder(32768);
  uint n=GetFinalPathNameByHandle(h,b,(uint)b.Capacity,0); if(n==0||n>=b.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error()); return b.ToString().TrimEnd('\\'); } }
}
'@ -ErrorAction Stop
    $scratchFinal=[WdQ4CardJobPathsV1]::Final($scratch)
    $protected=@($bundle,(Split-Path -Parent $python),(Split-Path -Parent $hostPath),$request,$runtime,$snapshot,$decision,$finish,
        [string]$r.runtime_root,[string]$snapshotData.trusted_model_root,[string]$q.output_root,[string]$q.generation_root,
        [string]$decisionData.source_path,(Split-Path -Parent ([string]$decisionData.source_path)),
        [string]$decisionData.anchor.root,[string]$decisionData.git.repository,
        [string]$finishData.composer_options.bundle_store_root,[string]$finishData.composer_options.active_bundle_root)
    foreach ($item in @($decisionData.inputs.PSObject.Properties)) { $protected+= [string]$item.Value.path }
    foreach ($item in $protected) {
        $checked=CheckedPath $item ([IO.Directory]::Exists($item))
        $other=[WdQ4CardJobPathsV1]::Final($checked)
        if ($scratchFinal.Equals($other,[StringComparison]::OrdinalIgnoreCase) -or
            $scratchFinal.StartsWith($other+'\',[StringComparison]::OrdinalIgnoreCase) -or
            $other.StartsWith($scratchFinal+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'scratch' }
    }
    . ([scriptblock]::Create($helperText))
    function PSLiteral([string]$Value) {
        if ($Value -match '[^\x20-\x7e]') { throw 'argument' }
        return "'" + $Value.Replace("'","''") + "'"
    }
    # A fixed worker program exists only inside this launch, after all scratch
    # checks; the public script has no callable InternalWorker bypass. Values
    # enter it solely as escaped literals and cannot select code or commands.
    $worker="`$python=$(PSLiteral $python)`n`$bootstrap=$(PSLiteral $bootstrap)`n" +
        "`$manifest=$(PSLiteral $manifest)`n`$request=$(PSLiteral $request)`n`$runtime=$(PSLiteral $runtime)`n" +
        "`$pythonPin=$(PSLiteral $pythonPin)`n`$bootstrapPin=$(PSLiteral ([string]$m.files.'tools-bootstrap/tools/wd_q4_card_orchestrator_runtime_bootstrap.py'))`n" +
        "`$manifestPin=$(PSLiteral $ExpectedManifestSha256)`n`$requestPin=$(PSLiteral $ExpectedRequestSha256)`n`$runtimePin=$(PSLiteral $ExpectedRuntimeSha256)`n"
    $worker+=@'
$ErrorActionPreference='Stop'
function Exact([string]$p,[string]$expected) {
 $s=[IO.File]::Open($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try { $h=[Security.Cryptography.SHA256]::Create(); try { $v=[BitConverter]::ToString($h.ComputeHash($s)).Replace('-','') } finally { $h.Dispose() } }
 finally { $s.Dispose() }; if($v -cne $expected.ToUpperInvariant()) { throw 'pin' }
}
function Quoted([string]$v) {
 if(-not $v -or $v.Contains('"') -or $v.EndsWith('\') -or $v -match '[\x00-\x1f\x7f]') { throw 'argument' }
 return '"'+$v+'"'
}
$proc=$null; $started=$false
try {
 Exact $manifest $manifestPin; Exact $bootstrap $bootstrapPin; Exact $python $pythonPin
 Exact $request $requestPin; Exact $runtime $runtimePin
 $env:WD_REBOOT_EXPECTED_MANIFEST_HASH=$manifestPin
 $psi=[Diagnostics.ProcessStartInfo]::new(); $psi.FileName=$python
 $psi.Arguments=('-I -S -B {0} --enable --request {1} --request-sha256 {2} --manifest-sha256 {3} --runtime {4} --runtime-sha256 {5}' -f `
     (Quoted $bootstrap),(Quoted $request),$requestPin,$manifestPin,(Quoted $runtime),$runtimePin)
 $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true
 $proc=[Diagnostics.Process]::new(); $proc.StartInfo=$psi
 if(-not $proc.Start()) { throw 'start' }; $started=$true
 $drain=$proc.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
 $buffer=New-Object byte[] 1024; $total=0; $out=[Console]::OpenStandardOutput()
 while(($n=$proc.StandardOutput.BaseStream.Read($buffer,0,$buffer.Length)) -gt 0) {
  $total+=$n; if($total -gt 4096) { throw 'output' }; $out.Write($buffer,0,$n)
 }
 $proc.WaitForExit(); if(-not $drain.Wait(2000)) { throw 'stderr' }
 $code=$proc.ExitCode; exit $code
} catch { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
finally {
 if($null -ne $proc) {
  try { if($started -and -not $proc.HasExited) { $proc.Kill(); [void]$proc.WaitForExit(2000) } } catch { }
  $proc.Dispose()
 }
}
'@
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($worker))
    $args=@('-NoProfile','-NonInteractive','-EncodedCommand',$encoded)
    $entered=$true
    $result=Invoke-WdQ4ProcessJob -Executable $hostPath -Arguments $args -WorkingDirectory $bundle `
        -ScratchRoot $scratch -InputBytes ([byte[]]@()) -TimeoutSeconds $TimeoutSeconds -MaxOutputBytes 8192 -DetailedOutcome
    if ([string]$result.Outcome -cne 'completed' -or [string]$result.Cleanup -cne 'confirmed' -or
        $result.ExitCode -ne 0 -or $null -eq $result.Stdout) { throw 'job' }
    $text=[Text.UTF8Encoding]::new($false,$true).GetString([byte[]]$result.Stdout)
    if ($text -cnotmatch '^\{"pointer_path":"(?:[^"\\\x00-\x1f]|\\.)*","pointer_sha256":"[0-9a-f]{64}","status":"PREPARED"\}\r?\n$') { throw 'output' }
    $answer=$text | ConvertFrom-Json -ErrorAction Stop
    $pointer=CheckedPath ([string]$answer.pointer_path)
    $expected=Join-Path (CheckedPath ([string]$q.generation_root) $true) 'stage\candidate-pointer.json'
    if (-not $pointer.Equals($expected,[StringComparison]::OrdinalIgnoreCase)) { throw 'pointer' }
    ExactHash $pointer ([string]$answer.pointer_sha256)
    [Console]::Out.Write($text)
    exit 0
} catch {
    if ($entered) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
}

#requires -Version 5.1
<# One bounded, on-demand stdin relay. A failed or uncertain entered call is never retried.
   Query bytes are never put in argv, environment, a file, or diagnostics.
   -Live selects the separately provisioned live pointer only in the query child;
   without it any inherited live-mode value is removed from that child.
   -WithCoverage requires -Live and returns a versioned envelope that reports
   coverage and freshness as UNKNOWN; it does not assert completeness.
   Job containment is cooperative with Windows and is not hostile-user isolation.
   A stopped runspace can suppress the status: without a complete exit-zero reply,
   treat the outcome as UNKNOWN and do not retry until separately reconciled.
   The worker writes a single gate byte only after assigning its wrapper host to
   a Job. This assumes the trusted no-profile PowerShell host waits for that byte
   before starting the wrapper; conhost/host-internal processes are not proven
   Job members. -InternalWorker is a private implementation path and bypasses
   the outer deadline if invoked directly. A hard kill may bypass finally. #>
[CmdletBinding()]
param(
    [Parameter(ValueFromPipeline=$true)][AllowEmptyString()][string]$QueryJson,
    [ValidateRange(1,1800)][int]$TimeoutSeconds = 120,
    [switch]$Live,
    [switch]$WithCoverage,
    [switch]$Runtime,
    [switch]$WithStatus,
    [switch]$InternalWorker
)
begin {
    $ErrorActionPreference = 'Stop'
    if ($Runtime -and -not $PSBoundParameters.ContainsKey('TimeoutSeconds')) { $TimeoutSeconds = 900 }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $items = [Collections.Generic.List[string]]::new()
    function Get-RecallHash([byte[]]$Bytes) {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-','') }
        finally { $sha.Dispose() }
    }
    function Write-RecallStatus([string]$Status, [Nullable[int]]$ChildExit, [string]$Cleanup) {
        if (-not $WithStatus) { return }
        $exitText = if ($null -eq $ChildExit) { 'null' } else { $ChildExit.ToString([Globalization.CultureInfo]::InvariantCulture) }
        [Console]::Error.WriteLine('{"status":"' + $Status + '","exit_code":' + $exitText + ',"cleanup":"' + $Cleanup + '"}')
    }
}
process {
    if ($PSBoundParameters.ContainsKey('QueryJson')) { $items.Add($QueryJson) }
}
end {
    $query = $null; $manifestStream = $null; $wrapperStream = $null
    if (-not $InternalWorker) {
        # The public invocation supervises a separate copy of this script.
        # Thus its deadline includes the worker's manifest reads and Add-Type,
        # not just the eventual nested wrapper invocation. A timeout is UNKNOWN:
        # terminating this worker alone is not proof of whole-tree cleanup.
        $worker = $null; $stdout = $null; $stderr = $null
        try {
            if (($WithCoverage -and -not $Live) -or ($Runtime -and ($Live -or $WithCoverage))) { throw 'refused' }
            if ($items.Count -gt 1) { throw 'refused' }
            if ($items.Count -eq 1) {
                $query = [Text.UTF8Encoding]::new($false,$true).GetBytes($items[0])
            } else {
                $source = [Console]::OpenStandardInput()
                $memory = [IO.MemoryStream]::new(); $buffer = [byte[]]::new(4096)
                try {
                    while ($true) {
                        $remaining = ($TimeoutSeconds * 1000) - [int]$watch.ElapsedMilliseconds
                        if ($remaining -le 0) { throw 'refused' }
                        $read = $source.ReadAsync($buffer,0,$buffer.Length)
                        if (-not $read.Wait($remaining)) { throw 'refused' }
                        $count = $read.Result
                        if ($count -eq 0) { break }
                        if ($memory.Length + $count -gt 16000) { throw 'refused' }
                        $memory.Write($buffer,0,$count)
                    }
                    $query = $memory.ToArray()
                } finally { $memory.Dispose() }
            }
            if ($query.Length -lt 1 -or $query.Length -gt 16000) { throw 'refused' }
            $remaining = ($TimeoutSeconds * 1000) - [int]$watch.ElapsedMilliseconds
            if ($remaining -le 0) { throw 'refused' }
            $shellName = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
            $shell = [IO.Path]::Combine($PSHOME,$shellName)
            $remainingSeconds = [Math]::Max(1,[int][Math]::Ceiling($remaining / 1000.0))
            $scriptPath = $PSCommandPath.Replace('"','')
            $start = [Diagnostics.ProcessStartInfo]::new()
            $start.FileName = $shell
            $start.Arguments = '-NoProfile -NonInteractive -File "' + $scriptPath + '" -InternalWorker -TimeoutSeconds ' + $remainingSeconds
            if ($Live) { $start.Arguments += ' -Live' }
            if ($WithCoverage) { $start.Arguments += ' -WithCoverage' }
            if ($Runtime) { $start.Arguments += ' -Runtime' }
            if ($WithStatus) { $start.Arguments += ' -WithStatus' }
            $start.UseShellExecute = $false; $start.CreateNoWindow = $true
            $start.RedirectStandardInput = $true; $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
            $worker = [Diagnostics.Process]::Start($start)
            if ($null -eq $worker) { throw 'refused' }
            $stdout = [IO.MemoryStream]::new(); $stderr = [IO.MemoryStream]::new()
            $outBuffer = [byte[]]::new(4096); $errBuffer = [byte[]]::new(4096)
            $outStream = $worker.StandardOutput.BaseStream; $errStream = $worker.StandardError.BaseStream
            $outRead = $outStream.ReadAsync($outBuffer,0,$outBuffer.Length)
            $errRead = $errStream.ReadAsync($errBuffer,0,$errBuffer.Length)
            $writing = $worker.StandardInput.BaseStream.WriteAsync($query,0,$query.Length)
            $inputClosed = $false; $outDone = $false; $errDone = $false
            while ($true) {
                if ($watch.ElapsedMilliseconds -ge ($TimeoutSeconds * 1000)) { throw 'unknown' }
                if (-not $inputClosed -and $writing.IsCompleted) {
                    if ($writing.IsFaulted -or $writing.IsCanceled) { throw 'unknown' }
                    $worker.StandardInput.Close(); $inputClosed = $true
                }
                if (-not $outDone -and $outRead.IsCompleted) {
                    if ($outRead.IsFaulted -or $outRead.IsCanceled) { throw 'unknown' }
                    $count = $outRead.Result
                    if ($count -eq 0) { $outDone = $true }
                    else {
                        if ($stdout.Length + $count -gt 65536) { throw 'unknown' }
                        $stdout.Write($outBuffer,0,$count)
                        $outRead = $outStream.ReadAsync($outBuffer,0,$outBuffer.Length)
                    }
                }
                if (-not $errDone -and $errRead.IsCompleted) {
                    if ($errRead.IsFaulted -or $errRead.IsCanceled) { throw 'unknown' }
                    $count = $errRead.Result
                    if ($count -eq 0) { $errDone = $true }
                    else {
                        if ($stderr.Length + $count -gt 65536) { throw 'unknown' }
                        $stderr.Write($errBuffer,0,$count)
                        $errRead = $errStream.ReadAsync($errBuffer,0,$errBuffer.Length)
                    }
                }
                if ($worker.HasExited -and $inputClosed -and $outDone -and $errDone) { break }
                [Threading.Thread]::Sleep(10)
            }
            $code = $worker.ExitCode
            if ($WithStatus) {
                # Only a single exact, locally generated record crosses this boundary.
                # Never relay the worker's raw stderr, even on parse failure.
                $record = [Text.Encoding]::ASCII.GetString($stderr.ToArray())
                if ($record -cmatch '^\{"status":"EXITED","exit_code":(-?(?:0|[1-9][0-9]*)),"cleanup":"CONFIRMED"\}\r?\n$') {
                    $childCode = 0
                    if ([int]::TryParse($Matches[1], [ref]$childCode) -and $childCode -eq $code) {
                        Write-RecallStatus 'EXITED' $childCode 'CONFIRMED'
                    } else { Write-RecallStatus 'UNKNOWN' $null 'UNKNOWN' }
                } else { Write-RecallStatus 'UNKNOWN' $null 'UNKNOWN' }
            }
            if ($code -eq 0) {
                $bytes = $stdout.ToArray(); $out = [Console]::OpenStandardOutput()
                $out.Write($bytes,0,$bytes.Length); $out.Flush()
            }
            exit $code
        } catch {
            Write-RecallStatus 'UNKNOWN' $null 'UNKNOWN'
            if ($null -ne $worker) {
                try { if (-not $worker.HasExited) { $worker.Kill(); [void]$worker.WaitForExit(2000) } } catch { }
                exit 3
            }
            exit 2
        } finally {
            # Pipeline Stop normally runs this block even if catch is bypassed.
            # Kill only the exact Process object started above. A killed host is
            # not, by itself, evidence of whole-tree cleanup.
            if ($null -ne $worker) {
                try {
                    if (-not $worker.HasExited) { $worker.Kill(); [void]$worker.WaitForExit(2000) }
                } catch { }
            }
            if ($null -ne $stdout) { $stdout.Dispose() }
            if ($null -ne $stderr) { $stderr.Dispose() }
            if ($null -ne $worker) { $worker.Dispose() }
            $query = $null; $items.Clear()
        }
    }
    try {
        if ($items.Count -gt 1) { throw 'refused' }
        $bundle = [IO.Path]::GetFullPath([string]$env:WD_BRIDGE_BUNDLE_ROOT)
        $wrapper = [IO.Path]::GetFullPath([string]$env:WD_BRIDGE_PYTHON_WRAPPER)
        $pin = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
        if (-not [IO.Path]::IsPathRooted($bundle) -or $pin -cnotmatch '^[a-fA-F0-9]{64}$' -or
            -not $wrapper.Equals([IO.Path]::Combine($bundle,'Invoke-WdBridgePython.ps1'),[StringComparison]::OrdinalIgnoreCase)) { throw 'refused' }
        foreach ($path in @($bundle,$wrapper,[IO.Path]::Combine($bundle,'deployment-manifest.json'))) {
            $current = $path
            while ($current) {
                if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'refused' }
                $parent = [IO.Path]::GetDirectoryName($current)
                if (-not $parent -or $parent -ceq $current) { break }
                $current = $parent
            }
        }
        $manifestPath = [IO.Path]::Combine($bundle,'deployment-manifest.json')
        # Hold read-only sharing handles while the wrapper executes; verify and
        # parse the same bytes. This does not defeat hostile path replacement.
        $manifestStream = [IO.File]::Open($manifestPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        $wrapperStream = [IO.File]::Open($wrapper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        if ($manifestStream.Length -lt 1 -or $manifestStream.Length -gt 16777216 -or
            $wrapperStream.Length -lt 1 -or $wrapperStream.Length -gt 2097152) { throw 'refused' }
        $manifestBytes = [byte[]]::new([int]$manifestStream.Length)
        $wrapperBytes = [byte[]]::new([int]$wrapperStream.Length)
        foreach ($pair in @(@($manifestStream,$manifestBytes),@($wrapperStream,$wrapperBytes))) {
            $offset = 0
            while ($offset -lt $pair[1].Length) {
                $count = $pair[0].Read($pair[1],$offset,$pair[1].Length-$offset)
                if ($count -le 0) { throw 'refused' }
                $offset += $count
            }
        }
        if ((Get-RecallHash $manifestBytes) -cne $pin.ToUpperInvariant()) { throw 'refused' }
        $manifestText = [Text.UTF8Encoding]::new($false,$true).GetString($manifestBytes).TrimStart([char]0xfeff)
        $manifest = $manifestText | ConvertFrom-Json
        if ($manifest.schema_version -ne 1 -or $null -eq $manifest.files -or
            (Get-RecallHash $wrapperBytes) -cne ([string]$manifest.files.'Invoke-WdBridgePython.ps1').ToUpperInvariant()) { throw 'refused' }
        $typeName = 'WdQ4RecallRelay_' + [Guid]::NewGuid().ToString('N')
        $resultTypeName = 'WdQ4RecallResult_' + [Guid]::NewGuid().ToString('N')
        Add-Type -TypeDefinition @"
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
public sealed class $resultTypeName { public int Code; public byte[] Output; public bool ChildExitObserved; public int ChildExitCode; public bool CleanupConfirmed; }
public static class $typeName {
 [StructLayout(LayoutKind.Sequential)] struct Basic { public long a,b; public uint flags; public UIntPtr min,max; public uint active; public UIntPtr affinity; public uint priority,scheduling; }
 [StructLayout(LayoutKind.Sequential)] struct Io { public ulong a,b,c,d,e,f; }
 [StructLayout(LayoutKind.Sequential)] struct Extended { public Basic basic; public Io io; public UIntPtr pm,jm,pp,pj; }
 [StructLayout(LayoutKind.Sequential)] struct Accounting { public long a,b,c,d; public uint faults,total,active,terminated; }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr a,string b);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref Extended value,int size);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int kind,ref Accounting value,int size,IntPtr ret);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr h,uint ms);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 static byte[] ReadBounded(Stream source,int max) { using(var m=new MemoryStream()) { var b=new byte[4096]; int n; while((n=source.Read(b,0,b.Length))>0) { if(m.Length+n>max) throw new InvalidDataException(); m.Write(b,0,n); } return m.ToArray(); } }
 public static byte[] ReadInput(int cap,int milliseconds) {
   var t=Task.Run(()=>ReadBounded(Console.OpenStandardInput(),cap));
   if(milliseconds<=0 || !t.Wait(milliseconds)) throw new TimeoutException(); return t.Result;
 }
 public static $resultTypeName Run(string shell,string encoded,byte[] input,int ms) {
   var result=new $resultTypeName {Code=3,Output=null}; IntPtr job=IntPtr.Zero; Process p=null;
   Task write=null; Task<byte[]> output=null,error=null; bool ok=true,entered=false,assigned=false; var sw=Stopwatch.StartNew();
   try {
     if(ms<=0) throw new InvalidOperationException();
     job=CreateJobObject(IntPtr.Zero,null); if(job==IntPtr.Zero) throw new InvalidOperationException();
     var limits=new Extended(); limits.basic.flags=0x2000;
     if(!SetInformationJobObject(job,9,ref limits,Marshal.SizeOf(typeof(Extended)))) throw new InvalidOperationException();
     var si=new ProcessStartInfo(shell,"-NoProfile -NonInteractive -EncodedCommand "+encoded) {UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true};
     p=Process.Start(si); if(p==null) throw new InvalidOperationException(); entered=true;
     if(!AssignProcessToJobObject(job,p.Handle)) throw new InvalidOperationException();
     assigned=true;
     output=Task.Run(()=>ReadBounded(p.StandardOutput.BaseStream,65536));
     error=Task.Run(()=>ReadBounded(p.StandardError.BaseStream,65536));
     write=Task.Run(()=> { var s=p.StandardInput.BaseStream; s.WriteByte(71); s.Write(input,0,input.Length); s.Flush(); s.Close(); });
     while(true) {
       if(sw.ElapsedMilliseconds>=ms || write.IsFaulted || output.IsFaulted || error.IsFaulted) throw new InvalidOperationException();
       if(p.WaitForExit(20) && write.IsCompleted && output.IsCompleted && error.IsCompleted) break;
     }
     Task.WaitAll(write,output,error);
     result.Code=p.ExitCode; result.ChildExitCode=p.ExitCode; result.ChildExitObserved=true; if(result.Code==0) result.Output=output.Result;
   } catch(Exception) { result.Code=entered?3:2; result.Output=null; }
   finally {
     if(job!=IntPtr.Zero && !TerminateJobObject(job,125)) ok=false;
     if(p!=null) {
       if(!assigned) { try { if(!p.HasExited) p.Kill(); } catch(Exception) {ok=false;} }
       try { if(WaitForSingleObject(p.Handle,2000)!=0) ok=false; } catch(Exception) {ok=false;}
     }
     if(job!=IntPtr.Zero) {
       bool empty=false; var cleanup=Stopwatch.StartNew();
       do { var a=new Accounting(); if(!QueryInformationJobObject(job,1,ref a,Marshal.SizeOf(typeof(Accounting)),IntPtr.Zero)) break;
            if(a.active==0) {empty=true;break;} Thread.Sleep(10); } while(cleanup.ElapsedMilliseconds<2000);
       if(!empty) ok=false;
       if(!CloseHandle(job)) ok=false;
     }
     try { if(p!=null) p.Dispose(); } catch(Exception) {ok=false;}
   }
   result.CleanupConfirmed=ok && entered && assigned;
   if(!ok) { result.Code=3; result.Output=null; } return result;
 }
}
"@
        $relayType = $typeName -as [type]
        if ($null -eq $relayType) { throw 'refused' }
        $remaining = [Math]::Max(0, ($TimeoutSeconds * 1000) - [int]$watch.ElapsedMilliseconds)
        if ($items.Count -eq 1) { $query = [Text.UTF8Encoding]::new($false,$true).GetBytes($items[0]) }
        else { $query = $relayType::ReadInput(16000,$remaining) }
        if ($query.Length -lt 1 -or $query.Length -gt 16000) { throw 'refused' }
        $text = [Text.UTF8Encoding]::new($false,$true).GetString($query)
        $document = $text | ConvertFrom-Json
        $names = @($document.PSObject.Properties.Name)
        if ($document -is [array] -or $names.Count -ne 2 -or $names -cnotcontains 'situation' -or $names -cnotcontains 'time' -or
            $document.situation -isnot [string] -or [string]::IsNullOrWhiteSpace($document.situation) -or
            ($document.time -isnot [string] -and $document.time -isnot [datetime]) -or
            ($document.time -is [string] -and $document.time -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$')) { throw 'refused' }
        if (($WithCoverage -and -not $Live) -or ($Runtime -and ($Live -or $WithCoverage))) { throw 'refused' }
        $liveEnvironment = if ($Live) { '$env:WD_Q4_LIVE_MODE=''1''; ' } else { 'Remove-Item Env:WD_Q4_LIVE_MODE -ErrorAction SilentlyContinue; ' }
        $coverageEnvironment = if ($WithCoverage) { '$env:WD_Q4_WITH_COVERAGE=''1''; ' } else { 'Remove-Item Env:WD_Q4_WITH_COVERAGE -ErrorAction SilentlyContinue; ' }
        $callerTool = if ($Runtime) { 'tools/wd_q4_lane_caller_runtime_bootstrap.py' } else { 'tools/wd_q4_lane_caller_cli.py' }
        $bootstrap = $liveEnvironment + $coverageEnvironment + 'if ([Console]::OpenStandardInput().ReadByte() -ne 71) { exit 2 }; try { & $env:WD_BRIDGE_PYTHON_WRAPPER ' + $callerTool + ' --enabled; exit $LASTEXITCODE } catch { exit 2 }'
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
        $shellName = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
        $shell = [IO.Path]::Combine($PSHOME,$shellName)
        $remaining = [Math]::Max(0, ($TimeoutSeconds * 1000) - [int]$watch.ElapsedMilliseconds)
        $result = $relayType::Run($shell,$encoded,$query,$remaining)
        if ($result.ChildExitObserved -and $result.CleanupConfirmed -and $result.Code -eq $result.ChildExitCode) {
            Write-RecallStatus 'EXITED' $result.ChildExitCode 'CONFIRMED'
        } else { Write-RecallStatus 'UNKNOWN' $null 'UNKNOWN' }
        if ($result.Code -eq 0 -and $null -ne $result.Output) {
            $out = [Console]::OpenStandardOutput(); $out.Write($result.Output,0,$result.Output.Length); $out.Flush()
        }
        exit $result.Code
    } catch { Write-RecallStatus 'UNKNOWN' $null 'UNKNOWN'; exit 2 }
    finally {
        if ($null -ne $wrapperStream) { $wrapperStream.Dispose() }
        if ($null -ne $manifestStream) { $manifestStream.Dispose() }
        $query = $text = $document = $null; $items.Clear()
    }
}

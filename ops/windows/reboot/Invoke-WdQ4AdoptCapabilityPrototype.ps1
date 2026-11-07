#requires -Version 5.1
<# Synthetic, default-off native handle experiment. This is not a production
   ownership gate and does not defend against hostile same-user duplication. #>
[CmdletBinding(PositionalBinding=$false)]
param(
    [switch]$Run,
    [string]$AuditRoot='', [string]$AuditAnchor='',
    [ValidateSet('good','missing','closed','foreign','notjob','readonly','unsafejob','deletecap','killparent','mutate','grandchild','breakaway','silentbreakaway','wrongattempt','outer_notjob','squat','markerprotect','holdjob')][string]$Scenario='good',
    [string]$Python='', [string]$PythonSha256='', [string]$ProbeSha256='',
    [string]$Manifest='', [string]$ManifestSha256=''
)
$ErrorActionPreference='Stop'
if (-not $Run) { [Console]::Out.WriteLine('{"status":"OFF"}'); return }
$started=$false
try {
    if ($PSVersionTable.PSEdition -ceq 'Desktop') { Import-Module -Name (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop }
    foreach ($pin in @($PythonSha256,$ProbeSha256,$ManifestSha256)) {
        if ($pin -cnotmatch '^[0-9a-f]{64}$') { throw 'pin' }
    }
    $audit=[IO.Path]::GetFullPath($AuditRoot).TrimEnd('\')
    $anchor=[IO.Path]::GetFullPath($AuditAnchor).TrimEnd('\')
    if ($anchor -cnotmatch '^C:\\' -or $anchor -ceq 'C:') { throw 'anchor' }
    if (-not $audit.StartsWith($anchor+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'root' }
    foreach ($value in @($anchor,$audit,$Python,$Manifest)) {
        $item=Get-Item -LiteralPath $value -Force -ErrorAction Stop
        while ($null -ne $item) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse' }
            if ($item.PSIsContainer) { $item=$item.Parent } else { $item=$item.Directory }
        }
    }
    $manifestFull=[IO.Path]::GetFullPath($Manifest)
    if (-not $manifestFull.StartsWith($audit+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'manifest root' }
    $probe=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\tools\wd_q4_adopt_capability_probe.py'))
    foreach ($item in @(@($Python,$PythonSha256),@($probe,$ProbeSha256),@($Manifest,$ManifestSha256))) {
        if ((Get-FileHash -LiteralPath $item[0] -Algorithm SHA256).Hash -cne $item[1].ToUpperInvariant()) { throw 'hash' }
    }
    $document=Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $keys=@($document.PSObject.Properties.Name)
    if ($keys.Count -ne 5 -or @($keys | Where-Object { $_ -cnotin @('schema','python_sha256','probe_sha256','attempt','audit_anchor') }).Count -ne 0 -or
        [string]$document.schema -cne 'wd.synthetic-adopt-capability.v1' -or
        [string]$document.python_sha256 -cne $PythonSha256 -or [string]$document.probe_sha256 -cne $ProbeSha256 -or
        [string]$document.attempt -cnotmatch '^[0-9a-f]{64}$' -or
        -not [IO.Path]::GetFullPath([string]$document.audit_anchor).TrimEnd('\').Equals($anchor,[StringComparison]::OrdinalIgnoreCase)) { throw 'manifest' }
    if ($null -ne ('WdQ4CapabilityPrototypeV1' -as [type])) { throw 'type' }
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;
public static class WdQ4CapabilityPrototypeV1 {
    static readonly object LaunchGate=new object();
    [StructLayout(LayoutKind.Sequential)] struct SI {
        public int cb; public IntPtr reserved,desktop,title; public int x,y,xsize,ysize,xchars,ychars,fill,flags;
        public short show,reserved2; public IntPtr reserved3,input,output,error;
    }
    [StructLayout(LayoutKind.Sequential)] struct SIX { public SI startup; public IntPtr attributes; }
    [StructLayout(LayoutKind.Sequential)] struct PI { public IntPtr process,thread; public uint pid,tid; }
    [StructLayout(LayoutKind.Sequential)] struct BASIC {
        public long processTime,jobTime; public uint flags; public UIntPtr min,max; public uint active;
        public UIntPtr affinity; public uint priority,scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct FILEID { public ulong volume; [MarshalAs(UnmanagedType.ByValArray,SizeConst=16)] public byte[] id; }
    [StructLayout(LayoutKind.Sequential)] struct IO { public ulong a,b,c,d,e,f; }
    [StructLayout(LayoutKind.Sequential)] struct EXT { public BASIC basic; public IO io; public UIntPtr pm,jm,ppm,pjm; }
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern SafeFileHandle CreateFile(string n,uint a,uint s,IntPtr sec,uint create,uint flags,IntPtr t);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern void SetLastError(uint code);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandleEx(SafeFileHandle h,int cls,ref FILEID value,int size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool DuplicateHandle(IntPtr src,IntPtr h,IntPtr dst,out IntPtr result,uint access,bool inherit,uint options);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr s,string name);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref EXT value,int size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr attribute,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcessW(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref SIX si,out PI pi);
    [DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr h);
    [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr h,uint timeout);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr h,out uint code);
    static void Check(bool value) { if(!value) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    static string Q(string s) {
        if(String.IsNullOrEmpty(s) || s.IndexOf('"')>=0 || s.EndsWith("\\")) throw new ArgumentException();
        return "\""+s+"\"";
    }
    static string Sha(byte[] b) { using(var h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(b)).Replace("-","").ToLowerInvariant(); }
    static IntPtr Dup(IntPtr h,uint access,bool same) {
        IntPtr result; Check(DuplicateHandle(GetCurrentProcess(),h,GetCurrentProcess(),out result,access,true,same?2u:0u)); return result;
    }
    static string FileIdentity(SafeFileHandle h) {
        var value=new FILEID {id=new byte[16]};
        Check(GetFileInformationByHandleEx(h,18,ref value,Marshal.SizeOf(typeof(FILEID))));
        return value.volume.ToString("x16")+BitConverter.ToString(value.id).Replace("-","").ToLowerInvariant();
    }
    public static int Run(string root,string python,string probe,string scenario,string manifest,string manifestSha,string attempt) {
        IntPtr job=IntPtr.Zero,outerJob=IntPtr.Zero,squatter=IntPtr.Zero,markerDup=IntPtr.Zero;
        IntPtr process=IntPtr.Zero,thread=IntPtr.Zero,attrs=IntPtr.Zero,list=IntPtr.Zero,jobList=IntPtr.Zero,env=IntPtr.Zero;
        bool attrsReady=false,assigned=false; FileStream marker=null,capability=null,foreign=null;
        try {
            string path=Path.Combine(root,"synthetic-capability-marker.bin");
            var cap=CreateFile(Path.Combine(root,"synthetic-owned-capability.bin"),0xC0010000,1,IntPtr.Zero,1,0x80000080,IntPtr.Zero);
            if(cap.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            capability=new FileStream(cap,FileAccess.ReadWrite); capability.Flush(true);
            string capId=FileIdentity(capability.SafeFileHandle);
            var mh=CreateFile(path,0xC0010000,1,IntPtr.Zero,1,0x80000080,IntPtr.Zero);
            if(mh.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            marker=new FileStream(mh,FileAccess.ReadWrite);
            byte[] bytes=Encoding.ASCII.GetBytes("wd.synthetic-adopt-marker:"+attempt+":"+capId+"\n");
            marker.Write(bytes,0,bytes.Length); marker.Flush(true);
            if(scenario=="wrongattempt") { marker.Position=0; marker.WriteByte((byte)'X'); marker.Flush(true); }
            if(scenario=="outer_notjob") {
                outerJob=CreateJobObject(IntPtr.Zero,null); Check(outerJob!=IntPtr.Zero);
                var outer=new EXT(); outer.basic.flags=0x2000;
                Check(SetInformationJobObject(outerJob,9,ref outer,Marshal.SizeOf(typeof(EXT))));
                Check(AssignProcessToJobObject(outerJob,GetCurrentProcess()));
            }
            string jobName="Local\\wd-q4-synthetic-"+attempt;
            if(scenario=="squat") { squatter=CreateJobObject(IntPtr.Zero,jobName); Check(squatter!=IntPtr.Zero); }
            SetLastError(0); job=CreateJobObject(IntPtr.Zero,jobName); int createError=Marshal.GetLastWin32Error();
            Check(job!=IntPtr.Zero); if(createError==183) throw new InvalidOperationException();
            var limits=new EXT(); limits.basic.flags=scenario=="unsafejob"?0u:scenario=="breakaway"?0x2800u:scenario=="silentbreakaway"?0x3000u:0x2000u;
            Check(SetInformationJobObject(job,9,ref limits,Marshal.SizeOf(typeof(EXT))));
            IntPtr chosen=capability.SafeFileHandle.DangerousGetHandle();
            if(scenario=="foreign") {
                var fh=CreateFile(Path.Combine(root,"synthetic-foreign-marker.bin"),0xC0010000,1,IntPtr.Zero,1,0x80000080,IntPtr.Zero);
                if(fh.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                foreign=new FileStream(fh,FileAccess.ReadWrite); foreign.Flush(true);
                chosen=foreign.SafeFileHandle.DangerousGetHandle();
            }
            // Give the child only read/write data, read attributes and
            // synchronize. It must never inherit DELETE or the Job handle.
            lock(LaunchGate) {
            if(scenario!="missing") {
                uint access=scenario=="readonly"?0x120089u:0x100083u;
                markerDup=Dup(chosen,access,scenario=="deletecap");
            }
            IntPtr childMarker=markerDup;
            if(scenario=="closed") { Check(CloseHandle(markerDup)); markerDup=IntPtr.Zero; }
            bool attach=scenario!="notjob" && scenario!="outer_notjob";
            int count=(attach?1:0)+(markerDup!=IntPtr.Zero?1:0);
            if(count==0) throw new InvalidOperationException();
            IntPtr size=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,count,0,ref size);
            if(size==IntPtr.Zero) throw new InvalidOperationException();
            attrs=Marshal.AllocHGlobal(size); Check(InitializeProcThreadAttributeList(attrs,count,0,ref size)); attrsReady=true;
            if(markerDup!=IntPtr.Zero) {
                list=Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(list,markerDup);
                Check(UpdateProcThreadAttribute(attrs,0,new IntPtr(0x00020002),list,new IntPtr(IntPtr.Size),IntPtr.Zero,IntPtr.Zero));
            }
            if(attach) {
                jobList=Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(jobList,job);
                Check(UpdateProcThreadAttribute(attrs,0,new IntPtr(0x0002000D),jobList,new IntPtr(IntPtr.Size),IntPtr.Zero,IntPtr.Zero));
            }
            var si=new SIX {startup=new SI {cb=Marshal.SizeOf(typeof(SIX))},attributes=attrs};
            string mode=scenario=="killparent"?"linger":scenario=="holdjob"?"holdjob":scenario=="markerprotect"?"markerprotect":scenario=="mutate"?"mutate":scenario=="grandchild"?"grandchild":"once";
            string command=Q(python)+" -I -S -B "+Q(probe)+" --child "+childMarker.ToInt64()+" "+Q(path)+" "+Q(manifest)+" "+manifestSha+" "+mode;
            string system=Environment.GetEnvironmentVariable("SYSTEMROOT");
            env=Marshal.StringToHGlobalUni("SYSTEMROOT="+system+"\0WINDIR="+system+"\0TEMP="+root+"\0TMP="+root+"\0\0");
            PI pi; Check(CreateProcessW(python,new StringBuilder(command),IntPtr.Zero,IntPtr.Zero,markerDup!=IntPtr.Zero,0x08080404,env,root,ref si,out pi));
            process=pi.process; thread=pi.thread;
            assigned=attach;
            // The process-wide cooperating launch gate covers duplicate/create/close.
            if(markerDup!=IntPtr.Zero) { Check(CloseHandle(markerDup)); markerDup=IntPtr.Zero; }
            }
            if(ResumeThread(thread)==UInt32.MaxValue) throw new Win32Exception(Marshal.GetLastWin32Error());
            if(WaitForSingleObject(process,15000)!=0) throw new TimeoutException();
            uint code; Check(GetExitCodeProcess(process,out code));
            if(code!=0 && code!=2) throw new InvalidOperationException();
            return (int)code;
        } finally {
            if(job!=IntPtr.Zero) TerminateJobObject(job,124);
            if(process!=IntPtr.Zero && !assigned) { TerminateProcess(process,125); WaitForSingleObject(process,2000); }
            if(thread!=IntPtr.Zero) CloseHandle(thread); if(process!=IntPtr.Zero) CloseHandle(process);
            if(markerDup!=IntPtr.Zero) CloseHandle(markerDup); if(job!=IntPtr.Zero) CloseHandle(job);
            if(squatter!=IntPtr.Zero) CloseHandle(squatter);
            // Synthetic outer job intentionally lives until this short-lived host exits.
            if(outerJob!=IntPtr.Zero && scenario!="outer_notjob") CloseHandle(outerJob);
            if(foreign!=null) foreign.Dispose(); if(marker!=null) marker.Dispose(); if(capability!=null) capability.Dispose();
            if(attrsReady) DeleteProcThreadAttributeList(attrs); if(attrs!=IntPtr.Zero) Marshal.FreeHGlobal(attrs);
            if(list!=IntPtr.Zero) Marshal.FreeHGlobal(list); if(jobList!=IntPtr.Zero) Marshal.FreeHGlobal(jobList); if(env!=IntPtr.Zero) Marshal.FreeHGlobal(env);
        }
    }
}
'@ -ErrorAction Stop
    $started=$true
    $code=[WdQ4CapabilityPrototypeV1]::Run($audit,$Python,$probe,$Scenario,$manifestFull,$ManifestSha256,[string]$document.attempt)
    if ($code -eq 0) { [Console]::Out.WriteLine('{"status":"CAPABILITY_VALID"}'); exit 0 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
} catch {
    if ($started) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
}

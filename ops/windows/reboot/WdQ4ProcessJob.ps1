#requires -Version 5.1
# Inactive, caller-pinned job helper. This is not an installer, a caller
# authenticator, or a whole-call/preflight deadline. It never writes input to disk.
# Caller must pin/trust this helper, host and executable, authorize ScratchRoot
# and check it does not overlap any protected runtime/data/manifest/lock paths.
# Child stdout remains untrusted and must be independently validated/sanitized.
# Scratch children are fresh and intentionally retained; no cleanup is attempted.
# The helper accepts only the native type it loaded in this script scope. A
# pre-existing same-named type, or re-dot-sourcing after load, fails closed.
$script:WdQ4ProcessJobOwnedTypeV1 = $null
function Invoke-WdQ4ProcessJob {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string] $Executable,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]] $Arguments,
        [Parameter(Mandatory)][string] $WorkingDirectory,
        [Parameter(Mandatory)][string] $ScratchRoot,
        [Parameter(Mandatory)][AllowEmptyCollection()][byte[]] $InputBytes,
        [Parameter(Mandatory)][int] $TimeoutSeconds,
        [Parameter(Mandatory)][int] $MaxOutputBytes
    )
    try {
        if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 1800 -or
            $MaxOutputBytes -lt 1 -or $MaxOutputBytes -gt 262144 -or
            $InputBytes.Length -gt 35000000 -or
            -not [IO.Path]::IsPathRooted($Executable) -or
            -not [IO.Path]::IsPathRooted($WorkingDirectory) -or
            $ScratchRoot -cnotmatch '^[A-Za-z]:\\' -or $ScratchRoot.Substring(2).Contains(':')) { throw 'invalid' }
        foreach ($segment in $ScratchRoot.Substring(3).Split('\')) {
            if (-not $segment -or $segment -in @('.', '..') -or $segment.EndsWith('.') -or
                $segment.EndsWith(' ')) { throw 'invalid' }
        }
        $scratch=[IO.Path]::GetFullPath($ScratchRoot)
        $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($scratch))
        if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed -or
            -not [IO.Directory]::Exists($scratch)) { throw 'invalid' }
        $current=$scratch
        while ($current) {
            $attrs=[IO.File]::GetAttributes($current)
            if (($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                ($attrs -band [IO.FileAttributes]::Directory) -eq 0) { throw 'invalid' }
            $parent=[IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }
            $current=$parent
        }
        foreach ($other in @([IO.Path]::GetFullPath($WorkingDirectory),
                              [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Executable)))) {
            $left=$scratch.TrimEnd('\');$right=$other.TrimEnd('\')
            if ($left.Equals($right,[StringComparison]::OrdinalIgnoreCase) -or
                $left.StartsWith($right+'\',[StringComparison]::OrdinalIgnoreCase) -or
                $right.StartsWith($left+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'invalid' }
        }
        $nativeType='WdQ4ProcessJobNativeV1' -as [type]
        if ($null -ne $nativeType -and ($null -eq $script:WdQ4ProcessJobOwnedTypeV1 -or
            -not [object]::ReferenceEquals($nativeType,$script:WdQ4ProcessJobOwnedTypeV1))) { throw 'invalid' }
        if ($null -eq $nativeType) {
            $defined=@(Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;

public sealed class WdQ4ProcessJobResultV1 {
    public int ExitCode { get; set; }
    public byte[] Stdout { get; set; }
}

public static class WdQ4ProcessJobNativeV1 {
    [StructLayout(LayoutKind.Sequential)] struct SA { public int length; public IntPtr descriptor; public int inherit; }
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct SI {
        public int size; public string reserved, desktop, title;
        public int x,y,xSize,ySize,xChars,yChars,fill,flags;
        public short show,reservedSize; public IntPtr reservedPtr,input,output,error;
    }
    [StructLayout(LayoutKind.Sequential)] struct SIX { public SI startup; public IntPtr attributes; }
    [StructLayout(LayoutKind.Sequential)] struct PI { public IntPtr process,thread; public int pid,tid; }
    [StructLayout(LayoutKind.Sequential)] struct BL {
        public long processTime,jobTime; public uint flags; public UIntPtr minWorking,maxWorking;
        public uint activeLimit; public UIntPtr affinity; public uint priority,scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IO {
        public ulong readOps,writeOps,otherOps,readBytes,writeBytes,otherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct EL {
        public BL basic; public IO io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob;
    }
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool CreatePipe(out IntPtr r,out IntPtr w,ref SA a,int size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetHandleInformation(IntPtr h,uint mask,uint flags);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr key,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr a,string name);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref EL info,int size);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcessW(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref SIX si,out PI pi);
    [DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint ms);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateDirectoryW(string path,IntPtr attributes);
    static void Check(bool value) { if (!value) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    static void Close(ref IntPtr h) { if (h!=IntPtr.Zero) { CloseHandle(h); h=IntPtr.Zero; } }
    static string Quote(string text) {
        var b=new StringBuilder("\""); int slashes=0;
        foreach(char c in text) {
            if(c=='\\') { slashes++; continue; }
            if(c=='\"') { b.Append('\\',slashes*2+1); b.Append(c); }
            else { b.Append('\\',slashes); b.Append(c); }
            slashes=0;
        }
        b.Append('\\',slashes*2); b.Append('\"'); return b.ToString();
    }
    static IntPtr EnvironmentBlock(string scratch) {
        var values=new List<string>();
        foreach(string k in new [] {"SYSTEMROOT","WINDIR"}) {
            string v=Environment.GetEnvironmentVariable(k);
            if(!String.IsNullOrEmpty(v)) {
                if(v.IndexOf('\0')>=0) throw new InvalidOperationException();
                values.Add(k+"="+v);
            }
        }
        values.Add("TEMP="+scratch); values.Add("TMP="+scratch);
        values.Sort(StringComparer.OrdinalIgnoreCase);
        return Marshal.StringToHGlobalUni(String.Join("\0",values.ToArray())+"\0\0");
    }
    public static WdQ4ProcessJobResultV1 Run(string executable,string[] args,string cwd,string scratchRoot,byte[] input,int timeout,int cap) {
        IntPtr job=IntPtr.Zero,process=IntPtr.Zero,thread=IntPtr.Zero,attr=IntPtr.Zero,handles=IntPtr.Zero,env=IntPtr.Zero;
        IntPtr childIn=IntPtr.Zero,parentIn=IntPtr.Zero,parentOut=IntPtr.Zero,childOut=IntPtr.Zero,parentErr=IntPtr.Zero,childErr=IntPtr.Zero;
        FileStream writer=null,reader=null,errors=null; bool attrInitialized=false,assigned=false;
        var clock=Stopwatch.StartNew(); var output=new MemoryStream(); int total=0;
        try {
            string scratch=Path.Combine(scratchRoot,"wd-q4-"+Guid.NewGuid().ToString("N"));
            Check(CreateDirectoryW(scratch,IntPtr.Zero));
            if ((File.GetAttributes(scratch) & FileAttributes.ReparsePoint)!=0) throw new InvalidOperationException();
            job=CreateJobObject(IntPtr.Zero,null); Check(job!=IntPtr.Zero);
            var limits=new EL(); limits.basic.flags=0x2000; // KILL_ON_JOB_CLOSE, no breakaway flags
            Check(SetInformationJobObject(job,9,ref limits,Marshal.SizeOf(typeof(EL))));
            var sa=new SA {length=Marshal.SizeOf(typeof(SA)),inherit=1};
            Check(CreatePipe(out childIn,out parentIn,ref sa,0));
            Check(CreatePipe(out parentOut,out childOut,ref sa,0));
            Check(CreatePipe(out parentErr,out childErr,ref sa,0));
            Check(SetHandleInformation(parentIn,1,0)); Check(SetHandleInformation(parentOut,1,0)); Check(SetHandleInformation(parentErr,1,0));
            IntPtr length=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref length);
            if(length==IntPtr.Zero) throw new InvalidOperationException();
            attr=Marshal.AllocHGlobal(length); Check(InitializeProcThreadAttributeList(attr,1,0,ref length)); attrInitialized=true;
            handles=Marshal.AllocHGlobal(IntPtr.Size*3);
            Marshal.WriteIntPtr(handles,0,childIn); Marshal.WriteIntPtr(handles,IntPtr.Size,childOut); Marshal.WriteIntPtr(handles,IntPtr.Size*2,childErr);
            Check(UpdateProcThreadAttribute(attr,0,new IntPtr(0x00020002),handles,new IntPtr(IntPtr.Size*3),IntPtr.Zero,IntPtr.Zero));
            var si=new SIX {startup=new SI {size=Marshal.SizeOf(typeof(SIX)),flags=0x100,input=childIn,output=childOut,error=childErr},attributes=attr};
            var cmd=new StringBuilder(Quote(executable)); foreach(var a in args) cmd.Append(" ").Append(Quote(a));
            env=EnvironmentBlock(scratch); PI pi;
            Check(CreateProcessW(executable,cmd,IntPtr.Zero,IntPtr.Zero,true,0x08080404,env,cwd,ref si,out pi));
            process=pi.process; thread=pi.thread;
            Check(AssignProcessToJobObject(job,process)); assigned=true;
            if(ResumeThread(thread)==UInt32.MaxValue) throw new Win32Exception(Marshal.GetLastWin32Error());
            Close(ref childIn); Close(ref childOut); Close(ref childErr); Close(ref thread);
            writer=new FileStream(new SafeFileHandle(parentIn,true),FileAccess.Write,4096,false); parentIn=IntPtr.Zero;
            reader=new FileStream(new SafeFileHandle(parentOut,true),FileAccess.Read,4096,false); parentOut=IntPtr.Zero;
            errors=new FileStream(new SafeFileHandle(parentErr,true),FileAccess.Read,4096,false); parentErr=IntPtr.Zero;
            var w=writer; var r=reader; var e=errors;
            var write=Task.Run(() => { try { w.Write(input,0,input.Length); } finally { w.Dispose(); } });
            var read=Task.Run(() => { byte[] b=new byte[8192]; int n; while((n=r.Read(b,0,b.Length))!=0) {
                if(Interlocked.Add(ref total,n)>cap) throw new InvalidDataException(); output.Write(b,0,n); } });
            var readErr=Task.Run(() => { byte[] b=new byte[8192]; int n; while((n=e.Read(b,0,b.Length))!=0)
                if(Interlocked.Add(ref total,n)>cap) throw new InvalidDataException(); });
            for(;;) {
                if(clock.ElapsedMilliseconds >= timeout*1000L || write.IsFaulted || read.IsFaulted || readErr.IsFaulted)
                    throw new InvalidOperationException();
                uint state=WaitForSingleObject(process,20);
                if(state==0xffffffff) throw new Win32Exception(Marshal.GetLastWin32Error());
                if(state==0 && write.IsCompleted && read.IsCompleted && readErr.IsCompleted) break;
            }
            Task.WhenAll(write,read,readErr).GetAwaiter().GetResult();
            uint code; Check(GetExitCodeProcess(process,out code));
            if(code>Int32.MaxValue) throw new InvalidOperationException();
            return new WdQ4ProcessJobResultV1 {ExitCode=(int)code,Stdout=output.ToArray()};
        } finally {
            bool cleanupOk=true;
            if(job!=IntPtr.Zero && !TerminateJobObject(job,124)) cleanupOk=false;
            if(process!=IntPtr.Zero && !assigned && !TerminateProcess(process,125)) cleanupOk=false;
            Close(ref job); Close(ref process); Close(ref thread);
            if(writer!=null) writer.Dispose(); if(reader!=null) reader.Dispose(); if(errors!=null) errors.Dispose();
            Close(ref childIn); Close(ref parentIn); Close(ref parentOut); Close(ref childOut); Close(ref parentErr); Close(ref childErr);
            if(attrInitialized) DeleteProcThreadAttributeList(attr);
            if(attr!=IntPtr.Zero) Marshal.FreeHGlobal(attr); if(handles!=IntPtr.Zero) Marshal.FreeHGlobal(handles);
            if(env!=IntPtr.Zero) Marshal.FreeHGlobal(env); output.Dispose();
            if(!cleanupOk) throw new InvalidOperationException();
        }
    }
}
'@ -PassThru -ErrorAction Stop)
            $matches=@($defined | Where-Object { $_.FullName -ceq 'WdQ4ProcessJobNativeV1' })
            if ($matches.Count -ne 1 -or $matches[0] -isnot [type]) { throw 'invalid' }
            $nativeType='WdQ4ProcessJobNativeV1' -as [type]
            if (-not [object]::ReferenceEquals($nativeType,$matches[0])) { throw 'invalid' }
            $script:WdQ4ProcessJobOwnedTypeV1=$nativeType
        }
        return $nativeType::Run($Executable, $Arguments, $WorkingDirectory, $scratch, $InputBytes,
                                $TimeoutSeconds, $MaxOutputBytes)
    } catch { throw 'q4_job_refused' }
}

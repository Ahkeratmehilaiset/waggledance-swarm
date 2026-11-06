#requires -Version 5.1
<#
Unwired SYNTHETIC ECHO peer, default off. Not a Q4 wrapper/model launcher.
The caller must pin this script, PowerShell and the parent transport. No
filesystem/log/env output or query text in arguments. PID/creation-time
comparison is process binding, not authentication of an operator or publisher.
The parent owns the kill-on-close job and the deadline. This child uses blocking
pipe I/O and depends on that parent containment; no independent watchdog claim.
Path-based PowerShell launch remains a caller-owned check/use limitation.
#>
[CmdletBinding(PositionalBinding=$true)]
param(
    [switch]$Enabled,
    [Parameter(Position=0)][string]$PipeName='',
    [Parameter(Position=1)][int]$ParentPid=0,
    [Parameter(Position=2)][long]$ParentCreated=0
)
$ErrorActionPreference='Stop'
if(-not $Enabled){throw 'secure_pipe_disabled'}
if($PipeName -cnotmatch '^\\\\\.\\pipe\\wd-q4-synthetic-[0-9a-f]{32}\z' -or
   $ParentPid -le 0 -or $ParentCreated -le 0){throw 'secure_pipe_refused'}
$pipe=$null
try {
    if('WdQ4SyntheticPipePeerV1' -as [type]){throw 'secure_pipe_refused'}
    $types=@(Add-Type -PassThru -ErrorAction Stop -TypeDefinition @'
using System;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Diagnostics;
using Microsoft.Win32.SafeHandles;
public static class WdQ4SyntheticPipePeerV1 {
    [DllImport("kernel32.dll",SetLastError=true)]
    static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe,out uint pid);
    [DllImport("kernel32.dll",SetLastError=true)]
    static extern bool GetHandleInformation(SafePipeHandle pipe,out uint flags);
    public static void Check(NamedPipeClientStream pipe,int expected,long created) {
        uint actual,flags;
        if(!GetHandleInformation(pipe.SafePipeHandle,out flags) || (flags&1)!=0 ||
           !GetNamedPipeServerProcessId(pipe.SafePipeHandle,out actual) || actual!=(uint)expected)
            throw new InvalidOperationException();
        using(var process=Process.GetProcessById(expected)) {
            if(process.HasExited || process.StartTime.ToUniversalTime().ToFileTimeUtc()!=created)
                throw new InvalidOperationException();
        }
    }
}
'@)
    $matches=@($types|Where-Object {$_.FullName -ceq 'WdQ4SyntheticPipePeerV1'})
    if($matches.Count -ne 1 -or -not [object]::ReferenceEquals(
        ('WdQ4SyntheticPipePeerV1' -as [type]),$matches[0])){throw 'secure_pipe_refused'}
    # Explicit HandleInheritability.None: never a temporary inheritable handle.
    $shortName=$PipeName.Substring(9)
    $pipe=[IO.Pipes.NamedPipeClientStream]::new(
        '.', $shortName, [IO.Pipes.PipeDirection]::InOut,
        [IO.Pipes.PipeOptions]::None,
        [Security.Principal.TokenImpersonationLevel]::Identification,
        [IO.HandleInheritability]::None)
    $pipe.Connect(5000)
    [WdQ4SyntheticPipePeerV1]::Check($pipe,$ParentPid,$ParentCreated)
    function Read-WdSyntheticExact([int]$Count) {
        $bytes=New-Object byte[] $Count
        $offset=0
        while($offset -lt $Count){
            $n=$pipe.Read($bytes,$offset,$Count-$offset)
            if($n -le 0){throw 'secure_pipe_refused'}
            $offset+=$n
        }
        return ,$bytes
    }
    function Read-WdSyntheticFrame([int]$Limit) {
        [byte[]]$header=Read-WdSyntheticExact 4
        if([BitConverter]::IsLittleEndian){[Array]::Reverse($header)}
        $size=[BitConverter]::ToUInt32($header,0)
        if($size -gt $Limit){throw 'secure_pipe_refused'}
        return ,(Read-WdSyntheticExact ([int]$size))
    }
    [byte[]]$metadata=Read-WdSyntheticFrame 65536
    $document=ConvertFrom-Json ([Text.UTF8Encoding]::new($false,$true).GetString($metadata))
    $keys=[string[]]@($document.PSObject.Properties.Name)
    [Array]::Sort($keys,[StringComparer]::Ordinal)
    if(($keys -join ',') -cne 'max_output_bytes,mode' -or
       $document.mode -isnot [string] -or $document.mode -cne 'echo' -or
       $document.max_output_bytes -isnot [int] -or
       $document.max_output_bytes -le 0 -or $document.max_output_bytes -gt 262144){
        throw 'secure_pipe_refused'
    }
    [byte[]]$inputBytes=Read-WdSyntheticFrame 65536
    if($inputBytes.Length -gt $document.max_output_bytes){throw 'secure_pipe_refused'}
    [byte[]]$length=[BitConverter]::GetBytes([uint32]$inputBytes.Length)
    if([BitConverter]::IsLittleEndian){[Array]::Reverse($length)}
    $pipe.WriteByte(1)
    $pipe.Write($length,0,4)
    if($inputBytes.Length){$pipe.Write($inputBytes,0,$inputBytes.Length)}
    [byte[]]$end=@(0,0,0,0,69,78,68,33)
    $pipe.Write($end,0,$end.Length)
    $pipe.Flush()
} catch {
    # No raw exception, query, path or policy is emitted to stdout/stderr.
    if($null -ne $pipe -and $pipe.IsConnected){
        try{$pipe.WriteByte(0);$pipe.Flush()}catch{}
    }
    exit 2
} finally {
    if($null -ne $pipe){$pipe.Dispose()}
}
exit 0

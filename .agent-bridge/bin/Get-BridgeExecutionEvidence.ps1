#requires -Version 5.1
# No pointer rebinding. Unknown observations remain null, never guessed.
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
function Get-BridgeEvidenceHash {
    param([string]$Path)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $sha=[Security.Cryptography.SHA256]::Create()
    try {return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}
    finally {$sha.Dispose();$stream.Dispose()}
}
$script:bridgeEvidenceCimError=$null
$script:bridgeEvidenceProcessMethod='cim'
function Get-BridgeEvidenceProcess {
    param([int]$ProcessId)
    try {
        if (-not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) { throw 'CIM unavailable' }
        return Get-CimInstance Win32_Process -Filter ('ProcessId='+$ProcessId) -ErrorAction Stop
    } catch {
        $script:bridgeEvidenceCimError=[pscustomobject]@{phase='cim_process_read';pid=$ProcessId;error=$_.Exception.Message}
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw }
        if (-not ('WdBridgeReadOnlyProcess' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Runtime.InteropServices;
public sealed class WdBridgeReadOnlyProcess {
  public int ProcessId, ParentProcessId;
  public string Name, CommandLine, ExecutablePath;
  public DateTime CreationDate;
  [StructLayout(LayoutKind.Sequential)] struct Basic {
    public IntPtr exitStatus, peb, affinity, priority, id, parent;
  }
  [StructLayout(LayoutKind.Sequential)] struct Unicode { public ushort length, maximum; public IntPtr buffer; }
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int id);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetProcessTimes(IntPtr p,out long created,out long exited,out long kernel,out long user);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr p,int flags,StringBuilder name,ref int size);
  [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(IntPtr p,int kind,ref Basic value,int size,out int returned);
  [DllImport("ntdll.dll", EntryPoint="NtQueryInformationProcess")] static extern int QueryBuffer(IntPtr p,int kind,IntPtr value,int size,out int returned);
  public static WdBridgeReadOnlyProcess Read(int id) {
    // No VM_READ, debug privilege, token mutation, or elevation. A denied or
    // unsupported query remains unknown rather than weakening the identity.
    IntPtr handle=OpenProcess(0x1000,false,id);
    if(handle==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(),"OpenProcess(QueryLimitedInformation) pid="+id);
    try {
      Basic basic=new Basic(); int returned;
      int result=NtQueryInformationProcess(handle,0,ref basic,Marshal.SizeOf(typeof(Basic)),out returned);
      if(result!=0 || basic.id.ToInt64()!=id) throw new InvalidOperationException("ProcessBasicInformation failed for pid="+id+" status="+result);
      long created,exited,kernel,user;
      if(!GetProcessTimes(handle,out created,out exited,out kernel,out user) || exited!=0) throw new InvalidOperationException("Process lifetime unavailable pid="+id);
      StringBuilder image=new StringBuilder(32768);int size=image.Capacity;
      if(!QueryFullProcessImageName(handle,0,image,ref size)) throw new Win32Exception(Marshal.GetLastWin32Error(),"Process image unavailable pid="+id);
      // Windows ProcessCommandLineInformation (60) is queried from this exact
      // open process handle; unsupported Windows versions fail closed.
      QueryBuffer(handle,60,IntPtr.Zero,0,out size);
      if(size<Marshal.SizeOf(typeof(Unicode)) || size>1048576) throw new InvalidOperationException("Command line size unavailable pid="+id);
      IntPtr memory=Marshal.AllocHGlobal(size);
      string command;
      try {
        result=QueryBuffer(handle,60,memory,size,out returned);
        if(result!=0 || returned>size) throw new InvalidOperationException("Process command line denied pid="+id+" status="+result);
        Unicode text=(Unicode)Marshal.PtrToStructure(memory,typeof(Unicode));
        long offset=text.buffer.ToInt64()-memory.ToInt64();
        if(text.length%2!=0 || offset<Marshal.SizeOf(typeof(Unicode)) || offset+text.length>size) throw new InvalidOperationException("Invalid command line buffer");
        command=Marshal.PtrToStringUni(text.buffer,text.length/2);
      } finally {Marshal.FreeHGlobal(memory);}
      long endCreated;
      if(!GetProcessTimes(handle,out endCreated,out exited,out kernel,out user) || exited!=0 || endCreated!=created) throw new InvalidOperationException("Process lifetime changed pid="+id);
      return new WdBridgeReadOnlyProcess {ProcessId=id,ParentProcessId=checked((int)basic.parent.ToInt64()),CreationDate=DateTime.FromFileTimeUtc(created),Name=Path.GetFileName(image.ToString()),ExecutablePath=image.ToString(),CommandLine=command};
    } finally {CloseHandle(handle);}
  }
}
'@
        }
        $script:bridgeEvidenceProcessMethod='win32_limited_query'
        return [WdBridgeReadOnlyProcess]::Read($ProcessId)
    }
}
$observed=[datetime]::UtcNow.ToString('o')
$native=$null; $launcher=$null; $nativeId=$null; $ancestryError=$null; $observedAgent=$null
$ancestryPhase='self_process'
try {
        $child=Get-BridgeEvidenceProcess $PID
        $visited=@{}
        for ($i=0;$i -lt 24 -and $null -ne $child;$i++) {
            if ($visited.ContainsKey([string]$child.ProcessId)) { break }
            $visited[[string]$child.ProcessId]=$true
            if ($null -eq $native -and $child.Name -cin @('codex.exe','claude.exe')) { $native=$child }
            if ($child.CommandLine -match '(?i)-File\s+"?[^"\r\n]*start-wd-(agent|tools-consumer)\.ps1(?:"|\s)') { $launcher=$child; break }
            $ancestryPhase='parent_process:'+ $child.ParentProcessId
            $parent=Get-BridgeEvidenceProcess $child.ParentProcessId
            if ($null -ne $parent -and ($parent.ProcessId -ne $child.ParentProcessId -or $parent.CreationDate.ToUniversalTime() -gt $child.CreationDate.ToUniversalTime())) { throw 'Parent process lifetime changed' }
            $child=$parent
        }
} catch { $ancestryError=$_.Exception.Message; $native=$null; $launcher=$null }
if ($null -ne $native -and $native.CommandLine -match '(?i)(?:--resume|\bresume)\s+"?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:"|\s|$)') {
    $nativeId=$Matches[1].ToLowerInvariant()
}
if ($null -ne $launcher) {
    if ($launcher.CommandLine -match '(?i)start-wd-tools-consumer\.ps1') {$observedAgent='codex-tools-1'}
    elseif ($launcher.CommandLine -match '(?i)-Agent\s+"?([a-z][a-z0-9_-]{1,32})(?:"|\s|$)') {$observedAgent=$Matches[1]}
}
$pinStatus='unknown'; $pinError=$null; $generation=$null; $manifestHash=$null
$helperHashes=[ordered]@{}
foreach ($leaf in @('Get-BridgeExecutionEvidence.ps1','Write-BridgeTaskReply.ps1','Write-AgentEvent.ps1','BridgeTaskResult.ps1')) {
    $helperHashes[$leaf]=Get-BridgeEvidenceHash (Join-Path $PSScriptRoot $leaf)
}
if ($env:WD_BRIDGE_BIN) {
    try {
        if ([IO.Path]::GetFullPath($env:WD_BRIDGE_BIN).TrimEnd('\','/') -ine [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\','/')) { throw 'Inherited helper pin differs from executing helper directory' }
        $bundle=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
        $manifestPath=Join-Path $bundle deployment-manifest.json
        $manifestHash=Get-BridgeEvidenceHash $manifestPath
        if (-not $env:WD_REBOOT_EXPECTED_MANIFEST_HASH -or $manifestHash -ine $env:WD_REBOOT_EXPECTED_MANIFEST_HASH) { throw 'Inherited manifest anchor is missing or mismatched' }
        $manifest=Get-Content -LiteralPath $manifestPath -Raw|ConvertFrom-Json
        foreach ($leaf in $helperHashes.Keys) {
            $field=$manifest.files.PSObject.Properties['tools-bootstrap/.agent-bridge/bin/'+$leaf]
            if ($null -eq $field -or $field.Value -cne $helperHashes[$leaf]) { throw ('Packaged helper hash mismatch: '+$leaf) }
        }
        $generation=Split-Path $bundle -Leaf
        if ($null -ne $launcher -and $launcher.CommandLine -notmatch [regex]::Escape($generation)) { throw 'Executing helper generation differs from observed launcher' }
        $pinStatus=if ($null -ne $launcher) {'manifest_and_launcher_verified'} else {'manifest_verified_launcher_unknown'}
    } catch { $pinStatus='mismatch'; $pinError=$_.Exception.Message }
}
[pscustomobject]@{schema='wd.execution-evidence.v1';observed_at_utc=$observed;observation_completed_utc=[datetime]::UtcNow.ToString('o');
    helper_directory=$PSScriptRoot;helper_sha256=$helperHashes;parser_version=$PSVersionTable.PSVersion.ToString();
    inherited_helper_directory=$(if ($env:WD_BRIDGE_BIN) {$env:WD_BRIDGE_BIN} else {$null});
    generation=$generation;manifest_sha256=$manifestHash;pin_status=$pinStatus;pin_error=$pinError;
    observed_agent=$observedAgent;native_conversation_id=$nativeId;native_pid=$(if ($null -ne $native) {$native.ProcessId} else {$null});
    native_process_start_utc=$(if ($null -ne $native) {$native.CreationDate.ToUniversalTime().ToString('o')} else {$null});
    cli_kind=$(if ($null -ne $native) {$native.Name} else {$null});launcher_pid=$(if ($null -ne $launcher) {$launcher.ProcessId} else {$null});
    ancestry_error=$ancestryError;ancestry_failure_phase=$(if($ancestryError){$ancestryPhase}else{$null});
    process_query_method=$script:bridgeEvidenceProcessMethod;cim_error=$script:bridgeEvidenceCimError;
    observation_source='helper_runtime_and_process_ancestry';
    semantic_content_verified=$false;task_completion_verified=$false} | ConvertTo-Json -Depth 8

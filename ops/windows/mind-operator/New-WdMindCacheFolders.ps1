# CANDIDATE v2 (fable-5 draft, 2026-11-19) - NOT REVIEWED - NOT AN INSTALLATION INSTRUCTION.
# Creates the two folder levels of the Mind vector cache. Each level is created NEW by one native create call that
# carries the owner and the access list, and the call itself returns the handle that is then checked: there is no
# create-then-open step and no moment in which a level exists with a default or inherited list.
#
#   -Mode Synthetic -SyntheticRoot <existing folder>   rehearsal and tests: the levels are created under that folder.
#                                                      A volume root is never accepted as a synthetic root.
#   -Mode Production                                   the operator's act. No lane runs it. Takes no path.
#
# The arguments are read by this script itself, in exactly the two forms above (same order, same letter case), so
# that a mistyped line also ends with the command's own line (reason=mode_arguments) and not with a shell error.
# Output: exactly one line, built only from the fixed words below. No path, account name or identifier is printed.
#   (mode word: synthetic, production, or unknown when the arguments were not in one of the two forms)
#   wd-mind-cache-folders <synthetic|production> both_created owner=process_user dacl=protected entries=2
#       allowed=user_and_system inherit=files_and_folders                                             exit 0
#   wd-mind-cache-folders <mode> nothing_created reason=<word>                                         exit 2
#   wd-mind-cache-folders <mode> level1_left reason=<word>           first level exists, second does not: exit 3
#   wd-mind-cache-folders <mode> both_left_unverified reason=<word>  both exist, a check after creation failed: exit 3
#   wd-mind-cache-folders <mode> <word of the state reached> reason=internal                           exit 4
# Nothing is ever removed or changed by this command. A level that was created stays; the line says so.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$Mode = ''
$SyntheticRoot = ''
$ArgumentsOk = $false
$given = @($args)
if ($given.Count -eq 2 -and $given[0] -is [string] -and $given[1] -is [string] -and
        $given[0] -ceq '-Mode' -and $given[1] -ceq 'Production') {
    $Mode = 'Production'; $ArgumentsOk = $true
} elseif ($given.Count -eq 4 -and $given[0] -is [string] -and $given[1] -is [string] -and
        $given[2] -is [string] -and $given[3] -is [string] -and $given[0] -ceq '-Mode' -and
        $given[1] -ceq 'Synthetic' -and $given[2] -ceq '-SyntheticRoot' -and $given[3] -ne '') {
    $Mode = 'Synthetic'; $SyntheticRoot = $given[3]; $ArgumentsOk = $true
}
$FirstName = 'wd-mind-private'
$SecondName = 'vector-cache'
$ProductionParent = 'C:\'
$ModeWord = 'unknown'
if ($ArgumentsOk) { $ModeWord = $Mode.ToLowerInvariant() }
$Handles = New-Object System.Collections.ArrayList
$Created = 0

function Finish([string]$Word, [string]$Reason, [int]$Code) {
    foreach ($h in $script:Handles) { try { [void][WdMindCacheFolders1]::CloseHandle($h) } catch { } }
    $script:Handles.Clear()
    $line = 'wd-mind-cache-folders ' + $script:ModeWord + ' ' + $Word
    if ($Word -ceq 'both_created') {
        $line += ' owner=process_user dacl=protected entries=2 allowed=user_and_system inherit=files_and_folders'
    } else {
        $line += ' reason=' + $Reason
    }
    Write-Output $line
    exit $Code
}

function Get-LevelFault([IntPtr]$Handle, [string]$UserSid, [string]$ParentVolume) {
    $attributes = [WdMindCacheFolders1]::Attributes($Handle)
    if ($attributes -eq [uint32]::MaxValue) { return 'info' }
    if (($attributes -band 0x10) -eq 0 -or ($attributes -band 0x400) -ne 0) { return 'type' }
    $identity = [WdMindCacheFolders1]::Identity($Handle)
    if ($identity -eq '' -or ($identity -split ':')[0] -cne $ParentVolume) { return 'volume' }
    $sddl = [WdMindCacheFolders1]::Sddl($Handle)
    if ($sddl -eq '') { return 'security' }
    $descriptor = New-Object System.Security.AccessControl.RawSecurityDescriptor($sddl)
    if ($null -eq $descriptor.Owner -or $descriptor.Owner.Value -cne $UserSid) { return 'owner' }
    $protected = [System.Security.AccessControl.ControlFlags]::DiscretionaryAclProtected
    if (($descriptor.ControlFlags -band $protected) -eq 0) { return 'protected' }
    $list = $descriptor.DiscretionaryAcl
    if ($null -eq $list -or $list.Count -ne 2) { return 'entries' }
    $seen = @{}
    foreach ($entry in $list) {
        if ($entry -isnot [System.Security.AccessControl.CommonAce]) { return 'entry_type' }
        if ($entry.AceQualifier -ne [System.Security.AccessControl.AceQualifier]::AccessAllowed) { return 'entry_type' }
        # exactly object inherit + container inherit: applies to the folder itself, its files and its subfolders;
        # not inherited from above, not inherit-only
        if ([int]$entry.AceFlags -ne 3) { return 'entry_flags' }
        if ([int]$entry.AccessMask -ne 0x1F01FF) { return 'entry_rights' }
        $seen[$entry.SecurityIdentifier.Value] = $true
    }
    if ($seen.Count -ne 2 -or -not $seen.ContainsKey($UserSid) -or -not $seen.ContainsKey('S-1-5-18')) { return 'entry_sids' }
    return ''
}

function Get-StateWord {
    if ($script:Created -ge 2) { return 'both_left_unverified' }
    if ($script:Created -eq 1) { return 'level1_left' }
    return 'nothing_created'
}

try {
    if (-not $ArgumentsOk) { Finish 'nothing_created' 'mode_arguments' 2 }
    if ([IntPtr]::Size -ne 8) {
        Write-Output ('wd-mind-cache-folders ' + $ModeWord + ' nothing_created reason=not_64_bit'); exit 2
    }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WdMindCacheFolders1 {
  [StructLayout(LayoutKind.Sequential)] public struct UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
  [StructLayout(LayoutKind.Sequential)] public struct OBJECT_ATTRIBUTES { public int Length; public IntPtr RootDirectory; public IntPtr ObjectName; public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService; }
  [StructLayout(LayoutKind.Sequential)] public struct IO_STATUS_BLOCK { public IntPtr Status; public IntPtr Information; }
  [StructLayout(LayoutKind.Sequential, Pack = 4)] public struct INFO { public uint Attributes; public long Creation; public long Access; public long Write; public uint Volume; public uint SizeHigh; public uint SizeLow; public uint Links; public uint IndexHigh; public uint IndexLow; }
  [DllImport("ntdll.dll")] static extern int NtCreateFile(out IntPtr handle, uint access, ref OBJECT_ATTRIBUTES oa, out IO_STATUS_BLOCK iosb, IntPtr alloc, uint attrs, uint share, uint disposition, uint options, IntPtr ea, uint eaLen);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sa, uint disposition, uint flags, IntPtr template);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetFileInformationByHandle(IntPtr h, out INFO info);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern uint GetFinalPathNameByHandleW(IntPtr h, StringBuilder path, uint size, uint flags);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string sddl, uint rev, out IntPtr sd, out uint size);
  [DllImport("advapi32.dll", SetLastError=true)] static extern uint GetSecurityInfo(IntPtr h, int objType, uint info, out IntPtr owner, out IntPtr grp, out IntPtr dacl, out IntPtr sacl, out IntPtr sd);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool ConvertSecurityDescriptorToStringSecurityDescriptorW(IntPtr sd, uint rev, uint info, out IntPtr str, out uint len);
  [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
  // synchronize, read control, read attributes, list directory. List directory makes the system refuse a rename or
  // removal of the folder by name while the handle is held (sharing below allows read and write, never delete).
  const uint Access = 0x00100000 | 0x00020000 | 0x80 | 0x1;
  const uint Share = 3;
  // directory file, synchronous, and never follow a link: the name itself is opened or created
  const uint Options = 0x1 | 0x20 | 0x00200000;
  public const int Collision = unchecked((int)0xC0000035);
  public const int NotCreated = unchecked((int)0xE0000002);
  public const int DescriptorFailed = unchecked((int)0xE0000001);
  public static string Layout() {
    return Marshal.SizeOf(typeof(OBJECT_ATTRIBUTES)) + ":" + Marshal.OffsetOf(typeof(UNICODE_STRING), "Buffer").ToInt32() + ":" + Marshal.OffsetOf(typeof(OBJECT_ATTRIBUTES), "RootDirectory").ToInt32() + ":" + Marshal.SizeOf(typeof(INFO)) + ":" + IntPtr.Size;
  }
  public static IntPtr OpenDir(string path) {
    IntPtr h = CreateFileW(path, Access, Share, IntPtr.Zero, 3, 0x02000000 | 0x00200000, IntPtr.Zero);
    return h == new IntPtr(-1) ? IntPtr.Zero : h;
  }
  static int Nt(IntPtr parent, string name, string sddl, uint disposition, out IntPtr handle) {
    handle = IntPtr.Zero; IntPtr sd = IntPtr.Zero; IntPtr text = IntPtr.Zero; IntPtr pus = IntPtr.Zero; uint size;
    try {
      if (sddl != null && !ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, out sd, out size)) return DescriptorFailed;
      text = Marshal.StringToHGlobalUni(name);
      UNICODE_STRING us = new UNICODE_STRING(); us.Length = (ushort)(name.Length * 2); us.MaximumLength = (ushort)(name.Length * 2 + 2); us.Buffer = text;
      pus = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UNICODE_STRING))); Marshal.StructureToPtr(us, pus, false);
      OBJECT_ATTRIBUTES oa = new OBJECT_ATTRIBUTES(); oa.Length = Marshal.SizeOf(typeof(OBJECT_ATTRIBUTES)); oa.RootDirectory = parent; oa.ObjectName = pus; oa.Attributes = 0x40; oa.SecurityDescriptor = sd;
      IO_STATUS_BLOCK io; IntPtr h;
      int status = NtCreateFile(out h, Access, ref oa, out io, IntPtr.Zero, 0x10, Share, disposition, Options, IntPtr.Zero, 0);
      if (status != 0) return status;
      if (disposition == 2 && io.Information.ToInt64() != 2) { CloseHandle(h); return NotCreated; }
      handle = h; return 0;
    } finally {
      if (pus != IntPtr.Zero) Marshal.FreeHGlobal(pus);
      if (text != IntPtr.Zero) Marshal.FreeHGlobal(text);
      if (sd != IntPtr.Zero) LocalFree(sd);
    }
  }
  // create-only: an existing name of any kind gives Collision and no handle
  public static int CreateNew(IntPtr parent, string name, string sddl, out IntPtr handle) { return Nt(parent, name, sddl, 2, out handle); }
  public static int OpenExisting(IntPtr parent, string name, out IntPtr handle) { return Nt(parent, name, null, 1, out handle); }
  public static uint Attributes(IntPtr h) { INFO i; return GetFileInformationByHandle(h, out i) ? i.Attributes : uint.MaxValue; }
  public static string Identity(IntPtr h) {
    INFO i; if (!GetFileInformationByHandle(h, out i)) return "";
    return i.Volume.ToString("x8") + ":" + i.IndexHigh.ToString("x8") + ":" + i.IndexLow.ToString("x8");
  }
  public static string FinalPath(IntPtr h) {
    StringBuilder b = new StringBuilder(1024); uint n = GetFinalPathNameByHandleW(h, b, 1024, 0);
    return (n == 0 || n >= 1024) ? "" : b.ToString();
  }
  public static string Sddl(IntPtr h) {
    IntPtr o, g, d, s, sd, str; uint len;
    if (GetSecurityInfo(h, 1, 5, out o, out g, out d, out s, out sd) != 0) return "";
    try { if (!ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 5, out str, out len)) return "";
          try { return Marshal.PtrToStringUni(str); } finally { LocalFree(str); } }
    finally { LocalFree(sd); }
  }
}
'@
    if ([WdMindCacheFolders1]::Layout() -cne '48:8:8:52:8') { Finish 'nothing_created' 'layout' 2 }

    # ---- the parent: fixed in production, a non-root scratch folder in synthetic mode
    if ($Mode -ceq 'Production') {
        if ($SyntheticRoot -ne '') { Finish 'nothing_created' 'mode_arguments' 2 }
        $parentPath = $ProductionParent
    } else {
        if ($SyntheticRoot -eq '') { Finish 'nothing_created' 'mode_arguments' 2 }
        if (-not [IO.Path]::IsPathRooted($SyntheticRoot) -or $SyntheticRoot.Contains('..')) { Finish 'nothing_created' 'synthetic_root' 2 }
        $full = [IO.Path]::GetFullPath($SyntheticRoot)
        if ($full.TrimEnd('\').Length -le 2 -or [IO.Path]::GetPathRoot($full) -eq $full) { Finish 'nothing_created' 'synthetic_root' 2 }
        $parentPath = $full
    }
    $parent = [WdMindCacheFolders1]::OpenDir($parentPath)
    if ($parent -eq [IntPtr]::Zero) { Finish 'nothing_created' 'parent_open_failed' 2 }
    [void]$Handles.Add($parent)
    $attributes = [WdMindCacheFolders1]::Attributes($parent)
    if ($attributes -eq [uint32]::MaxValue -or ($attributes -band 0x10) -eq 0 -or ($attributes -band 0x400) -ne 0) {
        Finish 'nothing_created' 'parent_not_plain_directory' 2
    }
    # where the handle really leads, whatever the path text said
    $final = [WdMindCacheFolders1]::FinalPath($parent)
    # the path text of the handle; built without backslash literals
    $bs = [string][char]92
    $prefix = $bs + $bs + '?' + $bs
    $rest = $final
    if ($final.StartsWith($prefix)) { $rest = $final.Substring(4) }
    $rest = $rest.TrimEnd([char]92)
    $finalIsVolumeRoot = ($rest -match '^[A-Za-z]:$') -or ($rest -match '^Volume[{][^{}]*[}]$')
    if ($Mode -ceq 'Production') {
        if ($final -cne ($prefix + 'C:' + $bs)) { Finish 'nothing_created' 'parent_not_expected_root' 2 }
    } elseif ($final -eq '' -or $finalIsVolumeRoot) {
        Finish 'nothing_created' 'synthetic_root' 2
    } elseif ($rest -ine $full.TrimEnd([char]92)) {
        # the typed path did not name the opened folder itself: a link, a short name or an alias in an ancestor
        Finish 'nothing_created' 'synthetic_root_redirected' 2
    }
    $parentIdentity = [WdMindCacheFolders1]::Identity($parent)
    if ($parentIdentity -eq '') { Finish 'nothing_created' 'parent_open_failed' 2 }
    $volume = ($parentIdentity -split ':')[0]

    # ---- owner and list: the user of this process as owner; protected list with exactly two allow entries
    #      (that user and the system account), each applying to the folder, its files and its subfolders
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $sddl = 'O:' + $me + 'D:P(A;OICI;FA;;;' + $me + ')(A;OICI;FA;;;SY)'

    # ---- first level
    $level1 = [IntPtr]::Zero
    $status = [WdMindCacheFolders1]::CreateNew($parent, $FirstName, $sddl, [ref]$level1)
    if ($status -eq [WdMindCacheFolders1]::Collision) { Finish 'nothing_created' 'level1_exists' 2 }
    if ($status -ne 0 -or $level1 -eq [IntPtr]::Zero) { Finish 'nothing_created' 'level1_create_failed' 2 }
    $Created = 1
    [void]$Handles.Add($level1)
    $fault = Get-LevelFault $level1 $me $volume
    if ($fault -ne '') { Finish 'level1_left' ('level1_check_' + $fault) 3 }

    # ---- second level, named relative to the HANDLE of the first level
    $level2 = [IntPtr]::Zero
    $status = [WdMindCacheFolders1]::CreateNew($level1, $SecondName, $sddl, [ref]$level2)
    if ($status -eq [WdMindCacheFolders1]::Collision) { Finish 'level1_left' 'level2_exists' 3 }
    if ($status -ne 0 -or $level2 -eq [IntPtr]::Zero) { Finish 'level1_left' 'level2_create_failed' 3 }
    $Created = 2
    [void]$Handles.Add($level2)
    $fault = Get-LevelFault $level2 $me $volume
    if ($fault -ne '') { Finish 'both_left_unverified' ('level2_check_' + $fault) 3 }

    # ---- the names lead to the very objects that were created, and the parent path still names the same parent
    $again = [IntPtr]::Zero
    if ([WdMindCacheFolders1]::OpenExisting($parent, $FirstName, [ref]$again) -ne 0) { Finish 'both_left_unverified' 'name_check' 3 }
    [void]$Handles.Add($again)
    if ([WdMindCacheFolders1]::Identity($again) -cne [WdMindCacheFolders1]::Identity($level1)) { Finish 'both_left_unverified' 'name_check' 3 }
    $again2 = [IntPtr]::Zero
    if ([WdMindCacheFolders1]::OpenExisting($level1, $SecondName, [ref]$again2) -ne 0) { Finish 'both_left_unverified' 'name_check' 3 }
    [void]$Handles.Add($again2)
    if ([WdMindCacheFolders1]::Identity($again2) -cne [WdMindCacheFolders1]::Identity($level2)) { Finish 'both_left_unverified' 'name_check' 3 }
    $parentAgain = [WdMindCacheFolders1]::OpenDir($parentPath)
    if ($parentAgain -eq [IntPtr]::Zero) { Finish 'both_left_unverified' 'parent_changed' 3 }
    [void]$Handles.Add($parentAgain)
    if ([WdMindCacheFolders1]::Identity($parentAgain) -cne $parentIdentity) { Finish 'both_left_unverified' 'parent_changed' 3 }

    Finish 'both_created' '' 0
} catch {
    Finish (Get-StateWord) 'internal' 4
}

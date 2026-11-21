# Save and compare the machine-root inventory for manual Mind installation and rollback.
# Read-only inventory of the 25 machine-root names that Deploy-WdRebootBundle.ps1 treats as one transaction
# (name list bound to commit 9d125024a, installer lines 1867 to 1920). Under the machine root it only reads:
# it never writes, copies, restores or removes anything there, never follows a link and never prints file content.
# Two forms, read by this script itself (same order, same letter case):
#   -Mode Save    -MachineRoot <folder> -Out <new file outside the machine root>
#   -Mode Compare -MachineRoot <folder> -Baseline <file written by Save>
# The last output line is always:  wd-machine-inventory <save|compare|unknown> <result words>
# Exit codes:
#   0 = Save: saved, and all 25 names are plain files with one link each, readable with owner and access list.
#       Compare (all_same): all 25 names were such plain files at save time and are now; for every name length,
#       content hash, link count, owner and access list text are equal AND it is the same file object with the
#       same write time; the owner and access list of the machine root folder itself are equal too (a new file
#       gets its list from that folder).
#   5 = Compare only (same_content_touched): everything above is equal EXCEPT that at least one name is another
#       file object or has another write time (identity_changed= and time_changed= count them). This is what a
#       restore by copying back or through a new file leaves. It is NOT success by itself: a person decides.
#   2 = refused before or during reading (arguments, machine root, output file, baseline); nothing was saved;
#   3 = Save: saved, but not all 25 are such plain files, or the output was left incomplete or its folder changed
#       after the write; Compare: at least one name is not the same;
#   any other code, or no last line = state unknown.
# What exit 0 of Compare does NOT say: that nothing happened between the two runs (a file written in place and
#   given its old write time back looks the same), that anything was or was not restored, or that anyone approved
#   anything. Exit 0 is not a rollback proof, and exit 5 is not one either.
# How the names are read: ALL 25 names are opened first, each name itself, for reading with sharing that allows
#   other readers only, and all handles are kept until every name has been hashed. While they are kept no process
#   can open any of the names for writing, removal or renaming, so the 25 rows describe one common interval; a name
#   that some process already has open for writing is reported as busy and not read. Identity, size, write time,
#   link count, owner and access list are taken from the open handle before and after hashing, and identity, size
#   and write time once more after the last name was hashed; any change gives kind=changed. The machine root
#   folder itself is kept open from the first check to the last read, so it can not be renamed or removed
#   meanwhile, and its path is checked again after the last read.
# Where Save writes: the output folder is opened and kept before anything is read, its real path is taken from
#   that handle (it must be the typed folder and outside the machine root), and the new file is created by one
#   create-only call RELATIVE TO THAT HANDLE, never by path and never through a link. A folder put in place of the
#   output folder or of one of its ancestors after the check can therefore not move the new file elsewhere.
# Access list notes: Save prints one note line for every name that has entries of its own, a list protected from
#   inheritance or more than one link, and counts them on the last line (own_entry_files= protected_files=
#   multi_link=). One distinct list among the names does NOT show that the entries are inherited: all names can
#   share the same own or protected list. A name written through a new temporary file moved over it is a new object;
#   the owner and the own entries of the old object are not carried over by that, nor by a copy back over it.
# LIMITS (Lead 15:33Z and 16:00Z): the last line of a saved file is its own SHA-256; it shows an accidental change
#   of the saved file, not a change made on purpose by the same user - the value printed by Save must be noted
#   separately and compared with baseline_sha256= printed by Compare. Same-user replacement of a file between two
#   runs with the same bytes, owner and list, and anything a process did before the read, stay unknown.
#   Windows PowerShell 5.1 compiles the small native helper through the temp folder.
#   NOT captured for a name: group, audit list, integrity label, file attributes, alternate data streams (for
#   example the zone mark of a downloaded script), short name, compression or encryption state. Equal rows do not
#   say these are equal. A writer that mapped a file into memory before the read is not seen as busy.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$Names = @(
    'CheckedPowerShellResult.ps1', 'Invoke-WdGrok.ps1', 'start-wd-all.ps1', 'start-wd-agent.ps1',
    'start-wd-codex-lead.ps1', 'start-wd-codex-tools.ps1', 'start-wd-claude-rco.ps1', 'start-wd-claude-rco2.ps1',
    'start-wd-fable-5.ps1', 'wd_supervisor.ps1', 'start-wd-tools-consumer.ps1',
    'wd_supervisor_loop.json', 'wd_supervisor_loop_snapshot.json', 'BOOT_AFTER_REBOOT.md',
    'Set-WdTaskConsoleContainment.ps1', 'WD_LOCAL_GPU_GUIDE.md', 'WD_SWARM_PARALLEL_POLICY_V1.md',
    'Write-WdLaneCurrentState.ps1', 'Get-WdSwarmParallelStatus.ps1',
    'WD_REBOOT_STATE_CURRENT.json', 'WD_REBOOT_STATE_CURRENT.md', 'WD_REBOOT_INTEGRITY_CURRENT.sha256',
    'WD_REBOOT_INTEGRITY_CURRENT.json', 'WD_GROK_MODEL_CURRENT.json', 'WD_GROK_MODEL_CURRENT.md'
)
$ModeWord = 'unknown'
$Separator = [string][char]92
$NewLine = [string][char]10

function Finish([string]$Words, [int]$Code) {
    [Console]::Out.WriteLine('wd-machine-inventory ' + $script:ModeWord + ' ' + $Words)
    exit $Code
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash($Bytes) } finally { $sha.Dispose() }
    return ([System.BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
}

function Add-Native {
    Add-Type -TypeDefinition @'
using System;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class WdMachineInventory4 {
  [StructLayout(LayoutKind.Sequential, Pack = 4)] public struct INFO { public uint Attributes; public long Creation; public long Access; public long Write; public uint Volume; public uint SizeHigh; public uint SizeLow; public uint Links; public uint IndexHigh; public uint IndexLow; }
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sa, uint disposition, uint flags, IntPtr template);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetFileInformationByHandle(IntPtr h, out INFO info);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern uint GetFinalPathNameByHandleW(IntPtr h, StringBuilder path, uint size, uint flags);
  [DllImport("advapi32.dll", SetLastError=true)] static extern uint GetSecurityInfo(IntPtr h, int objType, uint info, out IntPtr owner, out IntPtr grp, out IntPtr dacl, out IntPtr sacl, out IntPtr sd);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool ConvertSecurityDescriptorToStringSecurityDescriptorW(IntPtr sd, uint rev, uint info, out IntPtr str, out uint len);
  [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
  [StructLayout(LayoutKind.Sequential)] public struct UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }
  [StructLayout(LayoutKind.Sequential)] public struct OBJECT_ATTRIBUTES { public int Length; public IntPtr RootDirectory; public IntPtr ObjectName; public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService; }
  [StructLayout(LayoutKind.Sequential)] public struct IO_STATUS_BLOCK { public IntPtr Status; public IntPtr Information; }
  [DllImport("ntdll.dll")] static extern int NtCreateFile(out IntPtr handle, uint access, ref OBJECT_ATTRIBUTES oa, out IO_STATUS_BLOCK iosb, IntPtr alloc, uint attrs, uint share, uint disposition, uint options, IntPtr ea, uint eaLen);
  const uint OpenItself = 0x02000000 | 0x00200000;   // may open a folder; never follow a link
  const string None = "|-|-|-|-|-|-|-";
  // full path of the opened folder when the typed path is a plain folder itself, otherwise an empty text
  public static string FolderFinalPath(string path) {
    IntPtr h = CreateFileW(path, 0x80, 7, IntPtr.Zero, 3, OpenItself, IntPtr.Zero);
    if (h == new IntPtr(-1)) return "";
    try {
      INFO i; if (!GetFileInformationByHandle(h, out i)) return "";
      if ((i.Attributes & 0x10) == 0 || (i.Attributes & 0x400) != 0) return "";
      StringBuilder b = new StringBuilder(2048); uint n = GetFinalPathNameByHandleW(h, b, 2048, 0);
      if (n == 0 || n >= 2048) return "";
      string text = b.ToString(); string prefix = new string((char)92, 2) + "?" + (char)92;
      return text.StartsWith(prefix, StringComparison.Ordinal) ? text.Substring(prefix.Length) : text;
    } finally { CloseHandle(h); }
  }
  static bool ReadSecurity(IntPtr h, out string ownerText, out string access) {
    ownerText = "unreadable"; access = "unreadable";
    IntPtr owner, grp, dacl, sacl, sd, str; uint len; string o, a;
    if (GetSecurityInfo(h, 1, 0x1 | 0x4, out owner, out grp, out dacl, out sacl, out sd) != 0) return false;
    try {
      if (!ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x1, out str, out len)) return false;
      try { o = Marshal.PtrToStringUni(str); } finally { LocalFree(str); }
      if (!ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x4, out str, out len)) return false;
      try { a = Marshal.PtrToStringUni(str); } finally { LocalFree(str); }
    } finally { LocalFree(sd); }
    ownerText = o; access = a; return true;
  }
  // keeps the folder itself open for listing; while it is open no process can open it for renaming or removal
  public static IntPtr KeepFolder(string path) { return CreateFileW(path, 0x00020001, 3, IntPtr.Zero, 3, OpenItself, IntPtr.Zero); }
  public static bool IsKept(IntPtr h) { return h != new IntPtr(-1); }
  public static string KeptFolderPath(IntPtr h) {
    INFO i; if (!GetFileInformationByHandle(h, out i)) return "";
    if ((i.Attributes & 0x10) == 0 || (i.Attributes & 0x400) != 0) return "";
    StringBuilder b = new StringBuilder(2048); uint n = GetFinalPathNameByHandleW(h, b, 2048, 0);
    if (n == 0 || n >= 2048) return "";
    string text = b.ToString(); string prefix = new string((char)92, 2) + "?" + (char)92;
    return text.StartsWith(prefix, StringComparison.Ordinal) ? text.Substring(prefix.Length) : text;
  }
  public static string KeptFolderSecurity(IntPtr h) {
    string o, a; if (!ReadSecurity(h, out o, out a)) return "unreadable|unreadable";
    return o + "|" + a;
  }
  public static void Release(IntPtr h) { if (h != new IntPtr(-1)) CloseHandle(h); }
  static bool SameObject(INFO a, INFO b) {
    return a.Volume == b.Volume && a.IndexHigh == b.IndexHigh && a.IndexLow == b.IndexLow && a.SizeHigh == b.SizeHigh && a.SizeLow == b.SizeLow && a.Write == b.Write && a.Attributes == b.Attributes && a.Links == b.Links;
  }
  // "kind|length|sha256|written|fileid|links|owner|access" for one opened name; the handle stays open
  static string ReadOpened(IntPtr h, out INFO last, out bool isFile) {
    last = new INFO(); isFile = false;
    try {
      INFO a; if (!GetFileInformationByHandle(h, out a)) return "unreadable" + None;
      if ((a.Attributes & 0x400) != 0) return "link" + None;
      if ((a.Attributes & 0x10) != 0) return "folder" + None;
      string owner1, access1; bool sec1 = ReadSecurity(h, out owner1, out access1);
      byte[] hash; long read = 0;
      using (SHA256 sha = SHA256.Create()) {
        byte[] buffer = new byte[65536];
        using (FileStream stream = new FileStream(new SafeFileHandle(h, false), FileAccess.Read, 65536, false)) {
          int n;
          while ((n = stream.Read(buffer, 0, buffer.Length)) > 0) { sha.TransformBlock(buffer, 0, n, null, 0); read += n; }
        }
        sha.TransformFinalBlock(buffer, 0, 0); hash = sha.Hash;
      }
      INFO b; if (!GetFileInformationByHandle(h, out b)) return "unreadable" + None;
      long size = ((long)b.SizeHigh << 32) | b.SizeLow;
      if (!SameObject(a, b) || read != size) return "changed" + None;
      string ownerText, access; bool sec2 = ReadSecurity(h, out ownerText, out access);
      if (sec1 != sec2 || owner1 != ownerText || access1 != access) return "changed" + None;
      StringBuilder hex = new StringBuilder(64);
      foreach (byte x in hash) hex.Append(x.ToString("x2"));
      last = b; isFile = true;
      return "file|" + size.ToString(CultureInfo.InvariantCulture) + "|" + hex + "|" + DateTime.FromFileTimeUtc(b.Write).ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ", CultureInfo.InvariantCulture)
        + "|" + b.Volume.ToString("x8") + ":" + b.IndexHigh.ToString("x8") + ":" + b.IndexLow.ToString("x8") + "|" + b.Links.ToString(CultureInfo.InvariantCulture) + "|" + ownerText + "|" + access;
    } catch (Exception) { return "unreadable" + None; }
  }
  // all names are opened first, readers only may share, and every handle is kept until all names are hashed;
  // then identity, size and write time of every file are read once more; rows come in the order of the names
  public static string[] ReadAll(string root, string[] names) {
    int count = names.Length; IntPtr bad = new IntPtr(-1);
    IntPtr[] hs = new IntPtr[count]; string[] rows = new string[count]; INFO[] last = new INFO[count]; bool[] isFile = new bool[count];
    for (int i = 0; i < count; i++) { hs[i] = bad; rows[i] = "unreadable" + None; }
    try {
      for (int i = 0; i < count; i++) {
        hs[i] = CreateFileW(Path.Combine(root, names[i]), 0x80000000, 1, IntPtr.Zero, 3, OpenItself, IntPtr.Zero);
        if (hs[i] == bad) {
          int e = Marshal.GetLastWin32Error();
          rows[i] = ((e == 2 || e == 3) ? "absent" : (e == 32 ? "busy" : "unreadable")) + None;
        }
      }
      for (int i = 0; i < count; i++) { if (hs[i] != bad) rows[i] = ReadOpened(hs[i], out last[i], out isFile[i]); }
      for (int i = 0; i < count; i++) {
        if (!isFile[i]) continue;
        INFO c; if (!GetFileInformationByHandle(hs[i], out c) || !SameObject(last[i], c)) rows[i] = "changed" + None;
      }
    } finally {
      for (int i = 0; i < count; i++) { if (hs[i] != bad) CloseHandle(hs[i]); }
    }
    return rows;
  }
  // one create-only call relative to the kept folder handle: the name itself, never through a link; no handle when
  // the name exists in any form or the call fails
  public static IntPtr CreateNewUnder(IntPtr parent, string leaf) {
    IntPtr text = IntPtr.Zero; IntPtr pus = IntPtr.Zero; IntPtr bad = new IntPtr(-1);
    // one plain name only: a relative name with a separator would be created below a subfolder
    if (leaf == null || leaf.Length == 0 || leaf == "." || leaf == ".." || leaf.IndexOfAny(new char[] { (char)92, (char)47, (char)58 }) >= 0) return bad;
    try {
      text = Marshal.StringToHGlobalUni(leaf);
      UNICODE_STRING us = new UNICODE_STRING(); us.Length = (ushort)(leaf.Length * 2); us.MaximumLength = (ushort)(leaf.Length * 2 + 2); us.Buffer = text;
      pus = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UNICODE_STRING))); Marshal.StructureToPtr(us, pus, false);
      OBJECT_ATTRIBUTES oa = new OBJECT_ATTRIBUTES(); oa.Length = Marshal.SizeOf(typeof(OBJECT_ATTRIBUTES)); oa.RootDirectory = parent; oa.ObjectName = pus; oa.Attributes = 0x40;
      IO_STATUS_BLOCK io; IntPtr h;
      // write and read attributes, synchronize; normal file; no sharing; create only; a file, synchronous, never follow a link
      int status = NtCreateFile(out h, 0x00120116 | 0x80, ref oa, out io, IntPtr.Zero, 0x80, 0, 2, 0x40 | 0x20 | 0x00200000, IntPtr.Zero, 0);
      if (status != 0) return bad;
      if (io.Information.ToInt64() != 2) { CloseHandle(h); return bad; }
      return h;
    } catch (Exception) { return bad; }
    finally {
      if (pus != IntPtr.Zero) Marshal.FreeHGlobal(pus);
      if (text != IntPtr.Zero) Marshal.FreeHGlobal(text);
    }
  }
  // writes the two parts through the handle of the new file, flushes them to the disk and closes the handle
  public static bool WriteNew(IntPtr h, byte[] body, byte[] tail) {
    SafeFileHandle safe = new SafeFileHandle(h, true);
    try {
      using (FileStream stream = new FileStream(safe, FileAccess.Write, 4096, false)) { stream.Write(body, 0, body.Length); stream.Write(tail, 0, tail.Length); stream.Flush(true); }
      return true;
    } catch (Exception) { return false; }
    finally { safe.Dispose(); }
  }
}
'@
}

function Read-All([string]$Root) {
    $rows = @()
    $lines = [WdMachineInventory4]::ReadAll($Root, [string[]]$script:Names)
    for ($i = 0; $i -lt $script:Names.Count; $i++) {
        $f = ([string]$lines[$i]).Split('|')
        $rows += , ([ordered]@{ name = $script:Names[$i]; kind = $f[0]; length = $f[1]; sha256 = $f[2]; written = $f[3]; fileid = $f[4]; links = $f[5]; owner = $f[6]; access = $f[7] })
    }
    return , $rows
}

function Test-Plain($Row) {
    return ($Row.kind -ceq 'file' -and $Row.links -ceq '1' -and $Row.owner -cne 'unreadable' -and $Row.access -cne 'unreadable')
}

function Get-AccessShape([string]$List) {
    # read from the list text: entries of its own, inherited entries, and whether the list is protected from inheritance
    if (-not $List.StartsWith('D:') -or $List.Contains('NO_ACCESS_CONTROL')) { return 'shape=unknown' }
    $open = $List.IndexOf('(')
    $flags = $List.Substring(2)
    if ($open -ge 0) { $flags = $List.Substring(2, $open - 2) }
    $own = 0; $inherited = 0
    if ($open -ge 0) {
        foreach ($entry in $List.Substring($open).Split(')')) {
            if ($entry -eq '') { continue }
            $parts = $entry.TrimStart('(').Split(';')
            if ($parts.Count -ne 6) { return 'shape=unknown' }
            $isInherited = $false
            for ($k = 0; $k + 1 -lt $parts[1].Length; $k += 2) { if ($parts[1].Substring($k, 2) -ceq 'ID') { $isInherited = $true } }
            if ($isInherited) { $inherited++ } else { $own++ }
        }
    }
    $protected = 'no'
    if ($flags.Contains('P')) { $protected = 'yes' }
    return ('own_entries=' + $own + ' inherited_entries=' + $inherited + ' protected=' + $protected)
}

function Test-RootStill($Kept, [string]$Root) {
    $now = ([WdMachineInventory4]::KeptFolderPath($Kept)).TrimEnd([char]92)
    return [string]::Equals($now, $Root, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-PlainFolder([string]$Typed) {
    # the typed full path when it names an existing plain folder itself; 'redirected' when a link or an alias in an
    # ancestor leads somewhere else; $null otherwise
    if ([string]::IsNullOrEmpty($Typed) -or -not [System.IO.Path]::IsPathRooted($Typed)) { return $null }
    $full = [System.IO.Path]::GetFullPath($Typed).TrimEnd([char]92)
    if ($full.Length -lt 3) { return $null }
    $final = [WdMachineInventory4]::FolderFinalPath($full)
    if ($final -eq '') { return $null }
    if (-not [string]::Equals($final.TrimEnd([char]92), $full, [System.StringComparison]::OrdinalIgnoreCase)) { return 'redirected' }
    return $full
}

try {
    $given = @($args)
    $ok = ($given.Count -eq 6)
    if ($ok) { foreach ($g in $given) { if (-not ($g -is [string]) -or $g -eq '') { $ok = $false } } }
    if ($ok -and $given[0] -ceq '-Mode' -and $given[1] -ceq 'Save' -and $given[2] -ceq '-MachineRoot' -and $given[4] -ceq '-Out') {
        $ModeWord = 'save'
    } elseif ($ok -and $given[0] -ceq '-Mode' -and $given[1] -ceq 'Compare' -and $given[2] -ceq '-MachineRoot' -and $given[4] -ceq '-Baseline') {
        $ModeWord = 'compare'
    } else {
        Finish 'nothing_read reason=mode_arguments' 2
    }
    Add-Native
    $root = Get-PlainFolder $given[3]
    if ($null -eq $root) { Finish 'nothing_read reason=machine_root' 2 }
    if ($root -eq 'redirected') { Finish 'nothing_read reason=machine_root_redirected' 2 }
    $rootPrefix = $root + $Separator
    $other = [string]$given[5]
    if (-not [System.IO.Path]::IsPathRooted($other)) { Finish 'nothing_read reason=file_path' 2 }
    $otherFull = [System.IO.Path]::GetFullPath($other)
    $namesDigest = Get-Sha256Hex ([System.Text.Encoding]::UTF8.GetBytes(($Names -join $NewLine) + $NewLine))

    if ($ModeWord -eq 'save') {
        if ($otherFull.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { Finish 'nothing_read reason=out_inside_machine_root' 2 }
        $outFolder = Get-PlainFolder ([System.IO.Path]::GetDirectoryName($otherFull))
        if ($null -eq $outFolder -or $outFolder -eq 'redirected') { Finish 'nothing_read reason=out_folder' 2 }
        if (($outFolder + $Separator).StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { Finish 'nothing_read reason=out_inside_machine_root' 2 }
        if ([System.IO.File]::Exists($otherFull) -or [System.IO.Directory]::Exists($otherFull)) { Finish 'nothing_read reason=out_exists' 2 }
        $outLeaf = [System.IO.Path]::GetFileName($otherFull)
        if ([string]::IsNullOrEmpty($outLeaf) -or $outLeaf.IndexOfAny([char[]]@([char]92, [char]47, [char]58)) -ge 0 -or $outLeaf -eq '.' -or $outLeaf -eq '..') { Finish 'nothing_read reason=file_path' 2 }
        # the output folder is kept from here to the end of the write; its real path comes from the kept handle
        $outKept = [WdMachineInventory4]::KeepFolder($outFolder)
        if (-not [WdMachineInventory4]::IsKept($outKept)) { Finish 'nothing_read reason=out_folder' 2 }
        $outNow = ([WdMachineInventory4]::KeptFolderPath($outKept)).TrimEnd([char]92)
        if (-not [string]::Equals($outNow, $outFolder, [System.StringComparison]::OrdinalIgnoreCase)) { Finish 'nothing_read reason=out_folder' 2 }
        if (($outNow + $Separator).StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or [string]::Equals($outNow, $root, [System.StringComparison]::OrdinalIgnoreCase)) { Finish 'nothing_read reason=out_inside_machine_root' 2 }
        $kept = [WdMachineInventory4]::KeepFolder($root)
        if (-not [WdMachineInventory4]::IsKept($kept)) { Finish 'nothing_read reason=machine_root_not_kept' 2 }
        try {
            if (-not (Test-RootStill $kept $root)) { Finish 'nothing_read reason=machine_root_redirected' 2 }
            $rows = Read-All $root
            $rootSec = ([WdMachineInventory4]::KeptFolderSecurity($kept)).Split('|')
            $still = Test-RootStill $kept $root
        } finally { [WdMachineInventory4]::Release($kept) }
        if (-not $still) { Finish 'nothing_saved reason=machine_root_changed' 2 }
        $text = New-Object System.Text.StringBuilder
        [void]$text.Append('wd-machine-inventory-v3' + $NewLine)
        [void]$text.Append('root=' + $root + $NewLine)
        [void]$text.Append('taken_utc=' + [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ', [System.Globalization.CultureInfo]::InvariantCulture) + $NewLine)
        [void]$text.Append('names=' + $Names.Count + $NewLine)
        [void]$text.Append('names_sha256=' + $namesDigest + $NewLine)
        [void]$text.Append('root_owner=' + $rootSec[0] + $NewLine)
        [void]$text.Append('root_access=' + $rootSec[1] + $NewLine)
        $rootState = 'read'
        if ($rootSec[0] -ceq 'unreadable' -or $rootSec[1] -ceq 'unreadable') { $rootState = 'unreadable' }
        $files = 0; $absent = 0; $otherKind = 0; $multi = 0; $ownEntry = 0; $protected = 0; $notes = @()
        $ownerSet = New-Object 'System.Collections.Generic.HashSet[string]'
        $listSet = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($r in $rows) {
            if (Test-Plain $r) { $files++ } elseif ($r.kind -eq 'absent') { $absent++ } else { $otherKind++ }
            if ($r.kind -eq 'file') {
                [void]$ownerSet.Add($r.owner); [void]$listSet.Add($r.access)
                $shape = Get-AccessShape $r.access
                $isMulti = ($r.links -cne '1')
                $hasOwn = ($shape.StartsWith('own_entries=') -and -not $shape.StartsWith('own_entries=0 '))
                $isProtected = $shape.EndsWith('protected=yes')
                if ($isMulti) { $multi++ }
                if ($hasOwn) { $ownEntry++ }
                if ($isProtected) { $protected++ }
                if ($isMulti -or $hasOwn -or $isProtected -or $shape -eq 'shape=unknown') { $notes += ('  ' + $r.name + ' note links=' + $r.links + ' ' + $shape) }
            } else {
                $notes += ('  ' + $r.name + ' note kind=' + $r.kind)
            }
            [void]$text.Append(($r.name, $r.kind, $r.length, $r.sha256, $r.written, $r.fileid, $r.links, $r.owner, $r.access -join '|') + $NewLine)
        }
        $body = [System.Text.Encoding]::UTF8.GetBytes($text.ToString())
        $digest = Get-Sha256Hex $body
        $tail = [System.Text.Encoding]::UTF8.GetBytes('inventory_sha256=' + $digest + $NewLine)
        # one create-only call relative to the kept output folder; the path text is not used again
        $outHandle = [WdMachineInventory4]::CreateNewUnder($outKept, $outLeaf)
        if (-not [WdMachineInventory4]::IsKept($outHandle)) { Finish 'nothing_saved reason=out_create_failed' 2 }
        $written = [WdMachineInventory4]::WriteNew($outHandle, $body, $tail)
        $outStill = [string]::Equals(([WdMachineInventory4]::KeptFolderPath($outKept)).TrimEnd([char]92), $outFolder, [System.StringComparison]::OrdinalIgnoreCase)
        [WdMachineInventory4]::Release($outKept)
        if (-not $written) { Finish 'out_left_incomplete reason=out_write_failed' 3 }
        if (-not $outStill) { Finish 'saved_unverified reason=out_folder_changed' 3 }
        foreach ($line in $notes) { [Console]::Out.WriteLine($line) }
        $words = 'files=' + $files + ' absent=' + $absent + ' other=' + $otherKind + ' multi_link=' + $multi + ' own_entry_files=' + $ownEntry + ' protected_files=' + $protected + ' owners=' + $ownerSet.Count + ' lists=' + $listSet.Count + ' root_list=' + $rootState + ' inventory_sha256=' + $digest
        if ($files -eq $Names.Count -and $rootState -ceq 'read') { Finish ('saved_all_files ' + $words) 0 }
        Finish ('saved_not_all_files ' + $words) 3
    }

    try { $bytes = [System.IO.File]::ReadAllBytes($otherFull) } catch { Finish 'nothing_read reason=baseline_unreadable' 2 }
    $all = [System.Text.Encoding]::UTF8.GetString($bytes)
    $marker = $NewLine + 'inventory_sha256='
    $at = $all.LastIndexOf($marker, [System.StringComparison]::Ordinal)
    if ($at -lt 0 -or -not $all.EndsWith($NewLine)) { Finish 'nothing_read reason=baseline_format' 2 }
    $bodyText = $all.Substring(0, $at + 1)
    $claimed = $all.Substring($at + $marker.Length).TrimEnd([char]10)
    $digest = Get-Sha256Hex ([System.Text.Encoding]::UTF8.GetBytes($bodyText))
    if ($claimed -cne $digest) { Finish 'nothing_read reason=baseline_integrity' 2 }
    $lines = @($bodyText.TrimEnd([char]10).Split([char]10))
    if ($lines.Count -ne (7 + $Names.Count) -or $lines[0] -cne 'wd-machine-inventory-v3' -or -not $lines[1].StartsWith('root=') -or $lines[3] -cne ('names=' + $Names.Count)) {
        Finish 'nothing_read reason=baseline_format' 2
    }
    if ($lines[4] -cne ('names_sha256=' + $namesDigest)) { Finish 'nothing_read reason=baseline_other_name_list' 2 }
    if (-not $lines[5].StartsWith('root_owner=') -or -not $lines[6].StartsWith('root_access=')) { Finish 'nothing_read reason=baseline_format' 2 }
    if (-not [string]::Equals($lines[1].Substring(5), $root, [System.StringComparison]::OrdinalIgnoreCase)) { Finish 'nothing_read reason=baseline_other_root' 2 }
    $old = @()
    for ($i = 0; $i -lt $Names.Count; $i++) {
        $f = $lines[7 + $i].Split('|')
        if ($f.Count -ne 9 -or $f[0] -cne $Names[$i]) { Finish 'nothing_read reason=baseline_format' 2 }
        $old += , ([ordered]@{ name = $f[0]; kind = $f[1]; length = $f[2]; sha256 = $f[3]; written = $f[4]; fileid = $f[5]; links = $f[6]; owner = $f[7]; access = $f[8] })
    }
    $kept = [WdMachineInventory4]::KeepFolder($root)
    if (-not [WdMachineInventory4]::IsKept($kept)) { Finish 'nothing_read reason=machine_root_not_kept' 2 }
    try {
        if (-not (Test-RootStill $kept $root)) { Finish 'nothing_read reason=machine_root_redirected' 2 }
        $rows = Read-All $root
        $rootSec = ([WdMachineInventory4]::KeptFolderSecurity($kept)).Split('|')
        $still = Test-RootStill $kept $root
    } finally { [WdMachineInventory4]::Release($kept) }
    if (-not $still) { Finish 'nothing_read reason=machine_root_changed' 2 }
    $same = 0; $different = 0; $missing = 0; $appeared = 0; $absentBoth = 0
    $identityChanged = 0; $timeChanged = 0; $ownerEqual = 0; $accessEqual = 0
    for ($i = 0; $i -lt $Names.Count; $i++) {
        $o = $old[$i]; $n = $rows[$i]
        if ((Test-Plain $o) -and (Test-Plain $n)) {
            $fields = @()
            if ($o.length -cne $n.length) { $fields += 'length' }
            if ($o.sha256 -cne $n.sha256) { $fields += 'content' }
            if ($o.owner -cne $n.owner) { $fields += 'owner' } else { $ownerEqual++ }
            if ($o.access -cne $n.access) { $fields += 'access' } else { $accessEqual++ }
            $moved = @()
            if ($o.fileid -cne $n.fileid) { $identityChanged++; $moved += 'identity' }
            if ($o.written -cne $n.written) { $timeChanged++; $moved += 'time' }
            if ($fields.Count -eq 0) {
                $same++
                if ($moved.Count -gt 0) { [Console]::Out.WriteLine('  ' + $n.name + ' same_content changed=' + ($moved -join ',')) }
            } else {
                $different++
                $line = '  ' + $n.name + ' different fields=' + ($fields -join ',')
                # candidate 5 (RCO2 2026-11-20 08:22Z): no account identifier on the console; 'owner' in the fields list says
                # that the owner changed, and the saved files carry the values. Save again to a new file to record the new owner.
                if ($fields -contains 'owner') { $line += ' owner_changed' }
                if ($fields -contains 'access') { $line += ' list_was(' + (Get-AccessShape $o.access) + ') list_now(' + (Get-AccessShape $n.access) + ')' }
                [Console]::Out.WriteLine($line)
            }
        } elseif ($o.kind -ceq 'absent' -and $n.kind -ceq 'absent') {
            # absent at save time and absent now: equal, but never counted as the same plain file
            $absentBoth++; [Console]::Out.WriteLine('  ' + $n.name + ' absent_both')
        } elseif ((Test-Plain $o) -and $n.kind -ceq 'absent') {
            $missing++; [Console]::Out.WriteLine('  ' + $n.name + ' missing')
        } elseif ($o.kind -ceq 'absent') {
            $appeared++; [Console]::Out.WriteLine('  ' + $n.name + ' appeared now=' + $n.kind)
        } else {
            # a link, a folder, a busy, changed or unreadable object, or more than one link, on either side
            $different++; [Console]::Out.WriteLine('  ' + $n.name + ' different fields=not_comparable was=' + $o.kind + ' now=' + $n.kind + ' links_was=' + $o.links + ' links_now=' + $n.links)
        }
    }
    $rootOwnerEqual = 'no'; $rootAccessEqual = 'no'
    if ($rootSec[0] -cne 'unreadable' -and $lines[5].Substring(11) -ceq $rootSec[0]) { $rootOwnerEqual = 'yes' }
    if ($rootSec[1] -cne 'unreadable' -and $lines[6].Substring(12) -ceq $rootSec[1]) { $rootAccessEqual = 'yes' }
    if ($rootOwnerEqual -ceq 'no' -or $rootAccessEqual -ceq 'no') { [Console]::Out.WriteLine('  (machine root folder itself) different owner_equal=' + $rootOwnerEqual + ' list_equal=' + $rootAccessEqual) }
    $words = 'same=' + $same + ' different=' + $different + ' missing=' + $missing + ' appeared=' + $appeared + ' absent_both=' + $absentBoth + ' identity_changed=' + $identityChanged + ' time_changed=' + $timeChanged + ' owner_equal=' + $ownerEqual + ' access_equal=' + $accessEqual + ' root_owner_equal=' + $rootOwnerEqual + ' root_access_equal=' + $rootAccessEqual + ' baseline_sha256=' + $digest
    if ($same -eq $Names.Count -and $rootOwnerEqual -ceq 'yes' -and $rootAccessEqual -ceq 'yes') {
        # equal content and lists is all_same only when every name is also the same file object with the same write time
        if ($identityChanged -eq 0 -and $timeChanged -eq 0) { Finish ('all_same ' + $words) 0 }
        Finish ('same_content_touched ' + $words) 5
    }
    Finish ('not_same ' + $words) 3
} catch {
    Finish 'unknown_state reason=internal' 1
}

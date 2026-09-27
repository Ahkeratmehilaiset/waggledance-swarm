#requires -Version 5.1
# Windows creation policy: same logon session, minimum wait/release rights.
# Existing objects are opened, never re-ACL'd. Non-Windows keeps the original
# portable .NET mutex behavior; no Windows failure falls back to default ACLs.

function Initialize-BridgeNamedMutexType {
    if ('WaggleDance.BridgeNamedMutexV1' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace WaggleDance {
    public static class BridgeNamedMutexV1 {
        [StructLayout(LayoutKind.Sequential)]
        struct SidAndAttributes { public IntPtr Sid; public uint Attributes; }
        [StructLayout(LayoutKind.Sequential)]
        struct TokenGroups { public uint Count; public SidAndAttributes First; }
        [StructLayout(LayoutKind.Sequential)]
        struct SecurityAttributes {
            public int Length;
            public IntPtr Descriptor;
            [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
        }
        [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
        [DllImport("advapi32.dll", SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
        [DllImport("advapi32.dll", SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr buffer, uint size, out uint needed);
        [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool ConvertSidToStringSidW(IntPtr sid, out IntPtr text);
        [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string sddl, uint revision, out IntPtr descriptor, out uint size);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateMutexExW(ref SecurityAttributes attributes, string name, uint flags, uint access);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr OpenMutexW(uint access, [MarshalAs(UnmanagedType.Bool)] bool inherit, string name);
        [DllImport("advapi32.dll")]
        static extern uint GetSecurityInfo(IntPtr handle, int objectType, uint information,
            out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
        [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool ConvertSecurityDescriptorToStringSecurityDescriptorW(
            IntPtr descriptor, uint revision, uint information, out IntPtr text, out uint length);

        static string DaclText(IntPtr descriptor) {
            IntPtr text; uint length;
            if (!ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, out text, out length))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            try { return Marshal.PtrToStringUni(text); }
            finally { LocalFree(text); }
        }
        // Diagnostic open only. Never widen the operational handle or rewrite ACLs.
        // Keep the operational handle alive while inspecting so the name cannot vanish.
        public static string InspectDacl(string name, string expectedSddl) {
            IntPtr readHandle = OpenMutexW(0x00020000u, false, name);
            if (readHandle == IntPtr.Zero)
                return "bridge_mutex_acl_unverified: READ_CONTROL refused (" + Marshal.GetLastWin32Error() + ")";
            IntPtr descriptor = IntPtr.Zero, expected = IntPtr.Zero;
            try {
                IntPtr owner, group, dacl, sacl;
                uint error = GetSecurityInfo(readHandle, 6, 4, out owner, out group, out dacl, out sacl, out descriptor);
                if (error != 0) return "bridge_mutex_acl_unverified: GetSecurityInfo " + error;
                uint length;
                // The kernel maps GENERIC_ALL to MUTEX_ALL_ACCESS on creation.
                string mappedExpected = expectedSddl.Replace(";;GA;;;", ";;0x001f0001;;;");
                if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(mappedExpected, 1, out expected, out length))
                    return "bridge_mutex_acl_unverified: invalid expected descriptor";
                string observed = DaclText(descriptor);
                if (!String.Equals(observed, DaclText(expected), StringComparison.Ordinal))
                    return "bridge_mutex_acl_mismatch: observed=" + observed;
                return "";
            } catch (Win32Exception ex) {
                return "bridge_mutex_acl_unverified: " + ex.NativeErrorCode;
            } finally {
                if (descriptor != IntPtr.Zero) LocalFree(descriptor);
                if (expected != IntPtr.Zero) LocalFree(expected);
                CloseHandle(readHandle);
            }
        }

        static IntPtr TokenBuffer(IntPtr token, int kind, out uint size) {
            size = 0;
            bool initial = GetTokenInformation(token, kind, IntPtr.Zero, 0, out size);
            int error = Marshal.GetLastWin32Error();
            if (initial || error != 122 || size == 0 || size > 1048576)
                throw new Win32Exception(error, "Token information size unavailable");
            IntPtr buffer = Marshal.AllocHGlobal(checked((int)size));
            uint returned;
            if (!GetTokenInformation(token, kind, buffer, size, out returned)) {
                error = Marshal.GetLastWin32Error();
                Marshal.FreeHGlobal(buffer);
                throw new Win32Exception(error, "Token information unavailable");
            }
            if (returned > size) { Marshal.FreeHGlobal(buffer); throw new InvalidOperationException("Token size changed"); }
            size = returned;
            return buffer;
        }
        static string SidText(IntPtr sid) {
            IntPtr text;
            if (!ConvertSidToStringSidW(sid, out text)) throw new Win32Exception(Marshal.GetLastWin32Error());
            try { return Marshal.PtrToStringUni(text); }
            finally { LocalFree(text); }
        }
        public static string BuildSddl(string user, string[] enabledLogonSids) {
            if (user == null || !Regex.IsMatch(user, @"\AS-1-\d+(?:-\d+)+\z"))
                throw new InvalidOperationException("Invalid token user SID");
            if (enabledLogonSids == null || enabledLogonSids.Length != 1 ||
                enabledLogonSids[0] == null || !Regex.IsMatch(enabledLogonSids[0], @"\AS-1-5-5-\d+-\d+\z"))
                throw new InvalidOperationException("Exactly one enabled token logon SID is required");
            return "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100001;;;" + enabledLogonSids[0] + ")";
        }
        public static string GetCreationSddl() {
            IntPtr token;
            if (!OpenProcessToken(GetCurrentProcess(), 8, out token))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot read creator process token");
            try {
                uint size;
                IntPtr userBuffer = TokenBuffer(token, 1, out size);
                string user;
                try {
                    if (size < Marshal.SizeOf(typeof(SidAndAttributes))) throw new InvalidOperationException("Truncated token user");
                    user = SidText(Marshal.ReadIntPtr(userBuffer));
                } finally { Marshal.FreeHGlobal(userBuffer); }
                IntPtr groups = TokenBuffer(token, 2, out size);
                try {
                    if (size < 4) throw new InvalidOperationException("Truncated token groups");
                    uint count = unchecked((uint)Marshal.ReadInt32(groups));
                    int start = Marshal.OffsetOf(typeof(TokenGroups), "First").ToInt32();
                    int stride = Marshal.SizeOf(typeof(SidAndAttributes));
                    if ((ulong)start + (ulong)count * (ulong)stride > size)
                        throw new InvalidOperationException("Truncated token groups array");
                    var logons = new System.Collections.Generic.List<string>();
                    for (uint i=0; i<count; i++) {
                        var group = (SidAndAttributes)Marshal.PtrToStructure(
                            IntPtr.Add(groups, checked(start + (int)i * stride)), typeof(SidAndAttributes));
                        if ((group.Attributes & 0xC0000000u) == 0xC0000000u) {
                            if ((group.Attributes & 4u) == 0 || (group.Attributes & 16u) != 0)
                                throw new InvalidOperationException("Token logon SID is not enabled for grants");
                            logons.Add(SidText(group.Sid));
                        }
                    }
                    return BuildSddl(user, logons.ToArray());
                } finally { Marshal.FreeHGlobal(groups); }
            } finally { CloseHandle(token); }
        }
        public static Mutex Create(string name, out bool createdNew) {
            if (String.IsNullOrEmpty(name)) throw new ArgumentException("Named bridge mutex required");
            string sddl = GetCreationSddl(); // No environment/config identity overrides.
            IntPtr descriptor;
            uint size;
            if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, out descriptor, out size))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            IntPtr handle = IntPtr.Zero;
            try {
                var attributes = new SecurityAttributes {
                    Length=Marshal.SizeOf(typeof(SecurityAttributes)), Descriptor=descriptor, Inherit=false
                };
                handle = CreateMutexExW(ref attributes, name, 0, 0x00100001u);
                int error = Marshal.GetLastWin32Error();
                if (handle == IntPtr.Zero) throw new Win32Exception(error, "Bridge mutex create/open refused");
                createdNew = error != 183;
                // Reuse .NET's established timeout/abandoned/release semantics.
                var mutex = new Mutex(false);
                var initial = mutex.SafeWaitHandle;
                var owned = new SafeWaitHandle(handle, true);
                handle = IntPtr.Zero;
                try {
                    mutex.SafeWaitHandle = owned;
                } catch { owned.Dispose(); mutex.Dispose(); throw; }
                finally { initial.Dispose(); }
                return mutex;
            } finally {
                if (handle != IntPtr.Zero) CloseHandle(handle);
                LocalFree(descriptor);
            }
        }
    }
}
'@
}

function New-BridgeNamedMutex {
    param([Parameter(Mandatory)] [string] $Name)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        return New-Object System.Threading.Mutex($false, $Name)
    }
    Initialize-BridgeNamedMutexType
    $createdNew = $false
    $mutex = [WaggleDance.BridgeNamedMutexV1]::Create($Name, [ref]$createdNew)
    try {
        if (-not $createdNew) {
            $diagnostic = [WaggleDance.BridgeNamedMutexV1]::InspectDacl(
                $Name, [WaggleDance.BridgeNamedMutexV1]::GetCreationSddl())
            if ($diagnostic) {
                # Do not emit a bridge event here: that would recursively acquire
                # these same locks. The caller's warning/log channel is evidence.
                Write-Warning ("{0}: {1}" -f $Name, $diagnostic)
            }
        }
        return $mutex
    } catch { $mutex.Dispose(); throw }
}

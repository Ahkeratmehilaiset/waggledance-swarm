"""Operator-only creation of the fixed Mind vector-cache directory.

No existing object is changed or removed.  A failure after creation may leave
an empty protected parent, requiring operator inspection before retry.  This
does not authenticate against another process running as the same user or an
administrator; those risks remain UNKNOWN.  No policy or content is read.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys

_BASE = Path('C:\\')
_PARENT_NAME = 'wd-mind-private'
_CHILD_NAME = 'vector-cache'
_COLLISION = 0xC0000035
_NT_SUCCESS = 0
_FILE_CREATED = 2
_SE_DACL_PROTECTED = 0x1000
_FILE_ALL_ACCESS = 0x1F01FF
# The volume-root DACL scan is not proof of all effective tokens/privileges,
# and the lane-to-operator SID binding is not yet established. Never expose a
# runnable operator command until those gaps have been independently closed.
_PROVISION_PROOF_COMPLETE = False


class ProvisionRefused(ValueError):
    """A fixed non-sensitive refusal; never erase a partially created object."""


class _UnicodeString(ctypes.Structure):
    _fields_ = [('length', wintypes.USHORT), ('maximum_length', wintypes.USHORT),
                ('buffer', wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [('length', wintypes.ULONG), ('root', wintypes.HANDLE),
                ('name', ctypes.POINTER(_UnicodeString)), ('attributes', wintypes.ULONG),
                ('security_descriptor', ctypes.c_void_p), ('quality', ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [('status_or_pointer', ctypes.c_void_p), ('information', ctypes.c_size_t)]


class _FileTime(ctypes.Structure):
    _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]


class _FileInfo(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('creation', _FileTime), ('access', _FileTime),
                ('write', _FileTime), ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


class _Acl(ctypes.Structure):
    _fields_ = [('revision', ctypes.c_ubyte), ('pad', ctypes.c_ubyte), ('size', wintypes.WORD),
                ('count', wintypes.WORD), ('pad2', wintypes.WORD)]


class _Native:
    def __init__(self):
        if os.name != 'nt':
            raise ProvisionRefused('windows_required')
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.a = ctypes.WinDLL('advapi32', use_last_error=True)
        self.nt = ctypes.WinDLL('ntdll', use_last_error=True)
        self.k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.k.CreateFileW.restype = wintypes.HANDLE
        self.k.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
        self.k.GetFileInformationByHandle.restype = wintypes.BOOL
        self.k.GetFileType.argtypes = [wintypes.HANDLE]
        self.k.GetFileType.restype = wintypes.DWORD
        self.k.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                      wintypes.DWORD, wintypes.DWORD]
        self.k.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self.k.GetCurrentProcess.restype = wintypes.HANDLE
        self.k.GetCurrentThread.restype = wintypes.HANDLE
        self.k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k.CloseHandle.restype = wintypes.BOOL
        self.k.LocalFree.argtypes = [ctypes.c_void_p]
        self.k.LocalFree.restype = ctypes.c_void_p
        self.a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.a.OpenProcessToken.restype = wintypes.BOOL
        self.a.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL,
                                            ctypes.POINTER(wintypes.HANDLE)]
        self.a.OpenThreadToken.restype = wintypes.BOOL
        self.a.GetTokenInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.a.GetTokenInformation.restype = wintypes.BOOL
        self.a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        self.a.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.a.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        self.a.ConvertStringSidToSidW.restype = wintypes.BOOL
        self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        self.a.GetSecurityInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.a.GetSecurityInfo.restype = wintypes.DWORD
        self.a.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
                                                         ctypes.POINTER(wintypes.DWORD)]
        self.a.GetSecurityDescriptorControl.restype = wintypes.BOOL
        self.a.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        self.a.GetAce.restype = wintypes.BOOL
        self.a.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.a.EqualSid.restype = wintypes.BOOL
        self.nt.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
            ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
            wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
        self.nt.NtCreateFile.restype = ctypes.c_long

    def open_directory(self, path):
        # Pin each ancestor without FILE_SHARE_DELETE, and do not follow a
        # reparse point.  Handles remain open until both creations finish.
        handle = self.k.CreateFileW(str(path), 0x20080, 3, None, 3, 0x02200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ProvisionRefused('ancestor_open')
        try:
            self.verify_directory(handle)
            return handle
        except BaseException:
            self.k.CloseHandle(handle)
            raise

    def verify_directory(self, handle):
        info = _FileInfo()
        if (not self.k.GetFileInformationByHandle(handle, ctypes.byref(info))
                or self.k.GetFileType(handle) != 1 or not info.attributes & 0x10
                or info.attributes & 0x400):
            raise ProvisionRefused('directory_identity')

    def verify_final_path(self, handle, expected):
        # A no-delete-share directory handle does not necessarily prevent the
        # directory's name changing.  Its final path is checked explicitly;
        # same-user and privileged namespace races remain UNKNOWN.
        buffer = ctypes.create_unicode_buffer(32768)
        length = self.k.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not 0 < length < len(buffer):
            raise ProvisionRefused('final_path')
        value = buffer.value
        if not value.startswith('\\\\?\\') or value.startswith('\\\\?\\UNC\\'):
            raise ProvisionRefused('final_path')
        actual = value[4:]
        if os.path.normcase(os.path.normpath(actual)) != os.path.normcase(os.path.normpath(str(expected))):
            raise ProvisionRefused('final_path')

    def verify_namespace(self, handle, expected_sid):
        """Reject namespace control by untrusted ACEs on the pinned ancestor.

        This conservative DACL check is not an effective-token or privilege
        proof. Same-user, SYSTEM, administrator and TrustedInstaller actors
        remain outside this protection boundary.
        """
        owner, acl, security = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        trusted = []
        try:
            for value in (expected_sid, 'S-1-5-18', 'S-1-5-32-544',
                          'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'):
                sid = ctypes.c_void_p()
                if not self.a.ConvertStringSidToSidW(value, ctypes.byref(sid)):
                    raise ProvisionRefused('namespace_sid')
                trusted.append(sid)
            if self.a.GetSecurityInfo(handle, 1, 5, ctypes.byref(owner), None, ctypes.byref(acl),
                                      None, ctypes.byref(security)) or not owner or not acl:
                raise ProvisionRefused('namespace_acl')
            if not any(self.a.EqualSid(owner, sid) for sid in trusted):
                raise ProvisionRefused('namespace_owner')
            entries = ctypes.cast(acl, ctypes.POINTER(_Acl)).contents
            if not 0 < entries.count <= 4096:
                raise ProvisionRefused('namespace_acl')
            # DELETE_CHILD/DELETE/WRITE_DAC/WRITE_OWNER/WRITE_ATTRIBUTES and
            # generic write/all may permit a name, reparse or DACL change.
            dangerous = 0x100 | 0x40 | 0x10000 | 0x40000 | 0x80000 | 0x40000000 | 0x10000000
            for index in range(entries.count):
                ace = ctypes.c_void_p()
                if not self.a.GetAce(acl, index, ctypes.byref(ace)) or not ace:
                    raise ProvisionRefused('namespace_acl')
                ace_type = ctypes.c_ubyte.from_address(ace.value).value
                flags = ctypes.c_ubyte.from_address(ace.value + 1).value
                size = ctypes.c_ushort.from_address(ace.value + 2).value
                if ace_type not in (0, 1) or size < 16:
                    raise ProvisionRefused('namespace_acl')
                if ace_type == 1 or flags & 0x08:  # deny or inherit-only
                    continue
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                trustee = ctypes.c_void_p(ace.value + 8)
                if mask & dangerous and not any(self.a.EqualSid(trustee, sid) for sid in trusted):
                    raise ProvisionRefused('namespace_untrusted_control')
        finally:
            for item in [security, *trusted]:
                if item:
                    self.k.LocalFree(item)

    def token_identity(self):
        token, thread_token, sid_text = wintypes.HANDLE(), wintypes.HANDLE(), wintypes.LPWSTR()
        try:
            # The descriptor and access checks use the process user. Refuse
            # an impersonating thread rather than guessing its effective user.
            if self.a.OpenThreadToken(self.k.GetCurrentThread(), 8, True, ctypes.byref(thread_token)):
                raise ProvisionRefused('impersonation')
            if ctypes.get_last_error() != 1008:  # ERROR_NO_TOKEN
                raise ProvisionRefused('thread_token')
            if not self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, ctypes.byref(token)):
                raise ProvisionRefused('token')
            size = wintypes.DWORD()
            self.a.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise ProvisionRefused('token')
            user_data = ctypes.create_string_buffer(size.value)
            if not self.a.GetTokenInformation(token, 1, user_data, size.value, ctypes.byref(size)):
                raise ProvisionRefused('token')
            user = ctypes.cast(user_data, ctypes.POINTER(ctypes.c_void_p))[0]
            if not user or not self.a.ConvertSidToStringSidW(user, ctypes.byref(sid_text)):
                raise ProvisionRefused('token')
            return sid_text.value
        finally:
            if sid_text:
                self.k.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
            if token:
                self.k.CloseHandle(token)
            if thread_token:
                self.k.CloseHandle(thread_token)

    def descriptor(self, sid):
        descriptor = ctypes.c_void_p()
        sddl = f'O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)'
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1,
                ctypes.byref(descriptor), None):
            raise ProvisionRefused('descriptor')
        return descriptor

    def create_relative(self, parent, name, descriptor):
        name_buffer = ctypes.create_unicode_buffer(name)
        text = _UnicodeString(len(name) * 2, (len(name) + 1) * 2,
                              ctypes.cast(name_buffer, wintypes.LPWSTR))
        obj = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(text),
                                0x40, descriptor, None)
        status_block, handle = _IoStatusBlock(), wintypes.HANDLE()
        status = self.nt.NtCreateFile(ctypes.byref(handle), 0x120085, ctypes.byref(obj),
            ctypes.byref(status_block), None, 0x10, 3, 2, 0x00200021, None, 0) & 0xFFFFFFFF
        if status == _COLLISION:
            raise ProvisionRefused('already_exists')
        if status != _NT_SUCCESS:
            raise ProvisionRefused('create')
        try:
            if status_block.information != _FILE_CREATED:
                raise ProvisionRefused('not_created')
            self.verify_directory(handle)
            return handle
        except BaseException:
            self.k.CloseHandle(handle)
            raise

    def verify_private(self, handle, expected_sid):
        owner, acl, security, user, system = (ctypes.c_void_p() for _ in range(5))
        try:
            if (not self.a.ConvertStringSidToSidW(expected_sid, ctypes.byref(user))
                    or not self.a.ConvertStringSidToSidW('S-1-5-18', ctypes.byref(system))):
                raise ProvisionRefused('sid')
            if self.a.EqualSid(user, system):
                raise ProvisionRefused('operator_is_system')
            if self.a.GetSecurityInfo(handle, 1, 5, ctypes.byref(owner), None, ctypes.byref(acl),
                                      None, ctypes.byref(security)) or not owner or not acl:
                raise ProvisionRefused('acl')
            if not self.a.EqualSid(owner, user):
                raise ProvisionRefused('owner')
            control, revision = wintypes.WORD(), wintypes.DWORD()
            if (not self.a.GetSecurityDescriptorControl(security, ctypes.byref(control), ctypes.byref(revision))
                    or not control.value & _SE_DACL_PROTECTED):
                raise ProvisionRefused('dacl_not_protected')
            entries = ctypes.cast(acl, ctypes.POINTER(_Acl)).contents
            if entries.count != 2:
                raise ProvisionRefused('acl_entries')
            seen = set()
            for index in range(entries.count):
                ace = ctypes.c_void_p()
                if not self.a.GetAce(acl, index, ctypes.byref(ace)) or not ace:
                    raise ProvisionRefused('acl')
                ace_type = ctypes.c_ubyte.from_address(ace.value).value
                flags = ctypes.c_ubyte.from_address(ace.value + 1).value
                size = ctypes.c_ushort.from_address(ace.value + 2).value
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                if ace_type != 0 or flags != 3 or size < 16 or mask != _FILE_ALL_ACCESS:
                    raise ProvisionRefused('acl_entry')
                trustee = ctypes.c_void_p(ace.value + 8)
                if self.a.EqualSid(trustee, user):
                    seen.add('operator')
                elif self.a.EqualSid(trustee, system):
                    seen.add('system')
                else:
                    raise ProvisionRefused('acl_entry')
            if seen != {'operator', 'system'}:
                raise ProvisionRefused('acl_entry')
        finally:
            for item in (security, user, system):
                if item:
                    self.k.LocalFree(item)


def _provision():
    base = Path(_BASE)
    if not base.is_absolute() or base.drive.upper() != 'C:' or '..' in base.parts:
        raise ProvisionRefused('base')
    # Names are fixed constants in the operator CLI; no path input is accepted.
    native, handles, descriptor = _Native(), [], None
    try:
        sid = native.token_identity()
        if sid == 'S-1-5-18':
            raise ProvisionRefused('operator_is_system')
        current = Path(base.anchor)
        for part in (None, *base.parts[1:]):
            if part is not None:
                current /= part
            handles.append(native.open_directory(current))
            native.verify_final_path(handles[-1], current)
            native.verify_namespace(handles[-1], sid)
        descriptor = native.descriptor(sid)
        parent = native.create_relative(handles[-1], _PARENT_NAME, descriptor)
        handles.append(parent)
        native.verify_final_path(parent, base / _PARENT_NAME)
        native.verify_private(parent, sid)
        child = native.create_relative(parent, _CHILD_NAME, descriptor)
        handles.append(child)
        native.verify_final_path(child, base / _PARENT_NAME / _CHILD_NAME)
        native.verify_private(child, sid)
        # Recheck both while the two no-delete-share handles are still pinned.
        native.verify_final_path(parent, base / _PARENT_NAME)
        native.verify_final_path(child, base / _PARENT_NAME / _CHILD_NAME)
        native.verify_private(parent, sid)
        native.verify_private(child, sid)
    finally:
        for handle in reversed(handles):
            native.k.CloseHandle(handle)
        if descriptor:
            native.k.LocalFree(descriptor)


def main():
    if len(sys.argv) != 1:
        print('provision_refused:arguments', file=sys.stderr)
        return 2
    if _PROVISION_PROOF_COMPLETE is not True:
        print('provision_refused:namespace_proof_incomplete', file=sys.stderr)
        return 1
    try:
        _provision()
    except ProvisionRefused as exc:
        print(f'provision_refused:{exc}', file=sys.stderr)
        return 1
    except Exception:
        print('provision_refused:internal', file=sys.stderr)
        return 1
    print('provisioned:C:\\wd-mind-private\\vector-cache')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

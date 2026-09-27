# SPDX-License-Identifier: Apache-2.0
"""Fail-closed Windows creation policy for the bridge's named mutexes.

The security descriptor applies only when a name is new. An existing object is
never adopted by changing its ACL; a separate read-only diagnostic checks it.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
import sys
from typing import Any


MUTEX_ACCESS = 0x00100001  # SYNCHRONIZE | MUTEX_MODIFY_STATE
READ_CONTROL = 0x00020000
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ALREADY_EXISTS = 183
SE_GROUP_ENABLED = 0x00000004
SE_GROUP_USE_FOR_DENY_ONLY = 0x00000010
SE_GROUP_LOGON_ID = 0xC0000000


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class _TokenGroupsOne(ctypes.Structure):
    _fields_ = [("GroupCount", wintypes.DWORD), ("Groups", _SidAndAttributes * 1)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _winerror(operation: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{operation} failed: {ctypes.FormatError(code).strip()}")


def _configure(kernel32: Any, advapi32: Any) -> None:
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    kernel32.CreateMutexExW.argtypes = [
        ctypes.POINTER(_SecurityAttributes), wintypes.LPCWSTR,
        wintypes.DWORD, wintypes.DWORD,
    ]
    kernel32.CreateMutexExW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL


def _token_info(advapi32: Any, token: int, kind: int) -> ctypes.Array[Any]:
    needed = wintypes.DWORD()
    ok = advapi32.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
    if ok or ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or not 0 < needed.value <= 1_048_576:
        raise _winerror("GetTokenInformation(size)")
    buffer = ctypes.create_string_buffer(needed.value)
    returned = wintypes.DWORD()
    if not advapi32.GetTokenInformation(token, kind, buffer, needed.value, ctypes.byref(returned)):
        raise _winerror("GetTokenInformation")
    if returned.value > needed.value:
        raise ValueError("token information size changed")
    return buffer


def _sid_text(kernel32: Any, advapi32: Any, sid: int) -> str:
    if not sid:
        raise ValueError("token contains a null SID")
    text = wintypes.LPVOID()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _winerror("ConvertSidToStringSidW")
    try:
        return ctypes.wstring_at(text)
    finally:
        kernel32.LocalFree(text)


def _select_logon_sid(groups: list[tuple[str, int]]) -> str:
    logons: list[str] = []
    for sid, attributes in groups:
        if attributes & SE_GROUP_LOGON_ID != SE_GROUP_LOGON_ID:
            continue
        if not attributes & SE_GROUP_ENABLED or attributes & SE_GROUP_USE_FOR_DENY_ONLY:
            raise ValueError("token logon SID is not enabled for grants")
        logons.append(sid)
    if len(logons) != 1 or not re.fullmatch(r"S-1-5-5-\d+-\d+", logons[0]):
        raise ValueError("exactly one enabled token logon SID is required")
    return logons[0]


def _creation_sddl(kernel32: Any, advapi32: Any) -> str:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise _winerror("OpenProcessToken")
    try:
        user_info = _token_info(advapi32, token, 1)  # TokenUser
        if ctypes.sizeof(user_info) < ctypes.sizeof(_SidAndAttributes):
            raise ValueError("truncated token user")
        user = ctypes.cast(user_info, ctypes.POINTER(_SidAndAttributes)).contents
        user_sid = _sid_text(kernel32, advapi32, user.Sid)
        if not re.fullmatch(r"S-1-\d+(?:-\d+)+", user_sid):
            raise ValueError("invalid token user SID")
        group_info = _token_info(advapi32, token, 2)  # TokenGroups
        start = _TokenGroupsOne.Groups.offset
        if ctypes.sizeof(group_info) < start:
            raise ValueError("truncated token groups")
        count = ctypes.cast(group_info, ctypes.POINTER(wintypes.DWORD)).contents.value
        stride = ctypes.sizeof(_SidAndAttributes)
        if start + count * stride > ctypes.sizeof(group_info):
            raise ValueError("truncated token groups array")
        groups = []
        for index in range(count):
            address = ctypes.addressof(group_info) + start + index * stride
            item = ctypes.cast(address, ctypes.POINTER(_SidAndAttributes)).contents
            if item.Attributes & SE_GROUP_LOGON_ID == SE_GROUP_LOGON_ID:
                groups.append((_sid_text(kernel32, advapi32, item.Sid), item.Attributes))
        logon_sid = _select_logon_sid(groups)
        return f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100001;;;{logon_sid})"
    finally:
        kernel32.CloseHandle(token)


def _descriptor(kernel32: Any, advapi32: Any, sddl: str) -> wintypes.LPVOID:
    descriptor = wintypes.LPVOID()
    size = wintypes.DWORD()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), ctypes.byref(size)
    ):
        raise _winerror("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    if not descriptor:
        raise ValueError("empty security descriptor")
    return descriptor


def _dacl_text(kernel32: Any, advapi32: Any, descriptor: int) -> str:
    text = wintypes.LPVOID()
    length = wintypes.DWORD()
    if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor, 1, 4, ctypes.byref(text), ctypes.byref(length)
    ):
        raise _winerror("ConvertSecurityDescriptorToStringSecurityDescriptorW")
    try:
        return ctypes.wstring_at(text)
    finally:
        kernel32.LocalFree(text)


def _dacl_shape(sddl: str) -> tuple[str, ...]:
    """Compare normalized allow ACEs independent of their harmless order."""
    if not sddl.startswith("D:"):
        return (sddl,)
    return tuple(sorted(re.findall(r"\([^()]+\)", sddl)))


def _inspect_existing(name: str, expected: str, kernel32: Any, advapi32: Any) -> str:
    diagnostic = kernel32.OpenMutexW(READ_CONTROL, False, name)
    if not diagnostic:
        return f"bridge_mutex_acl_unverified: READ_CONTROL refused ({ctypes.get_last_error()})"
    descriptor = wintypes.LPVOID()
    expected_descriptor = wintypes.LPVOID()
    try:
        owner = wintypes.LPVOID()
        group = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        sacl = wintypes.LPVOID()
        error = advapi32.GetSecurityInfo(
            diagnostic, 6, 4, ctypes.byref(owner), ctypes.byref(group),
            ctypes.byref(dacl), ctypes.byref(sacl), ctypes.byref(descriptor),
        )
        if error:
            return f"bridge_mutex_acl_unverified: GetSecurityInfo {error}"
        expected_descriptor = _descriptor(
            kernel32, advapi32, expected.replace(";;GA;;;", ";;0x001f0001;;;")
        )
        observed = _dacl_text(kernel32, advapi32, descriptor)
        normalized_expected = _dacl_text(kernel32, advapi32, expected_descriptor)
        if _dacl_shape(observed) != _dacl_shape(normalized_expected):
            return f"bridge_mutex_acl_mismatch: observed={observed}"
        return ""
    finally:
        if descriptor:
            kernel32.LocalFree(descriptor)
        if expected_descriptor:
            kernel32.LocalFree(expected_descriptor)
        kernel32.CloseHandle(diagnostic)


def create_bridge_named_mutex(
    name: str, *, kernel32: Any | None = None, advapi32: Any | None = None,
) -> int:
    """Atomically create/open with minimum rights; caller owns returned handle."""
    if os.name != "nt":
        raise OSError("Windows named mutex required")
    if not name:
        raise ValueError("named bridge mutex required")
    kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = advapi32 or ctypes.WinDLL("advapi32", use_last_error=True)
    _configure(kernel32, advapi32)
    sddl = _creation_sddl(kernel32, advapi32)
    descriptor = _descriptor(kernel32, advapi32, sddl)
    handle = 0
    try:
        attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        handle = kernel32.CreateMutexExW(ctypes.byref(attributes), name, 0, MUTEX_ACCESS)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, f"CreateMutexExW failed for {name}")
    finally:
        kernel32.LocalFree(descriptor)
    if error == ERROR_ALREADY_EXISTS:
        try:
            diagnostic = _inspect_existing(name, sddl, kernel32, advapi32)
        except Exception as exc:
            diagnostic = f"bridge_mutex_acl_unverified: {exc}"
        if diagnostic:
            print(f"{name}: {diagnostic}", file=sys.stderr)
    return handle

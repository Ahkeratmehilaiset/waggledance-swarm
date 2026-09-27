# SPDX-License-Identifier: Apache-2.0
"""Portable policy tests and unique-name Windows smoke for bridge mutexes."""

from __future__ import annotations

import ctypes
import os
import re
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

import tools.bridge_named_mutex as named_mutex


LOGON = "S-1-5-5-0-367215"
EXPECTED = f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100001;;;{LOGON})"


@pytest.mark.parametrize("groups", [
    [],
    [(LOGON, named_mutex.SE_GROUP_LOGON_ID)],
    [(LOGON, named_mutex.SE_GROUP_LOGON_ID | named_mutex.SE_GROUP_USE_FOR_DENY_ONLY)],
    [(LOGON, named_mutex.SE_GROUP_LOGON_ID | named_mutex.SE_GROUP_ENABLED)] * 2,
    [("S-1-5-32-545", named_mutex.SE_GROUP_LOGON_ID | named_mutex.SE_GROUP_ENABLED)],
])
def test_logon_sid_selection_fails_closed(groups):
    with pytest.raises(ValueError):
        named_mutex._select_logon_sid(groups)


def test_logon_sid_selection_ignores_other_groups():
    assert named_mutex._select_logon_sid([
        ("S-1-5-32-545", named_mutex.SE_GROUP_ENABLED),
        (LOGON, named_mutex.SE_GROUP_LOGON_ID | named_mutex.SE_GROUP_ENABLED),
    ]) == LOGON


def test_dacl_shape_compares_ace_order_but_not_rights():
    assert named_mutex._dacl_shape(EXPECTED) == named_mutex._dacl_shape(
        f"D:(A;;0x00100001;;;{LOGON})(A;;GA;;;BA)(A;;GA;;;SY)"
    )
    assert named_mutex._dacl_shape(EXPECTED) != named_mutex._dacl_shape(
        f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{LOGON})"
    )


@pytest.fixture
def fake_creation(monkeypatch):
    kernel = SimpleNamespace(
        CreateMutexExW=Mock(return_value=42),
        LocalFree=Mock(return_value=None),
    )
    advapi = SimpleNamespace()
    monkeypatch.setattr(named_mutex, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(named_mutex, "_configure", lambda *args: None)
    monkeypatch.setattr(named_mutex, "_creation_sddl", lambda *args: EXPECTED)
    monkeypatch.setattr(named_mutex, "_descriptor", lambda *args: ctypes.c_void_p(123))
    return kernel, advapi


def test_atomic_create_uses_exact_noninheritable_dacl_and_minimum_rights(fake_creation, monkeypatch):
    kernel, advapi = fake_creation
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    assert named_mutex.create_bridge_named_mutex("unique-test", kernel32=kernel, advapi32=advapi) == 42
    attributes, name, flags, rights = kernel.CreateMutexExW.call_args.args
    assert name == "unique-test"
    assert flags == 0
    assert rights == named_mutex.MUTEX_ACCESS == 0x00100001
    assert attributes._obj.nLength == ctypes.sizeof(named_mutex._SecurityAttributes)
    assert attributes._obj.lpSecurityDescriptor == 123
    assert not attributes._obj.bInheritHandle
    kernel.LocalFree.assert_called_once()


def test_existing_object_dacl_mismatch_is_diagnostic_only(fake_creation, monkeypatch, capsys):
    kernel, advapi = fake_creation
    monkeypatch.setattr(ctypes, "get_last_error", lambda: named_mutex.ERROR_ALREADY_EXISTS, raising=False)
    inspect = Mock(return_value="bridge_mutex_acl_mismatch: observed=foreign")
    monkeypatch.setattr(named_mutex, "_inspect_existing", inspect)
    assert named_mutex.create_bridge_named_mutex("unique-test", kernel32=kernel, advapi32=advapi) == 42
    inspect.assert_called_once_with("unique-test", EXPECTED, kernel, advapi)
    assert "bridge_mutex_acl_mismatch" in capsys.readouterr().err
    kernel.LocalFree.assert_called_once()


def test_create_denial_is_not_ignored_and_descriptor_is_freed(fake_creation, monkeypatch):
    kernel, advapi = fake_creation
    kernel.CreateMutexExW.return_value = 0
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(OSError) as error:
        named_mutex.create_bridge_named_mutex("unique-test", kernel32=kernel, advapi32=advapi)
    assert error.value.errno == 5
    kernel.LocalFree.assert_called_once()


def test_existing_dacl_unreadable_is_visible_without_mutation(monkeypatch):
    kernel = SimpleNamespace(OpenMutexW=Mock(return_value=0))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    assert named_mutex._inspect_existing("unique-test", EXPECTED, kernel, object()) == (
        "bridge_mutex_acl_unverified: READ_CONTROL refused (5)"
    )
    kernel.OpenMutexW.assert_called_once_with(named_mutex.READ_CONTROL, False, "unique-test")


def test_existing_dacl_mismatch_closes_diagnostic_handle_and_frees_buffers(monkeypatch):
    kernel = SimpleNamespace(
        OpenMutexW=Mock(return_value=99),
        LocalFree=Mock(return_value=None),
        CloseHandle=Mock(return_value=True),
    )
    def get_security_info(handle, object_type, info, owner, group, dacl, sacl, descriptor):
        ctypes.cast(descriptor, ctypes.POINTER(ctypes.c_void_p))[0] = 111
        return 0
    advapi = SimpleNamespace(GetSecurityInfo=Mock(side_effect=get_security_info))
    monkeypatch.setattr(named_mutex, "_descriptor", lambda *args: ctypes.c_void_p(222))
    monkeypatch.setattr(
        named_mutex, "_dacl_text",
        lambda kernel32, advapi32, descriptor: (
            "D:(A;;GA;;;WD)" if descriptor.value == 111 else EXPECTED
        ),
    )
    assert "bridge_mutex_acl_mismatch" in named_mutex._inspect_existing(
        "unique-test", EXPECTED, kernel, advapi
    )
    assert kernel.LocalFree.call_count == 2
    kernel.CloseHandle.assert_called_once_with(99)


@pytest.mark.skipif(os.name != "nt", reason="requires real unique Win32 mutex")
def test_real_unique_name_create_then_open_and_dacl_diagnostic(capsys):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    named_mutex._configure(kernel, advapi)
    assert re.fullmatch(
        r"D:\(A;;GA;;;SY\)\(A;;GA;;;BA\)\(A;;0x00100001;;;S-1-5-5-\d+-\d+\)",
        named_mutex._creation_sddl(kernel, advapi),
    )
    name = rf"Local\WaggleDanceBridgeNamedMutexTest-{uuid.uuid4().hex}"
    first = named_mutex.create_bridge_named_mutex(name)
    try:
        second = named_mutex.create_bridge_named_mutex(name)
        try:
            assert first and second
            assert capsys.readouterr().err == ""
        finally:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(second)
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(first)

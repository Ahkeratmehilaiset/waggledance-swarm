"""Synthetic Windows path containment checks; never access an actual pointer."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path

import pytest

from tools import wd_q4_descriptor_pointer as pointer

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path behavior")


class ReachedChain(Exception):
    pass


def verify(state, bundle, root):
    return pointer._verify(
        state_path=str(state), expected_bundle=str(bundle), generations_root=str(root),
        expected_commit="a" * 40, expected_manifest_sha256="B" * 64,
        generation="c" * 40, descriptor_sha256="d" * 64,
    )


@pytest.mark.parametrize("protected", ["bundle", "generations"])
def test_case_variant_protected_ancestor_refuses_before_io(tmp_path, monkeypatch, protected):
    monkeypatch.setattr(pointer, "_chain", lambda _: (_ for _ in ()).throw(ReachedChain()))
    inside = tmp_path / "Protected" / "Nested" / "WD_REBOOT_STATE_CURRENT.json"
    ancestor = str(tmp_path / "Protected").swapcase()
    outside = tmp_path / "Other"
    bundle, root = (ancestor, outside) if protected == "bundle" else (outside, ancestor)
    with pytest.raises(pointer.PointerRefused, match="^path_refused$"):
        verify(inside, bundle, root)


def test_sibling_prefix_is_not_a_protected_ancestor(tmp_path, monkeypatch):
    monkeypatch.setattr(pointer, "_chain", lambda _: (_ for _ in ()).throw(ReachedChain()))
    state = tmp_path / "Protected-extra" / "WD_REBOOT_STATE_CURRENT.json"
    with pytest.raises(ReachedChain):
        verify(state, tmp_path / "Protected", tmp_path / "Other")


def test_different_drive_refuses_without_file_io(tmp_path, monkeypatch):
    monkeypatch.setattr(pointer, "_chain", lambda _: (_ for _ in ()).throw(ReachedChain()))
    state = tmp_path / "Separate" / "WD_REBOOT_STATE_CURRENT.json"
    other_drive = "Z" if Path(state).drive.upper() != "Z:" else "Y"
    with pytest.raises(ValueError):
        verify(state, other_drive + ":\\synthetic-bundle", tmp_path / "Other")


@pytest.mark.parametrize("protected", ["bundle", "generations"])
@pytest.mark.parametrize("alias_side", ["target", "protected"])
def test_real_83_alias_refuses_protected_parent(tmp_path, protected, alias_side):
    parent = tmp_path / "SyntheticProtectedLongDirectoryName"
    parent.mkdir()
    outside = tmp_path / "SyntheticOutsideDirectoryName"
    outside.mkdir()
    fn = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    fn.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    fn.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = fn(str(parent), buffer, len(buffer))
    if not length or length >= len(buffer):
        pytest.skip("8.3 alias unavailable")
    alias = Path(buffer.value)
    if pointer._same(alias, parent):
        pytest.skip("8.3 alias unavailable for this directory")
    assert os.path.samefile(alias, parent)
    target_parent, protected_path = (alias, parent) if alias_side == "target" else (parent, alias)
    state = target_parent / "WD_REBOOT_STATE_CURRENT.json"  # never created
    bundle, root = (protected_path, outside) if protected == "bundle" else (outside, protected_path)
    with pytest.raises(pointer.PointerRefused, match="^path_refused$"):
        verify(state, bundle, root)
    assert not state.exists()


def test_missing_state_parent_refuses_without_creating_it(tmp_path):
    bundle, root = tmp_path / "bundle", tmp_path / "generations"
    bundle.mkdir()
    root.mkdir()
    state = tmp_path / "missing-parent" / "WD_REBOOT_STATE_CURRENT.json"
    with pytest.raises((pointer.PointerRefused, OSError)):
        verify(state, bundle, root)
    assert not state.parent.exists()


def test_reparse_state_parent_refused_before_resolution(tmp_path, monkeypatch):
    bundle, root, state_parent = tmp_path / "bundle", tmp_path / "generations", tmp_path / "state-parent"
    for item in (bundle, root, state_parent):
        item.mkdir()
    original = pointer._is_reparse
    monkeypatch.setattr(pointer, "_is_reparse", lambda path, info: path == state_parent or original(path, info))
    with pytest.raises(pointer.PointerRefused, match="^path_refused$"):
        verify(state_parent / "WD_REBOOT_STATE_CURRENT.json", bundle, root)


def test_failed_canonicalization_refuses_before_read(tmp_path, monkeypatch):
    bundle, root, state_parent = tmp_path / "bundle", tmp_path / "generations", tmp_path / "state-parent"
    for item in (bundle, root, state_parent):
        item.mkdir()
    original = Path.resolve
    def refuse_target_parent(path, *args, **kwargs):
        if path == state_parent:
            raise OSError("synthetic resolution failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", refuse_target_parent)
    with pytest.raises(OSError, match="synthetic resolution failure"):
        verify(state_parent / "WD_REBOOT_STATE_CURRENT.json", bundle, root)

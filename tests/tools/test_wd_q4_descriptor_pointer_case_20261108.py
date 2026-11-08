"""Synthetic Windows path containment checks; never access an actual pointer."""

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

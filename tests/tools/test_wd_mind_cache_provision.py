"""Windows-only synthetic provision tests; artifacts remain under .codex-audit."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from tools import wd_mind_cache_provision as provision


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='native Windows security and handle semantics')
AUDIT = Path(r'C:\Python\project2\.codex-audit')
_REAL_NAMESPACE_CHECK = provision._Native.verify_namespace


@pytest.fixture
def synthetic(monkeypatch):
    assert AUDIT.is_absolute() and AUDIT.is_dir() and AUDIT.name == '.codex-audit'
    name = 'mind-cache-provision-test-' + uuid.uuid4().hex
    monkeypatch.setattr(provision, '_BASE', AUDIT)
    monkeypatch.setattr(provision, '_PARENT_NAME', name)
    # The shared test ancestor is not the production C: root and is writable
    # by other sandbox identities. Do not treat this as namespace evidence.
    monkeypatch.setattr(provision._Native, 'verify_namespace', lambda *_: None)
    return AUDIT / name


def _private(handle):
    native = provision._Native()
    native.verify_private(handle, native.token_identity())


def test_cli_refuses_before_creation_while_namespace_proof_is_incomplete(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['wd_mind_cache_provision.py'])
    monkeypatch.setattr(provision, '_provision', lambda: pytest.fail('creation must remain disabled'))
    assert provision.main() == 1
    assert 'namespace_proof_incomplete' in capsys.readouterr().err


def test_creates_both_private_directories_and_refuses_existing_without_changes(synthetic):
    provision._provision()
    target = synthetic / 'vector-cache'
    assert synthetic.is_dir() and target.is_dir()
    before = (synthetic.stat().st_ino, target.stat().st_ino)
    native = provision._Native()
    handles = [native.open_directory(synthetic), native.open_directory(target)]
    try:
        for handle in handles:
            _private(handle)
    finally:
        for handle in reversed(handles):
            native.k.CloseHandle(handle)
    with pytest.raises(provision.ProvisionRefused, match='already_exists'):
        provision._provision()
    assert (synthetic.stat().st_ino, target.stat().st_ino) == before


def test_parent_remains_after_child_creation_failure_and_retry_refuses(synthetic, monkeypatch):
    original = provision._Native.create_relative

    def fail_child(self, parent, name, descriptor):
        if name == 'vector-cache':
            raise provision.ProvisionRefused('synthetic_child_failure')
        return original(self, parent, name, descriptor)

    monkeypatch.setattr(provision._Native, 'create_relative', fail_child)
    with pytest.raises(provision.ProvisionRefused, match='synthetic_child_failure'):
        provision._provision()
    assert synthetic.is_dir() and not (synthetic / 'vector-cache').exists()
    before = synthetic.stat().st_ino
    with pytest.raises(provision.ProvisionRefused, match='already_exists'):
        provision._provision()
    assert synthetic.stat().st_ino == before


def test_existing_file_is_never_replaced_or_modified(synthetic):
    synthetic.write_bytes(b'synthetic-sentinel')
    before = synthetic.stat().st_ino
    with pytest.raises(provision.ProvisionRefused, match='already_exists'):
        provision._provision()
    assert synthetic.stat().st_ino == before
    assert synthetic.read_bytes() == b'synthetic-sentinel'


def test_system_token_refused_before_creating_anything(synthetic, monkeypatch):
    monkeypatch.setattr(provision._Native, 'token_identity', lambda _: 'S-1-5-18')
    with pytest.raises(provision.ProvisionRefused, match='operator_is_system'):
        provision._provision()
    assert not synthetic.exists()


def test_renamed_parent_is_detected_by_handle_final_path(synthetic):
    provision._provision()
    native = provision._Native()
    handle = native.open_directory(synthetic)
    other = synthetic.with_name(synthetic.name + '-renamed')
    assert synthetic.parent == AUDIT and other.parent == AUDIT and not other.exists()
    try:
        native.verify_final_path(handle, synthetic)
        os.rename(synthetic, other)
        with pytest.raises(provision.ProvisionRefused, match='final_path'):
            native.verify_final_path(handle, synthetic)
        assert other.is_dir() and not synthetic.exists()
    finally:
        native.k.CloseHandle(handle)


def test_reenabled_acl_inheritance_is_refused(synthetic):
    provision._provision()
    completed = subprocess.run(['icacls', str(synthetic), '/inheritance:e'],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    native = provision._Native()
    handle = native.open_directory(synthetic)
    try:
        with pytest.raises(provision.ProvisionRefused, match='dacl_not_protected'):
            _private(handle)
    finally:
        native.k.CloseHandle(handle)


def test_namespace_rejects_untrusted_delete_child_ace(synthetic):
    provision._provision()
    completed = subprocess.run(['icacls', str(synthetic), '/grant', '*S-1-1-0:(DC)'],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    native = provision._Native()
    handle = native.open_directory(synthetic)
    try:
        with pytest.raises(provision.ProvisionRefused, match='namespace_untrusted_control'):
            _REAL_NAMESPACE_CHECK(native, handle, native.token_identity())
    finally:
        native.k.CloseHandle(handle)


def test_actual_volume_root_passes_conservative_namespace_check():
    native = provision._Native()
    handle = native.open_directory(Path('C:\\'))
    try:
        native.verify_final_path(handle, Path('C:\\'))
        _REAL_NAMESPACE_CHECK(native, handle, native.token_identity())
    finally:
        native.k.CloseHandle(handle)


def test_missing_ancestor_refused_without_creation(synthetic):
    # A missing ancestor is also fail-closed; it must not be created by the
    # provisioner or cause creation outside the selected synthetic root.
    native = provision._Native()
    with pytest.raises(provision.ProvisionRefused, match='ancestor_open'):
        native.open_directory(synthetic)
    assert not synthetic.exists()


def test_directory_symlink_is_not_followed(synthetic):
    provision._provision()
    link = synthetic.with_name(synthetic.name + '-link')
    assert link.parent == AUDIT and synthetic.parent == AUDIT and not link.exists()
    try:
        os.symlink(synthetic, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'creating a synthetic directory symlink is unavailable: {exc.winerror}')
    native = provision._Native()
    with pytest.raises(provision.ProvisionRefused, match='directory_identity'):
        native.open_directory(link)

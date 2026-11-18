"""Synthetic tests of the cross-process Mind owner reservation.

No production marker or live owner is touched. A preseeded CLEAR fixture is
not evidence that an older, unguarded owner was absent during migration.
"""
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import wd_mind_runtime_child as child


ROOT = Path(__file__).resolve().parents[2]
MARKER = 'mind-owner-reservation.v1'
PREFIX = b'WD_MIND_OWNER_V1\n'
WINDOWS = pytest.mark.skipif(os.name != 'nt', reason='Windows reservation uses byte-range locking')


def _seed(root, state=b'C'):
    root.mkdir()
    (root / MARKER).write_bytes(PREFIX + state)
    return root


@WINDOWS
def test_missing_or_invalid_guard_refuses_without_creating_or_clearing(tmp_path):
    root = tmp_path / 'audit'
    root.mkdir()
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)
    assert not (root / MARKER).exists()
    for value in (b'', PREFIX, PREFIX + b'R', PREFIX + b'X', PREFIX + b'CC'):
        (root / MARKER).write_bytes(value)
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        assert (root / MARKER).read_bytes() == value


@WINDOWS
def test_valid_clear_is_reserved_until_explicit_proven_cleanup(tmp_path):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    assert guard._read() == PREFIX + b'R'
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)
    assert (root / MARKER).read_bytes() == PREFIX + b'C'
    second = child._reserve_local_owner(root)
    second.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
@pytest.mark.parametrize('evidence', [
    {'service_stopped': False, 'owner_stopped': True, 'owner_owned': False, 'control_stopped': True},
    {'service_stopped': True, 'owner_stopped': False, 'owner_owned': False, 'control_stopped': True},
    {'service_stopped': True, 'owner_stopped': True, 'owner_owned': True, 'control_stopped': True},
    {'service_stopped': True, 'owner_stopped': True, 'owner_owned': False, 'control_stopped': False},
    {'service_stopped': 1, 'owner_stopped': True, 'owner_owned': False, 'control_stopped': True},
])
def test_incomplete_cleanup_never_clears_reserved_guard(tmp_path, evidence):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    with pytest.raises(child.LaunchRefused):
        guard.release_clean(**evidence)
    assert guard._read() == PREFIX + b'R'
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
def test_cross_process_guard_prevents_second_owner_and_persists_clean_release(tmp_path):
    root = _seed(tmp_path / 'audit')
    script = r'''import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from tools import wd_mind_runtime_child as child
guard=child._reserve_local_owner(Path(sys.argv[2]))
print('RESERVED',flush=True)
assert sys.stdin.buffer.read(1)==b'S'
guard.release_clean(service_stopped=True,owner_stopped=True,owner_owned=False,control_stopped=True)
print('STOPPED',flush=True)
'''
    proc = subprocess.Popen([sys.executable, '-I', '-u', '-c', script, str(ROOT), str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        assert proc.stdout.readline().strip() == b'RESERVED'
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        proc.stdin.write(b'S'); proc.stdin.flush(); proc.stdin.close()
        assert proc.wait(timeout=20) == 0
        assert proc.stdout.read().strip() == b'STOPPED'
        guard = child._reserve_local_owner(root)
        guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)
    finally:
        if proc.poll() is None:
            # Test processes only: request graceful EOF; never terminate/kill.
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()


@WINDOWS
def test_owner_crash_marker_stays_held_without_timeout_clear(tmp_path):
    root = _seed(tmp_path / 'audit')
    script = r'''import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from tools import wd_mind_runtime_child as child
child._reserve_local_owner(Path(sys.argv[2]))
raise RuntimeError('synthetic interruption')
'''
    proc = subprocess.Popen([sys.executable, '-I', '-c', script, str(ROOT), str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert proc.wait(timeout=20) != 0
    assert (root / MARKER).read_bytes() == PREFIX + b'R'
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)


@WINDOWS
def test_other_process_cannot_write_locked_state_byte(tmp_path):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    script = r'''import os,sys
fd=os.open(sys.argv[1],os.O_RDWR|os.O_BINARY)
try:
    os.lseek(fd,int(sys.argv[2]),os.SEEK_SET)
    os.write(fd,b'C')
except PermissionError:
    print('BLOCKED')
else:
    print('WRITTEN')
finally:
    os.close(fd)
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(root / MARKER), str(len(PREFIX))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.stdout.strip() == b'BLOCKED'
    assert guard._read() == PREFIX + b'R'
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
@pytest.mark.parametrize('entry_name,args', [
    ('_local_candidate_owner_entry', ['--mind-local-candidate-owner', '--policy-sha256', 'b'*64,
        '--approval-reference-sha256', 'c'*64]),
])
@pytest.mark.parametrize('outcome', ['success', 'clean_refused', 'refused', 'exception'])
def test_fixed_cli_reserves_before_entry_and_only_clears_on_proven_success(
        tmp_path, monkeypatch, entry_name, args, outcome):
    root = _seed(tmp_path / 'audit')
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'a'*64)
    seen = []
    def entry(**kwargs):
        # This point precedes the adapter start inside the real entry.
        seen.append('entered')
        with pytest.raises(child.LaunchRefused):
            child._reserve_local_owner(root)
        if outcome == 'exception':
            raise RuntimeError('synthetic failure')
        if outcome in ('success', 'clean_refused'):
            kwargs['_cleanup_evidence']['clean'] = True
        return 0 if outcome == 'success' else 2
    monkeypatch.setattr(child, entry_name, entry)
    assert child._fixed_child_main(args) == (0 if outcome == 'success' else 2)
    assert seen == ['entered']
    assert (root / MARKER).read_bytes() == PREFIX + (b'C' if outcome in ('success', 'clean_refused') else b'R')


@WINDOWS
def test_invalid_initial_input_leaves_slot_available_for_next_caller(tmp_path, monkeypatch):
    root = _seed(tmp_path / 'audit')
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    assert child._guarded_local_entry(child._local_query_entry,
        control_stream=io.BytesIO(b''), output_stream=io.BytesIO(), bundle_root='synthetic',
        expected_manifest_sha256='a'*64, lane='codex-lead-1', policy_sha256='b'*64,
        approval_reference_sha256='c'*64) == 2
    assert (root / MARKER).read_bytes() == PREFIX + b'C'
    guard = child._reserve_local_owner(root)
    guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)


@WINDOWS
def test_hardlinked_marker_refuses_without_changes(tmp_path):
    root = _seed(tmp_path / 'audit')
    os.link(root / MARKER, root / 'alias')
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)
    assert (root / MARKER).read_bytes() == PREFIX + b'C'


@WINDOWS
def test_changed_marker_never_clears(tmp_path):
    root = _seed(tmp_path / 'audit')
    guard = child._reserve_local_owner(root)
    os.lseek(guard.fd, len(PREFIX), os.SEEK_SET)
    os.write(guard.fd, b'X')
    with pytest.raises(child.LaunchRefused):
        guard.release_clean(service_stopped=True, owner_stopped=True, owner_owned=False, control_stopped=True)
    guard.abandon()
    assert (root / MARKER).read_bytes() == PREFIX + b'X'
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(root)


@WINDOWS
def test_reparse_ancestor_refuses(tmp_path):
    real = _seed(tmp_path / 'audit')
    alias = tmp_path / 'alias'
    try:
        os.symlink(real, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip('symlink privilege unavailable: ' + type(exc).__name__)
    with pytest.raises(child.LaunchRefused):
        child._reserve_local_owner(alias)
    assert (real / MARKER).read_bytes() == PREFIX + b'C'


@WINDOWS
@pytest.mark.parametrize('state', [b'C', b'R'])
def test_closed_local_query_does_not_reserve_or_change_marker(tmp_path, monkeypatch, state):
    root = _seed(tmp_path / 'audit', state=state)
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    def unexpected(*args, **kwargs):
        raise AssertionError('closed route attempted admission or source I/O')
    monkeypatch.setattr(child, '_reserve_local_owner', unexpected)
    monkeypatch.setattr(child, '_LocalAdmission', unexpected)
    monkeypatch.setattr(child, 'create_local_manifest_query', unexpected)
    class Untouchable:
        def read(self, *args):
            raise AssertionError('closed route attempted input')
    output = io.BytesIO()
    assert child._guarded_local_entry(child._local_query_entry,
        control_stream=Untouchable(), output_stream=output, bundle_root='synthetic',
        expected_manifest_sha256='a'*64, lane='codex-lead-1', policy_sha256='b'*64,
        approval_reference_sha256='c'*64) == 2
    assert (root / MARKER).read_bytes() == PREFIX + state
    assert output.getvalue() == b''

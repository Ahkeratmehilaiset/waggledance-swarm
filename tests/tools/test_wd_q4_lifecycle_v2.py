"""Synthetic lifecycle-v2 admission; no production pointers or bridge writes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from tools import wd_q4_lifecycle_v2 as lifecycle


def binding(tmp_path):
    root = tmp_path / 'generation'
    root.mkdir()
    private = tmp_path / 'generation.q4-lifecycle-private-v2'
    private.mkdir()
    raw = json.dumps({'schema': 'wd.q4-lifecycle-binding.v2',
                      'generation_root': str(root), 'private_root': str(private)},
                     sort_keys=True, separators=(',', ':')).encode('ascii')
    return root, private, raw, hashlib.sha256(raw).hexdigest()


def test_default_off_neither_reads_binding_nor_writes(tmp_path):
    with lifecycle.admit(binding_bytes=None, binding_sha256=None, attempt=None,
                         previous_selector_sha256=None) as result:
        assert result == {'status': 'OFF'}
    assert list(tmp_path.iterdir()) == []


def test_inspection_of_unused_root_does_not_enroll_or_create_lock(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    assert lifecycle.inspect(enabled=True, binding_bytes=raw, binding_sha256=pin) == {'status': 'ABSENT_V2_ONLY'}
    assert list(root.iterdir()) == []
    assert list(private.iterdir()) == []


def test_failed_admission_does_not_enroll_over_a_legacy_pending_record(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    (private / 'q4-card-refresh.pending.json').write_bytes(b'legacy')
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                             attempt='a' * 64, previous_selector_sha256=None):
            pass
    assert not (root / 'q4-lifecycle-v2.enrolled.json').exists()
    assert not (root / 'q4-lifecycle-v2.pending.json').exists()


def test_successful_admission_creates_explicit_binding_pinned_enrollment(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                         attempt='a' * 64, previous_selector_sha256=None):
        enrolled = root / 'q4-lifecycle-v2.enrolled.json'
        assert json.loads(enrolled.read_bytes()) == {
            'schema': 'wd.q4-lifecycle-enrollment.v2', 'binding_sha256': pin}
        assert (root / 'q4-lifecycle-v2.pending.json').exists()


def test_foreign_enrollment_is_never_replaced_or_accepted(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    enrolled = root / 'q4-lifecycle-v2.enrolled.json'
    enrolled.write_bytes(b'{"foreign":true}')
    with pytest.raises(lifecycle.Unknown):
        lifecycle.inspect(enabled=True, binding_bytes=raw, binding_sha256=pin)
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                             attempt='a' * 64, previous_selector_sha256=None):
            pass
    assert enrolled.read_bytes() == b'{"foreign":true}'
    assert not (root / 'q4-lifecycle-v2.pending.json').exists()


def test_busy_before_admission_is_refused_without_intent(tmp_path, monkeypatch):
    from contextlib import contextmanager
    root, private, raw, pin = binding(tmp_path)
    @contextmanager
    def busy(*args, **kwargs):
        raise lifecycle.ReaderBusy()
        yield
    monkeypatch.setattr(lifecycle, 'reader_slot', busy)
    with pytest.raises(lifecycle.Refused):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                             attempt='a' * 64, previous_selector_sha256=None):
            pass
    assert list(root.iterdir()) == []


def test_rejects_wrong_private_root_before_any_state_write(tmp_path):
    root, private, raw, _ = binding(tmp_path)
    doc = json.loads(raw)
    other = tmp_path / 'different-private'; other.mkdir()
    doc['private_root'] = str(other)
    raw = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode('ascii')
    with pytest.raises(lifecycle.Refused):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=hashlib.sha256(raw).hexdigest(),
                             attempt='a' * 64, previous_selector_sha256=None):
            pass
    assert not list(root.iterdir())
    assert not list(private.iterdir())
    assert not list(other.iterdir())


def test_interrupted_admission_is_durable_and_second_attempt_refuses(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                             attempt='a' * 64, previous_selector_sha256=None):
            raise KeyboardInterrupt()
    marker = root / 'q4-lifecycle-v2.pending.json'
    before = marker.read_bytes()
    observation = lifecycle.inspect(enabled=True, binding_bytes=raw, binding_sha256=pin)
    assert observation['status'] == 'PENDING'
    assert observation['attempt'] == 'a' * 64
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                             attempt='b' * 64, previous_selector_sha256=None):
            pass
    assert marker.read_bytes() == before


def test_two_processes_share_one_lock_and_one_pending(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    script = r'''import json, sys
from tools import wd_q4_lifecycle_v2 as life
raw=bytes.fromhex(sys.argv[1]); pin=sys.argv[2]
try:
    with life.admit(enabled=True,binding_bytes=raw,binding_sha256=pin,attempt='a'*64,previous_selector_sha256=None):
        print('ADMITTED',flush=True)
        sys.stdin.readline()
except life.Refused: print('REFUSED',flush=True)
except life.Unknown: print('UNKNOWN',flush=True)
'''
    env=dict(os.environ, PYTHONPATH=str(Path.cwd()))
    first = subprocess.Popen([sys.executable, '-B', '-c', script, raw.hex(), pin], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        assert first.stdout.readline().strip() == 'ADMITTED'
        second=subprocess.run([sys.executable,'-B','-c',script,raw.hex(),pin],input='\n',capture_output=True,
                              text=True,env=env,timeout=15)
        assert second.returncode == 0 and second.stdout.strip() == 'UNKNOWN'
        assert (root/'q4-lifecycle-v2.pending.json').is_file()
    finally:
        first.stdin.write('\n'); first.stdin.flush()
        assert first.wait(timeout=15) == 0


def test_reparse_private_root_is_rejected(tmp_path):
    root = tmp_path/'generation'; root.mkdir()
    target = tmp_path/'target'; target.mkdir()
    private = tmp_path/'generation.q4-lifecycle-private-v2'
    try:
        private.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlink unavailable')
    raw=json.dumps({'schema':'wd.q4-lifecycle-binding.v2','generation_root':str(root),'private_root':str(private)},
                   sort_keys=True,separators=(',',':')).encode('ascii')
    with pytest.raises(lifecycle.Refused):
        with lifecycle.admit(enabled=True,binding_bytes=raw,binding_sha256=hashlib.sha256(raw).hexdigest(),
                             attempt='a'*64,previous_selector_sha256=None):
            pass
    assert not list(root.iterdir())
    assert not list(target.iterdir())


@pytest.mark.parametrize('name', [
    'q4-card-refresh.pending.json', 'q4-live-update.pending.json',
    'q4-continuation-adopt.pending.json', 'q4-adopt-recovery-job.pending.json',
    'prefix-private-state.pending', '.wd-q4-status-prefix-runner.pending',
    'q4-cutoff.pending.json', 'q4-continuation-orchestrator.pending.json',
    'q4-continuation-orchestrator-job.pending.json'])
def test_existing_legacy_marker_in_canonical_private_is_never_ignored(tmp_path, name):
    root, private, raw, pin = binding(tmp_path)
    old = private / name; old.write_bytes(b'unknown legacy pending')
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True,binding_bytes=raw,binding_sha256=pin,
                             attempt='b'*64,previous_selector_sha256=None):
            pass
    with pytest.raises(lifecycle.Unknown):
        lifecycle.inspect(enabled=True,binding_bytes=raw,binding_sha256=pin)
    assert old.read_bytes() == b'unknown legacy pending'
    assert not (root/'q4-lifecycle-v2.pending.json').exists()


def test_tampered_pending_is_unknown_and_never_removed(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    marker = root/'q4-lifecycle-v2.pending.json'
    marker.write_bytes(b'{"foreign":true}')
    with pytest.raises(lifecycle.Unknown):
        lifecycle.inspect(enabled=True,binding_bytes=raw,binding_sha256=pin)
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True,binding_bytes=raw,binding_sha256=pin,
                             attempt='b'*64,previous_selector_sha256=None):
            pass
    assert marker.read_bytes() == b'{"foreign":true}'


def test_binding_hash_mismatch_refuses_before_any_state_write(tmp_path):
    root, private, raw, _ = binding(tmp_path)
    with pytest.raises(lifecycle.Refused):
        with lifecycle.admit(enabled=True,binding_bytes=raw,binding_sha256='0'*64,
                             attempt='a'*64,previous_selector_sha256=None):
            pass
    assert list(root.iterdir()) == []
    assert list(private.iterdir()) == []


def test_error_after_marker_creation_stays_unknown_and_marker_is_preserved(tmp_path, monkeypatch):
    root, private, raw, pin = binding(tmp_path)
    original_read = lifecycle._read_pending
    def fail_pending_read(path):
        if path.name == 'q4-lifecycle-v2.pending.json':
            raise OSError()
        return original_read(path)
    monkeypatch.setattr(lifecycle, '_read_pending', fail_pending_read)
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True,binding_bytes=raw,binding_sha256=pin,
                             attempt='a'*64,previous_selector_sha256=None):
            pass
    assert (root/'q4-lifecycle-v2.pending.json').is_file()


def test_error_after_enrollment_creation_stays_unknown_and_enrollment_is_preserved(tmp_path, monkeypatch):
    root, private, raw, pin = binding(tmp_path)
    monkeypatch.setattr(lifecycle, '_read_pending', lambda *_: (_ for _ in ()).throw(OSError()))
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                             attempt='a' * 64, previous_selector_sha256=None):
            pass
    assert (root / 'q4-lifecycle-v2.enrolled.json').is_file()
    assert not (root / 'q4-lifecycle-v2.pending.json').exists()


def test_interrupt_during_enrollment_is_not_a_clean_refusal(tmp_path, monkeypatch):
    root, private, raw, pin = binding(tmp_path)
    def interrupted(root, binding_pin):
        (root / 'q4-lifecycle-v2.enrolled.json').write_bytes(b'partial')
        raise KeyboardInterrupt()
    monkeypatch.setattr(lifecycle, '_ensure_enrollment', interrupted)
    with pytest.raises(lifecycle.Interrupted):
        with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=pin,
                             attempt='a' * 64, previous_selector_sha256=None):
            pass
    assert (root / 'q4-lifecycle-v2.enrolled.json').read_bytes() == b'partial'


def test_child_exception_after_admission_never_claims_clean_refusal(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True,binding_bytes=raw,binding_sha256=pin,
                             attempt='a'*64,previous_selector_sha256=None):
            raise lifecycle.Refused('fake child failure')
    assert (root/'q4-lifecycle-v2.pending.json').exists()


def test_killed_process_leaves_pending_and_second_process_cannot_enter(tmp_path):
    root, private, raw, pin = binding(tmp_path)
    script = r'''import sys
from tools import wd_q4_lifecycle_v2 as life
with life.admit(enabled=True,binding_bytes=bytes.fromhex(sys.argv[1]),binding_sha256=sys.argv[2],attempt='a'*64,previous_selector_sha256=None):
    print('ADMITTED',flush=True)
    sys.stdin.readline()
'''
    env=dict(os.environ, PYTHONPATH=str(Path.cwd()))
    child=subprocess.Popen([sys.executable,'-B','-c',script,raw.hex(),pin],stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    assert child.stdout.readline().strip() == 'ADMITTED'
    child.kill(); child.wait(timeout=15)
    marker=root/'q4-lifecycle-v2.pending.json'
    before=marker.read_bytes()
    with pytest.raises(lifecycle.Unknown):
        with lifecycle.admit(enabled=True,binding_bytes=raw,binding_sha256=pin,
                             attempt='b'*64,previous_selector_sha256=None):
            pass
    assert marker.read_bytes() == before
    assert lifecycle.inspect(enabled=True,binding_bytes=raw,binding_sha256=pin)['status'] == 'PENDING'

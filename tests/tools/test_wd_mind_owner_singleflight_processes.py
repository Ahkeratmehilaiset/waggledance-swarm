"""Cross-process tests of fixed CLI admission using synthetic owner resources.

Every actor patches the audit root before the fixed entrypoint is called.
The tests do not prove that legacy or direct Python owners are absent.
"""
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
WINDOWS = pytest.mark.skipif(os.name != 'nt', reason='Windows cross-process marker locking')
MAGIC = b'WD_MIND_OWNER_V1\n'
MARKER = 'mind-owner-reservation.v1'


ACTOR = r'''
import json, os, pathlib, sys, time
root, case, audit, manifest, route = sys.argv[1:]
sys.path.insert(0, root)
from tools import wd_mind_runtime_child as child
from tools import wd_mind_runtime_process as process
case, audit = pathlib.Path(case), pathlib.Path(audit)
assert audit == case / 'synthetic-audit' and audit.is_absolute()
child._AUDIT_ROOT = audit
reserve = child._reserve_local_owner
def checked_reserve(path):
    if path != audit:
        raise RuntimeError('unexpected audit root')
    return reserve(path)
child._reserve_local_owner = checked_reserve
def emit(event, **data):
    print(json.dumps(dict(event=event, **data)), flush=True)
def await_gate(name):
    deadline = time.monotonic() + 45
    while not (case / name).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('synthetic gate expired')
        time.sleep(0.01)
class Owner:
    def __init__(self):
        self._owner = self
        self.announced = False
    def start(self):
        emit('build_enter')
        await_gate('build-complete')
        return {'state': 'STARTING'}
    def status(self):
        return {'state': 'READY', 'owned': True}
    def close(self):
        if not (case / 'cleanup-complete').exists():
            if not self.announced:
                emit('owner_hold')
                self.announced = True
            return {'state': 'HOLD', 'owned': True}
        return {'state': 'STOPPED', 'owned': False}
class Service:
    def start(self): pass
    def status(self): return {'state': 'RUNNING'}
    def close(self, *, timeout): return {'state': 'STOPPED'}
child.create_local_manifest_query = lambda **kw: Owner()
process.create_local_candidate_service = lambda **kw: Service()
os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = manifest
if route == 'owner':
    args = ['--mind-local-candidate-owner', '--policy-path', str(case / 'policy'),
            '--policy-sha256', __import__('hashlib').sha256(b'synthetic').hexdigest(),
            '--approval-reference-sha256', 'c' * 64]
else:
    args = ['--mind-local-query', '--policy-sha256', 'b' * 64,
            '--approval-reference-sha256', 'c' * 64, '--lane', 'codex-lead-1']
result = child._fixed_child_main(args)
emit('done', result=result)
'''


def _synthetic_environment():
    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith(('AGENT_BRIDGE_', 'WD_BRIDGE_', 'WD_REBOOT_', 'WD_MIND_')):
            del env[name]
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


class Actor:
    def __init__(self, case, manifest, route):
        self.events, self.queue = [], queue.Queue()
        self.process = subprocess.Popen(
            [sys.executable, '-I', '-B', '-u', '-c', ACTOR, str(ROOT), str(case),
             str(case / 'synthetic-audit'), manifest, route], cwd=ROOT,
            env=_synthetic_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.errors = []
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                self.queue.put(json.loads(line))
        finally:
            self.queue.put({'event': 'eof'})

    def _read_stderr(self):
        for line in self.process.stderr:
            self.errors.append(line)

    def until(self, event):
        deadline = time.monotonic() + 25
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(f'synthetic actor {self.process.pid} did not reach {event}; not killed')
            try:
                item = self.queue.get(timeout=remaining)
            except queue.Empty:
                pytest.fail(f'synthetic actor {self.process.pid} did not reach {event}; not killed')
            self.events.append(item)
            if item.get('event') == event or (event == 'ready' and item.get('state') == 'READY'):
                return item
            if item.get('event') == 'eof':
                pytest.fail('actor exited early: ' + ''.join(self.errors))

    def request_stop(self):
        if self.process.stdin is not None and not self.process.stdin.closed:
            try:
                self.process.stdin.write('S')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                self.process.stdin.close()

    def wait_clean(self):
        self.request_stop()
        try:
            code = self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail(f'synthetic actor {self.process.pid} still retained; no kill attempted')
        for reader in self.readers:
            reader.join(timeout=3)
        assert code == 0, ''.join(self.errors)
        assert all(not reader.is_alive() for reader in self.readers)


@WINDOWS
@pytest.mark.parametrize('phase', ['building', 'cleanup_hold'])
@pytest.mark.parametrize('second_manifest', ['a' * 64, 'b' * 64], ids=['same', 'new'])
@pytest.mark.parametrize('second_route', ['owner', 'query'])
def test_second_fixed_cli_refuses_before_build_during_owned_phase(
        tmp_path, phase, second_manifest, second_route):
    case = tmp_path / 'synthetic-case'
    case.mkdir()
    audit = case / 'synthetic-audit'
    audit.mkdir()
    (audit / MARKER).write_bytes(MAGIC + b'C')
    (case / 'policy').write_bytes(b'synthetic')
    first = second = replacement = None
    try:
        first = Actor(case, 'a' * 64, 'owner')
        first.until('build_enter')
        if phase == 'cleanup_hold':
            (case / 'build-complete').touch()
            first.until('ready')
            first.request_stop()
            first.until('owner_hold')
        second = Actor(case, second_manifest, second_route)
        result = second.until('done')
        assert result['result'] == 2
        assert not any(item.get('event') == 'build_enter' for item in second.events)
        second.wait_clean()
        (case / 'build-complete').touch()
        (case / 'cleanup-complete').touch()
        if phase == 'building':
            first.until('ready')
            first.request_stop()
        assert first.until('done')['result'] == 0
        first.wait_clean()
        assert (audit / MARKER).read_bytes() == MAGIC + b'C'
        replacement = Actor(case, second_manifest, 'owner')
        replacement.until('build_enter')
        replacement.until('ready')
        replacement.request_stop()
        assert replacement.until('done')['result'] == 0
        replacement.wait_clean()
    finally:
        (case / 'build-complete').touch()
        (case / 'cleanup-complete').touch()
        for actor in (replacement, second, first):
            if actor is not None:
                actor.wait_clean()


# These are the complete imports needed by the reservation and its path
# validator, not a representation of an installed deployment bundle.
_SYNTHETIC_PACKAGE_FILES = (
    'tools/__init__.py',
    'tools/wd_mind_runtime_child.py',
    'tools/wd_mind_absent_generation_prefix_readonly.py',
    'tools/bridge_compact_view.py',
    'waggledance/__init__.py',
    'waggledance/core/__init__.py',
    'waggledance/core/bridge_log_reader.py',
)

_DISTINCT_ROOT_ACTOR = r'''
import hashlib, json, os, pathlib, sys
bundle, slot, manifest = map(str, sys.argv[1:])
bundle, slot = pathlib.Path(bundle), pathlib.Path(slot)
assert bundle.is_absolute() and slot.is_absolute()
assert slot != pathlib.Path(r'C:\Python\project2\.codex-audit')
sys.path.insert(0, str(bundle / 'tools-bootstrap'))
from tools import wd_mind_runtime_child as child
expected_file = bundle / 'tools-bootstrap' / 'tools' / 'wd_mind_runtime_child.py'
assert pathlib.Path(child.__file__).resolve() == expected_file.resolve()
assert expected_file.resolve().parents[2] == bundle.resolve()
assert child._AUDIT_ROOT == pathlib.Path(r'C:\Python\project2\.codex-audit')
print(json.dumps({'event': 'loaded', 'path': str(expected_file.resolve()),
                  'sha256': hashlib.sha256(expected_file.read_bytes()).hexdigest(),
                  'manifest': manifest}), flush=True)
child._AUDIT_ROOT = slot  # Never open the live reservation in this test.
os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = manifest
def entry(*, _cleanup_evidence, _admission, **kwargs):
    assert pathlib.Path(kwargs['bundle_root']).resolve() == bundle.resolve()
    assert kwargs['expected_manifest_sha256'] == manifest
    print(json.dumps({'event': 'entry'}), flush=True)
    if sys.stdin.buffer.read(1) != b'S':
        return 2
    _cleanup_evidence['clean'] = True
    return 0
child._local_candidate_owner_entry = entry  # Synthetic owner, real CLI/guard.
result = child._fixed_child_main([
    '--mind-local-candidate-owner', '--policy-sha256', 'd' * 64,
    '--approval-reference-sha256', 'c' * 64])
if result == 2:
    print(json.dumps({'event': 'refused'}), flush=True)
else:
    print(json.dumps({'event': 'done', 'result': result}), flush=True)
'''


class DistinctRootActor(Actor):
    def __init__(self, bundle, slot, manifest):
        self.events, self.queue, self.errors = [], queue.Queue(), []
        self.process = subprocess.Popen(
            [sys.executable, '-I', '-B', '-u', '-c', _DISTINCT_ROOT_ACTOR,
             str(bundle), str(slot), manifest], cwd=bundle,
            env=_synthetic_environment(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers:
            reader.start()


@WINDOWS
@pytest.mark.parametrize('same_bundle', [True, False], ids=['same-root', 'distinct-root'])
@pytest.mark.parametrize('same_manifest', [True, False], ids=['same-manifest', 'different-manifest'])
def test_distinct_pinned_code_roots_share_synthetic_os_reservation(
        tmp_path, same_bundle, same_manifest):
    """Two copied Git-blob code roots share one test-only reservation marker.

    This tests the reservation boundary, not a deployed bundle, IPC, an E5
    worker, or the absence of old unguarded owners. The fixed production
    audit-root constant is asserted before substituting the synthetic root.
    """
    case = tmp_path / 'distinct-roots-case'
    case.mkdir()
    slot = case / 'synthetic-audit'
    slot.mkdir()
    (slot / MARKER).write_bytes(MAGIC + b'C')

    # Read bytes from the current committed head, and refuse a dirty source
    # closure so this test cannot silently exercise an older implementation.
    dirty = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *_SYNTHETIC_PACKAGE_FILES],
                           cwd=ROOT, check=False)
    assert dirty.returncode == 0, 'synthetic package sources differ from HEAD'
    blobs = {
        name: subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=ROOT)
        for name in _SYNTHETIC_PACKAGE_FILES
    }
    roots = [case / 'bundle-a', case / 'bundle-b']
    for bundle in roots:
        for name, blob in blobs.items():
            target = bundle.joinpath('tools-bootstrap', *name.split('/'))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(blob).digest()
    expected_sha = hashlib.sha256(blobs['tools/wd_mind_runtime_child.py']).hexdigest()
    second_root = roots[0] if same_bundle else roots[1]
    second_manifest = ('a' if same_manifest else 'b') * 64
    first = second = replacement = None
    try:
        first = DistinctRootActor(roots[0], slot, 'a' * 64)
        loaded_first = first.until('loaded')
        assert loaded_first['path'] == str((roots[0] / 'tools-bootstrap/tools/wd_mind_runtime_child.py').resolve())
        assert loaded_first['sha256'] == expected_sha
        first.until('entry')
        second = DistinctRootActor(second_root, slot, second_manifest)
        loaded_second = second.until('loaded')
        assert loaded_second['path'] == str((second_root / 'tools-bootstrap/tools/wd_mind_runtime_child.py').resolve())
        assert loaded_second['sha256'] == expected_sha
        assert (loaded_first['path'] == loaded_second['path']) is same_bundle
        second.until('refused')
        assert not any(event.get('event') == 'entry' for event in second.events)
        second.wait_clean()
        first.request_stop()
        assert first.until('done')['result'] == 0
        first.wait_clean()
        assert (slot / MARKER).read_bytes() == MAGIC + b'C'
        replacement = DistinctRootActor(second_root, slot, second_manifest)
        replacement.until('loaded')
        replacement.until('entry')
        replacement.request_stop()
        assert replacement.until('done')['result'] == 0
        replacement.wait_clean()
    finally:
        for actor in (replacement, second, first):
            if actor is not None:
                actor.wait_clean()

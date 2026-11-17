"""Cross-process tests of fixed CLI admission using synthetic owner resources.

Every actor patches the audit root before the fixed entrypoint is called.
The tests do not prove that legacy or direct Python owners are absent.
"""
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

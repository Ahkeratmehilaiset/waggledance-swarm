"""Synthetic Windows admission tests through the real fixed local CLI routes.

The provider and service are doubles. CLI dispatch, policy framing, reservation,
cleanup loops, byte-range locking and actor process exit are real. These tests
do not establish native model/Job cleanup, legacy-owner absence or migration.
"""

import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
if Path(__file__).resolve().parent != ROOT / "tests" / "tools":
    raise RuntimeError("Apply this proposal in the bound registered worktree before pytest")
MAGIC = b"WD_MIND_OWNER_V1\n"
MARKER = "mind-owner-reservation.v1"
POLICY = b"synthetic-policy-only"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows byte-range reservation")

ACTOR = r'''
import hashlib, json, os, sys, threading, time
from pathlib import Path
repo, slot, controls, manifest, mode, route = sys.argv[1:]
repo, slot, controls = Path(repo), Path(slot), Path(controls)
audit = Path(r'C:\Python\project2\.codex-audit').resolve(strict=True)
slot, controls = slot.resolve(strict=True), controls.resolve(strict=True)
if (not (repo / '.git').exists() or not slot.is_absolute() or not controls.is_absolute()
        or slot.parent != controls.parent or slot == audit or controls == audit
        or audit not in slot.parents or audit not in controls.parents):
    raise SystemExit(90)
sys.path.insert(0, str(repo))
from tools import wd_mind_runtime_child as child
from tools import wd_mind_runtime_process as process
policy = b'synthetic-policy-only'
policy_sha = hashlib.sha256(policy).hexdigest()
event_lock = threading.Lock()
def event(name):
    with event_lock:
        sys.stderr.write(json.dumps({'event': name}) + '\n')
        sys.stderr.flush()
def gate(name):
    return (controls / name).is_file()
class Adapter:
    def __init__(self):
        self._owner = self
        self.close_observed = False
    def start(self):
        event('owner_start')
        if mode == 'disappear':
            os._exit(41)  # This synthetic actor exits itself; no external kill.
        return {'state': 'STARTING'}
    def status(self):
        return {'state': 'NEW' if mode == 'building' and not gate('build') else 'READY',
                'owned': True}
    def start_diagnostic(self):
        return {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
    def wait(self, *, timeout):
        assert timeout == 300
        return {'state': 'READY', 'owned': True}
    def query(self):
        event('query')
        return b'{"synthetic":true}'
    def finalize_result(self):
        event('finalize')
        return b'{"synthetic":true}'
    def close(self):
        if not self.close_observed:
            event('owner_close')
            self.close_observed = True
        if mode in ('owner_hold', 'owner_unknown', 'owner_timeout') and not gate('owner'):
            return {'state': 'TIMEOUT' if mode == 'owner_timeout' else 'HOLD',
                    'owned': None if mode == 'owner_unknown' else True}
        event('owner_stopped')
        return {'state': 'STOPPED', 'owned': False}
class Service:
    def __init__(self):
        self.close_observed = False
        self.status_calls = 0
    def start(self):
        event('service_start')
        while mode == 'service_start_hold' and not gate('service_start'):
            time.sleep(0.01)
    def status(self):
        self.status_calls += 1
        if self.status_calls == 2:
            # The owner loop already consumed an earlier RUNNING observation.
            # This test-only event is not a public readiness claim.
            event('service_running')
        return {'state': 'RUNNING'}
    def close(self, *, timeout):
        assert timeout == 0
        if not self.close_observed:
            event('service_close')
            self.close_observed = True
        if mode == 'service_close_hold' and not gate('service'):
            return {'state': 'HOLD'}
        event('service_stopped')
        return {'state': 'STOPPED'}
def factory(**kw):
    event('factory')
    assert kw['enabled'] is True
    assert kw['expected_manifest_sha256'] == manifest
    assert kw['identifier_policy_bytes'] == policy
    assert kw['expected_identifier_policy_sha256'] == policy_sha
    assert kw['operator_approval_reference_sha256'] == 'c' * 64
    adapter = child._LocalManifestQuery(kw['lane'], 'synthetic', manifest, policy, policy_sha,
        'c' * 64, 1, 0, 1, admission=kw.get('_admission'))
    adapter._owner, adapter._state = Adapter(), 'READY'
    if kw.get('_admission') is not None:
        kw['_admission'].bind_owner(adapter)
        # Read through the owning guard; external byte-range reads are locked.
        with kw['_admission']._lock:
            assert kw['_admission']._guard._read() == child._OWNER_RESERVATION_MAGIC + b'R'
        event('reservation_held')
    return adapter
def adapter_status(self):
    if mode == 'building' and not gate('build'):
        return {'state': 'STARTING', 'owned': True}
    return self._owner.status()
child._LocalManifestQuery.start = lambda self: self._owner.start()
child._LocalManifestQuery.status = adapter_status
child._LocalManifestQuery.wait = lambda self, **kw: self._owner.wait(**kw)
child._LocalManifestQuery.query = lambda self: self._owner.query()
child._LocalManifestQuery.finalize_result = lambda self: self._owner.finalize_result()
def service_factory(**kw):
    assert kw['enabled'] is True
    assert type(kw['owner']) is child._LocalManifestQuery
    assert isinstance(kw['owner']._owner, Adapter)
    assert kw['pipe_name'] == process.local_candidate_pipe_name(manifest_sha256=manifest)
    return Service()
child._AUDIT_ROOT = slot  # Explicit synthetic root; never the installed root.
child.create_local_manifest_query = factory
process.create_local_candidate_service = service_factory
os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = manifest
if route == 'owner':
    args = ['--mind-local-candidate-owner', '--policy-path', str(controls / 'policy'),
            '--policy-sha256', policy_sha, '--approval-reference-sha256', 'c' * 64]
else:
    assert route == 'query'
    args = ['--mind-local-query', '--policy-sha256', policy_sha,
            '--approval-reference-sha256', 'c' * 64, '--lane', 'codex-tools-1']
event('before_cli')
raise SystemExit(child._fixed_child_main(args))
'''


class Actor:
    def __init__(self, slot, controls, *, manifest="a" * 64, mode="ready", route="owner"):
        self.controls = controls
        controls.mkdir()
        (controls / "policy").write_bytes(POLICY)
        self.route = route
        self.observed = {"event": [], "state": []}
        self.pending = queue.Queue()
        self.readers = []
        self.reader_eof = {"state": threading.Event(), "event": threading.Event()}
        self.reader_errors = queue.Queue()
        allowed = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'PATH', 'PATHEXT',
                   'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-u", "-c", ACTOR, str(ROOT), str(slot),
             str(controls), manifest, mode, route],
            cwd=ROOT, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        for name, stream in (("state", self.proc.stdout), ("event", self.proc.stderr)):
            thread = threading.Thread(target=self._drain, args=(name, stream), daemon=True)
            thread.start()
            self.readers.append(thread)
        if route == "query":
            try:
                self.proc.stdin.write(b"MCL1" + struct.pack(">I", len(POLICY)) + POLICY)
                self.proc.stdin.flush()
            except BrokenPipeError:
                pass  # A refusal may exit before the synthetic frame is sent.
            self.eof()

    def _drain(self, name, stream):
        try:
            while True:
                raw = stream.readline()
                if raw == b"":
                    self.reader_eof[name].set()
                    return
                self.pending.put((name, raw))
        except BaseException as exc:
            self.reader_errors.put((name, exc))

    def collect(self):
        while True:
            try:
                name, raw = self.pending.get_nowait()
            except queue.Empty:
                break
            self.observed[name].append(json.loads(raw))
        return self.observed

    def wait_for(self, name, token):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if token in [row.get(name) for row in self.collect()[name]]:
                return
            assert self.proc.poll() is None, self.collect()
            time.sleep(0.01)
        pytest.fail(f"Synthetic actor has not reported {name}={token}; pid={self.proc.pid}")

    def eof(self):
        if not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass

    def finish(self):
        timed_out = False
        try:
            code = self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Release only this fixture's cooperative gates; retain the
            # process until it actually exits, even if that has no deadline.
            self.release_test_gates()
            while True:
                try:
                    code = self.proc.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        for reader in self.readers:
            while reader.is_alive():
                reader.join(timeout=0.25)
        self.collect()
        assert self.reader_errors.empty(), "Actor output reader failed; EOF not established"
        assert all(event.is_set() for event in self.reader_eof.values()), "Actor pipe EOF not established"
        assert not timed_out, "Synthetic actor exceeded its deadline; exit and EOF subsequently observed"
        return code

    def release_test_gates(self):
        for name in ("build", "owner", "service", "service_start"):
            (self.controls / name).touch()
        self.eof()


@pytest.fixture
def actors():
    started = []

    def launch(slot, controls, **kwargs):
        actor = Actor(slot, controls, **kwargs)
        started.append(actor)
        return actor

    yield launch
    # Only test-owned gates and stdin EOF; never terminate a retained process.
    for actor in started:
        actor.release_test_gates()
    for actor in started:
        actor.finish()


def seed(tmp_path, name="slot", state=b"C"):
    slot = tmp_path / name
    slot.mkdir()
    if state is not None:
        (slot / MARKER).write_bytes(MAGIC + state)
    return slot


def refused(actor):
    actor.eof()
    assert actor.finish() == 2
    assert actor.observed["event"] == [{"event": "before_cli"}]
    assert actor.observed["state"] == []


def clean_owner_exit(actor, slot):
    actor.eof()
    assert actor.finish() == 0
    assert (slot / MARKER).read_bytes() == MAGIC + b"C"
    states = [row['state'] for row in actor.observed['state']]
    assert states[0] == 'STARTING' and states[-1] == 'STOPPED'
    assert 'READY' not in states and all(state == 'HOLD' for state in states[1:-1])
    assert {'event': 'owner_stopped'} in actor.observed['event']
    if {'event': 'service_start'} in actor.observed['event']:
        assert {'event': 'service_stopped'} in actor.observed['event']


def test_finish_timeout_retains_actor_until_exit_and_reports_failure():
    class SyntheticProcess:
        def __init__(self): self.calls = 0
        def wait(self, *, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired('synthetic', timeout)
            return 0
    actor = Actor.__new__(Actor)
    actor.proc = SyntheticProcess()
    actor.readers = []
    actor.reader_eof = {'state': threading.Event(), 'event': threading.Event()}
    for evidence in actor.reader_eof.values(): evidence.set()
    actor.reader_errors = queue.Queue()
    released = []
    actor.release_test_gates = lambda: released.append(True)
    actor.collect = lambda: None
    with pytest.raises(AssertionError, match='exceeded its deadline'):
        actor.finish()
    assert released == [True] and actor.proc.calls == 2


@pytest.mark.parametrize("phase", ["building", "ready"])
@pytest.mark.parametrize("manifest", ["a" * 64, "b" * 64])
@pytest.mark.parametrize("route", ["owner", "query"])
def test_fixed_cli_excludes_second_process_before_factory(tmp_path, actors, phase, manifest, route):
    slot = seed(tmp_path)
    first = actors(slot, tmp_path / "first", mode=phase)
    first.wait_for("state", "STARTING")
    first.wait_for("event", "reservation_held")
    if phase == "ready":
        first.wait_for("event", "service_running")
    second = actors(slot, tmp_path / "second", manifest=manifest, route=route)
    refused(second)
    assert first.proc.poll() is None
    assert [r["event"] for r in first.collect()["event"]].count("owner_start") == 1
    clean_owner_exit(first, slot)


@pytest.mark.parametrize("mode,entered", [
    ("owner_hold", "owner_close"),
    ("owner_unknown", "owner_close"),
    ("owner_timeout", "owner_close"),
    ("service_close_hold", "service_close"),
    ("service_start_hold", "service_start"),
])
def test_fixed_cli_retains_reservation_until_all_cleanup_finishes(tmp_path, actors, mode, entered):
    slot = seed(tmp_path)
    first = actors(slot, tmp_path / "first", mode=mode)
    first.wait_for("event", "service_start")
    if mode != "service_start_hold":
        first.wait_for("event", "service_running")
    first.eof()
    first.wait_for("event", entered)
    first.wait_for("state", "HOLD")
    first.wait_for("event", "reservation_held")
    refused(actors(slot, tmp_path / "second", manifest="b" * 64))
    assert first.proc.poll() is None
    first.release_test_gates()
    clean_owner_exit(first, slot)


@pytest.mark.parametrize("manifest", ["a" * 64, "b" * 64])
def test_owner_self_exit_keeps_reserved_marker_without_recovery(tmp_path, actors, manifest):
    slot = seed(tmp_path)
    first = actors(slot, tmp_path / "first", mode="disappear")
    assert first.finish() == 41
    assert {"event": "owner_start"} in first.observed["event"]
    assert (slot / MARKER).read_bytes() == MAGIC + b"R"
    refused(actors(slot, tmp_path / "second", manifest=manifest))
    assert (slot / MARKER).read_bytes() == MAGIC + b"R"


def test_clean_eof_allows_next_generation_guarded_owner(tmp_path, actors):
    slot = seed(tmp_path)
    for number, manifest in enumerate(("a" * 64, "b" * 64)):
        actor = actors(slot, tmp_path / str(number), manifest=manifest)
        actor.wait_for("event", "service_running")
        actor.wait_for("event", "reservation_held")
        clean_owner_exit(actor, slot)


def test_independent_synthetic_root_is_not_falsely_blocked(tmp_path, actors):
    first_slot, second_slot = seed(tmp_path, "slot-a"), seed(tmp_path, "slot-b")
    first = actors(first_slot, tmp_path / "first")
    first.wait_for("event", "service_running")
    second = actors(second_slot, tmp_path / "second", manifest="b" * 64)
    second.wait_for("event", "service_running")
    assert first.proc.poll() is None and second.proc.poll() is None
    first.wait_for("event", "reservation_held")
    second.wait_for("event", "reservation_held")
    clean_owner_exit(first, first_slot)
    clean_owner_exit(second, second_slot)


@pytest.mark.parametrize("state", [None, b"R", b"X"])
def test_fixed_cli_never_provisions_or_recovers_marker(tmp_path, actors, state):
    slot = seed(tmp_path, state=state)
    refused(actors(slot, tmp_path / "first"))
    if state is None:
        assert not (slot / MARKER).exists()
    else:
        assert (slot / MARKER).read_bytes() == MAGIC + state

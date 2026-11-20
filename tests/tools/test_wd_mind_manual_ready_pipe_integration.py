"""Synthetic no-READY owner with real Windows named pipe and source resolver.

The manifest/policy control readers, model adapter, worker and index build are
fixtures. No live bridge, installed bundle, real model or authenticated caller
is involved. Native process/PowerShell startup and prompt delivery are not tested.
"""
import hashlib
import io
import json
import math
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from test_wd_mind_budget_pipe_integration import (
    _assert_synthetic_sources, _bind_synthetic_owner, _fixtures, _full_fixtures,
    _isolate_synthetic_pin, flat_consumer_case, source_fixtures, synthetic_checkout_pins,
)
from tools import wd_bridge_mind_session as session
from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_runtime_child as child
from tools import wd_mind_runtime_process as process

_IN_SYNTHETIC_PIPE_CHILD = False
_UNCONFIRMED_TEST_CHILDREN = []


def _close_until_confirmed(stream, evidence, errors, *, timeout=5):
    """Record close evidence only after a successful close and observation."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            stream.close()
            if stream.closed:
                evidence.set()
                return
        except BaseException as exc:
            errors.append(exc)
        time.sleep(0.25)
    raise TimeoutError('synthetic stream close not confirmed; cleanup UNKNOWN')


def _run_manual_pipe_in_owned_test_child(tmp_path, *, timeout=90):
    """Outer hard deadline for this one synthetic process and its threads.

    The nested case is not allowed to create any native descendant; model and
    worker are already synthetic fixtures.  On deadline, end only the exact
    child returned by Popen and require actual exit; never infer test success,
    pipe EOF or owner cleanup from a forced exit.
    """
    if _IN_SYNTHETIC_PIPE_CHILD:
        return False
    root = Path(__file__).resolve().parents[2]
    # pytest owns tmp_path; keep every child artifact in a unique private child
    # directory without requiring a machine-specific developer/CI layout.
    private = tmp_path / 'owned-pipe-child'
    private.mkdir()
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
        'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}}
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    bootstrap = (
        "import sys,subprocess\nsys.path.insert(0,sys.argv[1])\nimport pytest\n"
        "class ForbiddenPopen(subprocess.Popen):\n"
        " def __init__(self,*args,**kwargs):\n"
        "  raise RuntimeError('synthetic test cannot launch descendants')\n"
        "subprocess.Popen=ForbiddenPopen\n"
        "class ChildMarker:\n"
        " def pytest_collection_modifyitems(self,items):\n"
        "  for item in items: item.module._IN_SYNTHETIC_PIPE_CHILD=True\n"
        "raise SystemExit(pytest.main(['-p','no:cacheprovider','-q',sys.argv[2],"
        "'--basetemp',sys.argv[3]],plugins=[ChildMarker()]))\n"
    )
    node = 'tests/tools/test_wd_mind_manual_ready_pipe_integration.py::test_manual_owner_no_ready_real_pipe_verified_synthetic_query'
    log = private / 'synthetic-owned-child.log'
    with log.open('wb') as output:
        process = subprocess.Popen([sys.executable, '-B', '-c', bootstrap,
            str(root), node, str(private / 'inner')], cwd=root, env=env,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                process.kill()  # only this locally created synthetic child handle
                process.wait(timeout=5)
            except BaseException as exc:
                # Preserve ownership information in this runner, but do not
                # claim cleanup or protection against the runner itself dying.
                _UNCONFIRMED_TEST_CHILDREN.append(process)
                raise AssertionError('synthetic child exit unconfirmed; cleanup UNKNOWN') from exc
            raise AssertionError('synthetic child timed out and was ended; cleanup UNKNOWN') from None
        except BaseException:
            # An interrupted wait must not silently abandon the specific
            # synthetic child.  Report any inability to observe its exit.
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except BaseException as exc:
                    _UNCONFIRMED_TEST_CHILDREN.append(process)
                    raise AssertionError('synthetic child exit unconfirmed; cleanup UNKNOWN') from exc
            raise
    assert code == 0, f'synthetic child failed with exit {code}; log: {log}'
    return True


def test_close_evidence_waits_for_confirmed_close(monkeypatch):
    observed_evidence = []
    class SyntheticStream:
        closed = False
        calls = 0
        def close(self):
            self.calls += 1
            observed_evidence.append(evidence.is_set())
            if self.calls == 1:
                raise OSError('synthetic-close-failure')
            self.closed = True
    stream, evidence, errors = SyntheticStream(), threading.Event(), []
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    _close_until_confirmed(stream, evidence, errors)
    assert stream.calls == 2 and stream.closed and evidence.is_set()
    assert observed_evidence == [False, False]
    assert len(errors) == 1 and isinstance(errors[0], OSError)


def test_close_deadline_never_invents_evidence():
    class NeverClosed:
        closed = False
        def close(self): raise OSError('synthetic close failure')
    evidence, errors = threading.Event(), []
    with pytest.raises(TimeoutError, match='cleanup UNKNOWN'):
        _close_until_confirmed(NeverClosed(), evidence, errors, timeout=0)
    assert not evidence.is_set()


@pytest.mark.skipif(os.name != 'nt', reason='Windows test-owned process')
def test_outer_watchdog_ends_and_reaps_only_its_hung_synthetic_child(tmp_path, monkeypatch):
    original = subprocess.Popen
    children = []
    def start_hung_test_child(_args, **kwargs):
        process = original([sys.executable, '-B', '-c', 'import time; time.sleep(60)'], **kwargs)
        children.append(process)
        return process
    # A caller-controlled environment must not disable the outer watchdog.
    monkeypatch.setenv('WD_MIND_SYNTHETIC_PIPE_CHILD', '1')
    monkeypatch.setattr(subprocess, 'Popen', start_hung_test_child)
    try:
        with pytest.raises(AssertionError, match='timed out and was ended; cleanup UNKNOWN'):
            _run_manual_pipe_in_owned_test_child(tmp_path, timeout=0.3)
        assert len(children) == 1 and children[0].poll() is not None
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows AF_PIPE')
def test_manual_owner_no_ready_real_pipe_verified_synthetic_query(
        synthetic_checkout_pins, flat_consumer_case, tmp_path, monkeypatch):
    if _run_manual_pipe_in_owned_test_child(tmp_path):
        return
    # The inner process uses a private synthetic path, never the installed
    # owner's audit marker, even on a failed synthetic start.
    synthetic_audit = tmp_path / 'synthetic-owner-audit'
    synthetic_audit.mkdir()
    monkeypatch.setattr(child, '_AUDIT_ROOT', synthetic_audit)
    package, original, _, old, clock, _ = flat_consumer_case
    monkeypatch.setattr(runtime._PrivateBinding, '_snapshot_support', lambda _: package)
    pin = _isolate_synthetic_pin(original, monkeypatch)
    _fixtures._located_candidates(flat_consumer_case, monkeypatch)
    snapshot = dict(old, messages=dict(old['messages']))
    source, sidecar = _assert_synthetic_sources(tmp_path, snapshot)
    expected = {reader._sha(reader._canonical(row)): (row['message'], row['ts_utc'])
                for row in (json.loads(line) for line in source.read_bytes().splitlines())}
    cursor = snapshot['messages']['end_cursor']
    snapshot['messages']['end_cursor'] = prefix.bridge.BridgeCursor(cursor.offset, cursor.file_identity, None)
    sidecar.unlink()
    snapshot['messages']['generation_path'] = str(source.with_name('events.generation.json'))
    decision = json.loads(snapshot['decision_bytes'])
    decision['source_prefix']['generation'] = None
    snapshot['decision_bytes'] = reader._canonical(decision)
    snapshot['decision_sha256'] = reader._sha(snapshot['decision_bytes'])
    inputs = {k: v for k, v in snapshot.items() if k not in ('projection_bytes', 'projection_sha256')}
    snapshot['projection_bytes'] = reader._serialized(reader.rederive_complete_message_projection(**inputs))
    snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    fleet = b'synthetic fleet bytes'
    controls = (str(source), snapshot['messages']['generation_path'], reader._sha(fleet),
                snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda *_: controls)
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda *_: (fleet, controls[3], controls[4]))
    monkeypatch.setattr(reader.dt, 'datetime', clock)
    gate = original._gate
    flat = json.loads(gate._flat_raw)
    flat['projection_sha256'] = snapshot['projection_sha256']
    flat_bytes = reader._canonical(flat)
    _, adapter_module, binding, factory, launches = _full_fixtures._single_owner_synthetic(monkeypatch, tmp_path)
    _bind_synthetic_owner(original, snapshot, binding, factory)
    synthetic_worker_exited = threading.Event()
    start_worker = runtime._ManifestWorkerFactory.__call__

    def synthetic_start_worker(factory_self, **kwargs):
        result = start_worker(factory_self, **kwargs)
        worker, client = result['owner'], result['client']
        close = client.close
        def synthetic_worker_status():
            return ({'state': 'STOPPED', 'owned': False} if synthetic_worker_exited.is_set()
                    else {'state': 'RUNNING', 'owned': True})
        def synthetic_client_close(*, timeout):
            result = close(timeout=timeout)
            if result == {'state': 'EXITED', 'owned': False, 'exit_code': 0}:
                synthetic_worker_exited.set()
            return result
        worker.status = synthetic_worker_status
        client.close = synthetic_client_close
        return result

    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', synthetic_start_worker)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda _: snapshot)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _: None)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)

    class VectorAdapter:
        def query(self, _text):
            return [1.0] + [0.0] * 383

    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', lambda **_: VectorAdapter())

    def synthetic_build(handler):
        handler._memory = runtime._PrivateFlatMemory(binding, snapshot, flat_bytes, gate._ids, gate._vectors, gate._encoder)
        handler._frozen_flat = (flat_bytes, gate._ids, gate._vectors, gate._encoder)
        return {'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'}

    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', synthetic_build)
    policy = snapshot['identifier_policy_binding'][0]
    monkeypatch.setattr(session, '_selected_pin_read', lambda *_: policy)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
                                                  worker_factory=factory, shutdown_timeout=0)

    allow_owner_close = threading.Event()
    class SyntheticOwnerAdapter:
        # This is a test-only replacement for _LocalManifestQuery; its owner is
        # the real synthetic _SingleManifestOwner, not the production loader.
        _owner = owner
        def start(self):
            assert owner.start()['state'] == 'READY'
            return {'state': 'STARTING'}
        def status(self):
            return owner.status()
        def close(self):
            if not allow_owner_close.is_set():
                return {'state': 'HOLD', 'owned': True}
            return owner.close()
        def candidates(self, request):
            return owner.candidates(request)
        def located_candidates(self, request):
            return owner.located_candidates(request)

    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **_: SyntheticOwnerAdapter())
    service_started = threading.Event()
    services = []
    create_service = process.create_local_candidate_service

    def capture_service(**kwargs):
        service = create_service(**kwargs)
        services.append(service)
        start = service.start
        def observed_start():
            start()
            service_started.set()
        service.start = observed_start
        return service

    monkeypatch.setattr(process, 'create_local_candidate_service', capture_service)
    tracked = []
    original_start = threading.Thread.start
    tracked_names = {'mind-e5-launch', 'mind-local-candidates', 'mind-local-wake', 'mind-local-selected-client',
                     'mind-local-query', 'mind-local-service-start', 'mind-local-control', 'mind-local-close'}

    def tracked_start(thread):
        if thread.name in tracked_names:
            tracked.append(thread)
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, 'start', tracked_start)
    control_read_fd, control_write_fd = os.pipe()
    output_read_fd, output_write_fd = os.pipe()
    control_read = os.fdopen(control_read_fd, 'rb', buffering=0)
    control_write = os.fdopen(control_write_fd, 'wb', buffering=0)
    output_read = os.fdopen(output_read_fd, 'rb', buffering=0)
    output_write = os.fdopen(output_write_fd, 'wb', buffering=0)
    hold_written = threading.Event()
    class ObservedOutput:
        def __init__(self): self.pending = bytearray()
        def write(self, data):
            count = output_write.write(data)
            self.pending.extend(data[:count])
            while b'\n' in self.pending:
                line, _, tail = self.pending.partition(b'\n')
                self.pending = bytearray(tail)
                if json.loads(line).get('state') == 'HOLD':
                    hold_written.set()
            return count
        def flush(self): output_write.flush()
    control_write.write(b'MCL1' + struct.pack('>I', len(policy)) + policy)
    entry_result, entry_errors, cleanup = [], [], {'clean': False}
    output_writer_closed, control_reader_closed = threading.Event(), threading.Event()

    def run_entry():
        try:
            entry_result.append(child._local_candidate_owner_entry(
                control_stream=control_read, output_stream=ObservedOutput(), bundle_root=str(original._root),
                expected_manifest_sha256=pin, policy_sha256=hashlib.sha256(policy).hexdigest(),
                approval_reference_sha256='c' * 64, _cleanup_evidence=cleanup))
        except BaseException as exc:
            entry_errors.append(exc)
        finally:
            for stream, evidence in ((output_write, output_writer_closed),
                                     (control_read, control_reader_closed)):
                # Retain the stream on close errors, preserve the exception,
                # and never publish close evidence until it is actually closed.
                _close_until_confirmed(stream, evidence, entry_errors)

    entry = threading.Thread(target=run_entry, name='synthetic-manual-owner')
    pending_error = None
    wire = None
    try:
        entry.start()
        assert service_started.wait(15), 'synthetic service did not start'
        assert launches == [True]
        args = ['--mind-local-candidate-read', '--policy-path', 'synthetic-policy',
                '--policy-sha256', reader._sha(policy), '--approval-reference-sha256', 'c'*64,
                '--lane', 'codex-lead-1', '--timeout-seconds', '30']
        out, err = io.BytesIO(), io.BytesIO()
        assert session._local_candidate_entry(args, out, err, original._root, pin) == 0, err.getvalue()
        result = json.loads(out.getvalue())
        assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
        assert result['lane'] == 'codex-lead-1' and result['fleet_sha256'] == reader._sha(fleet)
        assert result['results']
        for hit in result['results']:
            ref = hit['source_event_sha256']
            assert ref in expected and hit['text'] == expected[ref][0]
            floor = reader._utc(hit['event_time_utc_floor'])
            assert floor == reader._utc(expected[ref][1]).replace(second=0, microsecond=0)
            assert hit['event_age_seconds_upper_bound'] >= math.ceil((clock.now(reader.dt.timezone.utc) - floor).total_seconds())
    except BaseException as exc:
        pending_error = exc
    finally:
        # This absolute deadline covers cooperative cleanup inside the isolated
        # child. The outer process watchdog bounds blocking OS calls as well.
        cleanup_deadline = time.monotonic() + 20
        def require_cleanup_time(stage):
            if time.monotonic() >= cleanup_deadline:
                raise TimeoutError(f'synthetic {stage} not confirmed; cleanup UNKNOWN')
        try:
            print('HOLD: synthetic owner, pipes and threads remain owned until exit and EOF', flush=True)
        except BaseException as exc:
            if pending_error is None:
                pending_error = exc
        # Real anonymous-pipe EOF, not a fabricated STOP frame. Failed close is
        # uncertainty: retain the handle and retry rather than claiming EOF.
        while True:
            require_cleanup_time('control writer close')
            try:
                control_write.close()
                if control_write.closed:
                    break
            except BaseException as exc:
                if pending_error is None:
                    pending_error = exc
            time.sleep(0.25)
        # Hold the synthetic adapter's close until the real status writer has
        # successfully written HOLD. This makes the HOLD case deterministic,
        # rather than assuming the scheduler cannot complete cleanup first.
        if pending_error is None:
            if not hold_written.wait(min(10, max(0, cleanup_deadline - time.monotonic()))):
                pending_error = AssertionError('synthetic HOLD write was not observed')
        allow_owner_close.set()
        # Retain ownership after any assertion/timeout or interruption. A
        # Thread.start with no identity is ambiguous, so it is never promoted
        # to proof of exit. A deadline is failure, never cleanup evidence.
        while True:
            require_cleanup_time('owner entry exit')
            try:
                if entry.ident is not None:
                    entry.join(0.25)
                    if (not entry.is_alive() and output_writer_closed.is_set()
                            and control_reader_closed.is_set()):
                        break
                else:
                    time.sleep(0.25)
            except BaseException as exc:
                if pending_error is None:
                    pending_error = exc
        # Only the actually exited entry closes the sole output writer. Keep
        # the reader until actual EOF even if an earlier assertion failed.
        wire_value, wire_errors = [], []
        def read_wire():
            try:
                wire_value.append(output_read.read())
            except BaseException as exc:
                wire_errors.append(exc)
        wire_reader = threading.Thread(target=read_wire, daemon=True)
        wire_reader.start()
        wire_reader.join(max(0, cleanup_deadline - time.monotonic()))
        if wire_reader.is_alive():
            raise TimeoutError('synthetic output EOF not observed; cleanup UNKNOWN')
        if wire_errors:
            raise wire_errors[0]
        wire = wire_value[0]
        while True:
            require_cleanup_time('output reader close')
            try:
                output_read.close()
                if output_read.closed:
                    break
            except BaseException as exc:
                if pending_error is None:
                    pending_error = exc
            time.sleep(0.25)
        # A late session worker may start its query thread while cleanup is in
        # progress. Re-scan until every captured started thread has truly exited.
        while True:
            require_cleanup_time('tracked thread exit')
            try:
                before = tuple(tracked)
                for thread in before:
                    if thread.ident is not None:
                        thread.join(0.25)
                if (all(thread.ident is not None and not thread.is_alive() for thread in before)
                        and before == tuple(tracked)):
                    break
            except BaseException as exc:
                if pending_error is None:
                    pending_error = exc
            time.sleep(0.25)
    # Check retained objects even when an earlier assertion failed; preserve
    # the original error while attaching any distinct cleanup failures.
    cleanup_errors = []
    try:
        if services:
            if len(services) != 1 or services[0].status()['state'] != 'STOPPED':
                cleanup_errors.append('service not STOPPED')
            elif not services[0]._serve_done.is_set() or services[0]._thread.is_alive():
                cleanup_errors.append('service thread not exited')
        if owner.status().get('owned') is not False:
            cleanup_errors.append('owner release not established')
        if entry_errors:
            cleanup_errors.append('entry thread raised')
    except BaseException as exc:
        cleanup_errors.append('cleanup status check raised')
        if pending_error is None:
            pending_error = exc
    if pending_error is not None:
        if cleanup_errors and hasattr(pending_error, 'add_note'):
            pending_error.add_note('; '.join(cleanup_errors))
        raise pending_error
    assert not cleanup_errors, cleanup_errors
    assert entry_result == [0] and cleanup['clean'] is True
    states = [json.loads(raw)['state'] for raw in wire.splitlines()]
    assert states == ['STARTING', 'HOLD', 'STOPPED'] and 'READY' not in states
    assert len(services) == 1 and services[0].status()['state'] == 'STOPPED'
    assert services[0]._serve_done.is_set() and not services[0]._thread.is_alive()
    assert synthetic_worker_exited.is_set()
    assert owner.status()['state'] == 'STOPPED' and owner.status()['owned'] is False
    assert all(thread.ident is not None and not thread.is_alive() for thread in tracked)

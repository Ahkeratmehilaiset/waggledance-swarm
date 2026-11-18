"""Opt-in owner diagnostics; all workers and content in these tests are synthetic."""
import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import threading
from types import SimpleNamespace

import pytest

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_runtime_child as child
import tools

MANUAL_HELPER = Path(tools.__file__).resolve().parent.parent / 'ops/windows/reboot/Start-WdMindManualOwner.ps1'

def _v1_wire(state):
    return (b'{"authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED",'
            b'"manifest_sha256":"' + b'a'*64 + b'","schema":"wd.mind-local-owner-status.v1",'
            b'"source_authentication":"UNKNOWN","state":"' + state.encode('ascii') + b'"}\n')


def _owner(monkeypatch, tmp_path, *, vector_cache_enabled=False):
    from tools import wd_mind_e5_client as e5_client
    from tools import wd_mind_e5_snapshot_adapter as adapter
    fleet = json.dumps({'lanes': [{'agent': 'codex-lead-1'}],
                        'tools_supervisor': {'agent': 'codex-tools-1'}},
                       sort_keys=True, separators=(',', ':')).encode()
    snapshot = {'projection_sha256': 'b'*64}
    binding = runtime._PrivateBinding(snapshot, tmp_path, 'a'*64, (fleet,), {})
    guard = object.__new__(child._LocalOwnerReservation)
    guard.fd = 1  # Inert sentinel; no OS handle is opened or closed.
    monkeypatch.setattr(guard, '_check', lambda: None)
    monkeypatch.setattr(guard, '_read', lambda: child._OWNER_RESERVATION_MAGIC + b'R')
    lease, token = child._LocalAdmission(guard), object()
    lease.bind_owner(token)
    factory = runtime._ManifestWorkerFactory(str(tmp_path), 'a'*64, {}, tmp_path/'lock',
        5, 0, e5_client.BULK_CONTRACT_SHA256, lease, token)
    lease.bind_factory(token, factory)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda _: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda candidate, frozen: frozen)
    class Worker:
        def status(self): return {'state': 'RUNNING', 'owned': True}
        def shutdown(self, *, timeout): return {'state': 'HOLD', 'owned': True}
    class Client:
        def __init__(self, worker): self.owner = worker
        def close(self, *, timeout): return {'state': 'HOLD', 'owned': True}
    class Adapter: pass
    worker = Worker()
    launches = []
    def launch(self, **kwargs):
        launches.append(True)
        kwargs['owner_sink'](worker)
        return {'status': 'READY', 'client': Client(worker), 'owner': worker,
                'lifecycle': worker.status(), 'authority': 'none'}
    monkeypatch.setattr(runtime._ManifestWorkerFactory, '__call__', launch)
    monkeypatch.setattr(adapter, 'create_single_flat_adapter', lambda **kwargs: Adapter(), raising=False)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding,
        lane='codex-lead-1', worker_factory=factory, shutdown_timeout=0,
        vector_cache_enabled=vector_cache_enabled)
    return owner, launches


def test_preworker_failure_keeps_exact_legacy_status_and_optin_reason(monkeypatch, tmp_path):
    owner, launches = _owner(monkeypatch, tmp_path, vector_cache_enabled=True)
    class Cache:
        def preflight(self): raise ValueError('synthetic-secret-not-for-output')
    monkeypatch.setattr(runtime, '_load_pinned_vector_cache', lambda _: Cache())
    result = owner.start()
    assert result == {'state': 'HOLD', 'owned': None, 'authority': 'none',
                      'cache_status': 'PREFLIGHT_REFUSED'}
    assert launches == []
    diagnostic = owner.start_diagnostic()
    assert diagnostic == {'reason': 'CACHE_PREFLIGHT', 'exception_type': 'ValueError', 'authority': 'none'}
    assert 'synthetic-secret-not-for-output' not in repr(diagnostic)


def test_worker_failure_retains_owner_and_exact_legacy_status(monkeypatch, tmp_path):
    owner, launches = _owner(monkeypatch, tmp_path)
    def fail(_): raise runtime.FullRuntimeRefused('synthetic-source-details')
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', fail)
    assert owner.start() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert len(launches) == 1 and owner._owner is not None
    assert owner.close() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert owner._owner is not None
    diagnostic = owner.start_diagnostic()
    assert diagnostic == {'reason': 'HANDLER_BUILD', 'exception_type': 'FullRuntimeRefused', 'authority': 'none'}
    assert 'synthetic-source-details' not in repr(diagnostic)


@pytest.mark.parametrize('signal,typename', [(KeyboardInterrupt, 'KeyboardInterrupt'),
                                            (asyncio.CancelledError, 'CancelledError')])
def test_cancellation_preserves_original_exception_and_legacy_hold(monkeypatch, tmp_path, signal, typename):
    owner, _ = _owner(monkeypatch, tmp_path)
    def fail(_): raise signal('synthetic-cancel-secret')
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', fail)
    with pytest.raises(signal): owner.start()
    assert owner.status() == {'state': 'HOLD', 'owned': True, 'authority': 'none'}
    assert owner.start_diagnostic() == {'reason': 'HANDLER_BUILD', 'exception_type': typename, 'authority': 'none'}


def test_busy_status_start_and_close_are_unchanged_while_failure_waits(monkeypatch, tmp_path):
    owner, _ = _owner(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Hidden(Exception):
        def __str__(self): raise AssertionError('exception must not be formatted')
    def fail(_):
        entered.set()
        assert release.wait(5)
        raise Hidden('synthetic-private-content')
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', fail)
    out = []
    thread = threading.Thread(target=lambda: out.append(owner.start()))
    thread.start()
    try:
        assert entered.wait(5)
        assert owner.status() == {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        assert owner.close() == {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        with pytest.raises(runtime.FullRuntimeRefused, match='^mind_full_runtime_refused$'): owner.start()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert out == [{'state': 'HOLD', 'owned': True, 'authority': 'none'}]
    assert owner.start_diagnostic() == {'reason': 'HANDLER_BUILD', 'exception_type': 'UNKNOWN', 'authority': 'none'}


def _local_adapter():
    class Admission:
        def assert_owner(self, _): pass
    return child._LocalManifestQuery('codex-lead-1', 'synthetic', 'a'*64, b'policy',
        'b'*64, 'c'*64, 5, 0, 5, admission=Admission())


def test_local_adapter_preowner_diagnostic_is_optin_and_status_unchanged(monkeypatch):
    monkeypatch.setattr(child._LocalManifestQuery, '_check_policy', lambda self: None)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', lambda self: None)
    def reject(**kwargs): raise ValueError('synthetic-private-text')
    monkeypatch.setattr(runtime, 'assemble_full_runtime', reject)
    adapter = _local_adapter()
    assert adapter.start() == {'state':'STARTING', 'owned':None, 'authority':'none'}
    assert adapter.wait(timeout=5) == {'state':'HOLD', 'owned':None, 'authority':'none'}
    diagnostic = adapter.start_diagnostic()
    assert diagnostic == {'reason':'RUNTIME_BINDING', 'exception_type':'ValueError', 'authority':'none'}
    assert 'synthetic-private-text' not in repr(diagnostic)
    assert adapter.close() == {'state':'STOPPED', 'owned':False, 'authority':'none'}


def test_local_adapter_retains_owner_and_forwards_optin_diagnostic(monkeypatch):
    monkeypatch.setattr(child._LocalManifestQuery, '_check_policy', lambda self: None)
    monkeypatch.setattr(child._LocalManifestQuery, '_check_external_policy', lambda self: None)
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kwargs: object())
    monkeypatch.setattr(runtime, 'create_manifest_worker_factory', lambda **kwargs: object())
    class Owner:
        def start(self): return {'state':'HOLD', 'owned':True, 'authority':'none'}
        def status(self): return {'state':'HOLD', 'owned':True, 'authority':'none'}
        def start_diagnostic(self):
            return {'reason':'HANDLER_BUILD', 'exception_type':'FullRuntimeRefused', 'authority':'none'}
        def close(self): return {'state':'HOLD', 'owned':True, 'authority':'none'}
    owner = Owner()
    monkeypatch.setattr(runtime, 'create_single_manifest_owner', lambda **kwargs: owner)
    adapter = _local_adapter()
    assert adapter.start()['state'] == 'STARTING'
    assert adapter.wait(timeout=5) == {'state':'HOLD', 'owned':True, 'authority':'none'}
    assert adapter.start_diagnostic() == {'reason':'HANDLER_BUILD', 'exception_type':'FullRuntimeRefused', 'authority':'none'}
    assert adapter.close() == {'state':'HOLD', 'owned':True, 'authority':'none'}
    assert adapter._owner is owner


def test_local_adapter_thread_start_exception_type_unchanged(monkeypatch):
    adapter = _local_adapter()
    def fail(_): raise RuntimeError('synthetic-private-thread-error')
    monkeypatch.setattr(child.threading.Thread, 'start', fail)
    with pytest.raises(RuntimeError): adapter.start()
    assert adapter.status() == {'state':'HOLD', 'owned':None, 'authority':'none'}
    assert adapter.start_diagnostic() == {'reason':'THREAD_START', 'exception_type':'RuntimeError', 'authority':'none'}


def test_fixed_cli_routes_only_explicit_owner_diagnostics(monkeypatch):
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'a'*64)
    monkeypatch.setattr(child.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO()))
    monkeypatch.setattr(child.sys, 'stdout', SimpleNamespace(buffer=io.BytesIO()))
    calls = []
    def guarded(entry, **kwargs):
        calls.append((entry, kwargs))
        return 73
    monkeypatch.setattr(child, '_guarded_local_entry', guarded)
    argv = ['--mind-local-candidate-owner', '--policy-path', 'synthetic-policy', '--policy-sha256',
            'b'*64, '--approval-reference-sha256', 'c'*64]
    assert child._fixed_child_main(argv) == 73
    assert calls[-1][1]['include_start_diagnostics'] is False
    assert child._fixed_child_main(argv + ['--start-diagnostics']) == 73
    assert calls[-1][1]['include_start_diagnostics'] is True
    assert child._fixed_child_main(argv + ['--start-diagnostics', '--start-diagnostics']) == 2
    assert child._fixed_child_main(['--parent-control', '--start-diagnostics']) == 2
    assert len(calls) == 2


def test_pinned_cli_bootstrap_forwards_explicit_optin_unchanged(monkeypatch):
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'a'*64)
    monkeypatch.setattr(child, '__name__', '__main__')
    forwarded = []
    canonical = SimpleNamespace(_fixed_child_main=lambda argv: forwarded.append(argv) or 73)
    monkeypatch.setattr(child, '_load_pinned_canonical_child', lambda bundle, digest: canonical)
    argv = ['--mind-local-candidate-owner', '--policy-sha256', 'b'*64,
            '--approval-reference-sha256', 'c'*64, '--start-diagnostics']
    assert child._fixed_child_main(argv) == 73
    assert forwarded == [argv]


def test_local_adapter_diagnostic_does_not_wait_for_contended_lock():
    adapter = _local_adapter()
    response = []
    adapter._lock.acquire()
    thread = threading.Thread(target=lambda: response.append(adapter.start_diagnostic()), daemon=True)
    try:
        thread.start()
        thread.join(1)
        assert not thread.is_alive()
    finally:
        adapter._lock.release()
        thread.join(2)
    assert response == [{'reason':'UNKNOWN', 'exception_type':'UNKNOWN', 'authority':'none'}]


def _cli_fixture(monkeypatch, *, delayed, malicious=False, partial=False, diagnostic_write_fails=False,
                 entry_after_adapter=False, post_ready=False, diagnostic_stall=None):
    from tools import wd_mind_runtime_process as process
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)  # synthetic only
    release = threading.Event()
    close_called = threading.Event()
    private = b'synthetic-policy-never-in-output'
    stream = io.BytesIO(b'MCL1' + struct.pack('>I', len(private)) + private)
    class Input:
        def read(self, size):
            data = stream.read(size)
            if data: return data
            assert release.wait(5)
            return b'S'
    class Output:
        def __init__(self): self.lines, self.pending = [], bytearray()
        def write(self, data):
            raw = bytes(data)
            if diagnostic_write_fails and b'wd.mind-local-owner-start-diagnostic.v1' in raw:
                raise OSError('synthetic-write-error')
            if diagnostic_stall and b'wd.mind-local-owner-start-diagnostic.v1' in raw:
                diagnostic_stall[0].set()
                assert diagnostic_stall[1].wait(5)
            accepted = raw[:min(len(raw), 7)] if partial else raw
            self.pending.extend(accepted)
            while b'\n' in self.pending:
                end = self.pending.index(b'\n') + 1
                line = bytes(self.pending[:end])
                del self.pending[:end]
                self.lines.append(line)
                record = json.loads(line)
                if record.get('state') == 'HOLD': release.set()
            return len(accepted)
        def flush(self): pass
    class Owner:
        _owner = object()
        def __init__(self): self.closes, self.reads = 0, 0
        def start(self):
            if entry_after_adapter: raise ValueError('synthetic-entry-secret')
            return {'state': 'STARTING'}
        def status(self):
            return {'state': 'READY' if post_ready else 'HOLD', 'owned': True, 'authority': 'none'}
        def start_diagnostic(self):
            self.reads += 1
            if entry_after_adapter:
                return {'reason':'UNKNOWN', 'exception_type':'UNKNOWN', 'authority':'none'}
            if malicious:
                return {'reason': 'synthetic-private-text', 'exception_type': 'synthetic-private-text',
                        'authority': 'none'}
            if delayed and self.reads == 1:
                return {'reason': 'UNKNOWN', 'exception_type': 'UNKNOWN', 'authority': 'none'}
            return {'reason': 'HANDLER_BUILD', 'exception_type': 'FullRuntimeRefused', 'authority': 'none'}
        def close(self):
            self.closes += 1
            close_called.set()
            return {'state': 'HOLD', 'owned': True} if delayed and self.closes == 1 else {'state': 'STOPPED', 'owned': False}
    owner, output = Owner(), Output()
    output.owner = owner
    output.close_called = close_called
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: owner)
    if post_ready:
        class Service:
            def __init__(self): self.reads = 0
            def start(self): pass
            def status(self):
                self.reads += 1
                return {'state': 'RUNNING' if self.reads == 1 else 'FAILED'}
            def close(self, *, timeout): return {'state':'STOPPED'}
        monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    return Input(), output, private


@pytest.mark.parametrize('optin', [False, True])
def test_cli_legacy_wire_stays_exact_and_diagnostic_is_optin(monkeypatch, optin):
    control, output, private = _cli_fixture(monkeypatch, delayed=False)
    args = dict(control_stream=control, output_stream=output, bundle_root='synthetic',
                expected_manifest_sha256='a'*64, policy_sha256='b'*64, approval_reference_sha256='c'*64)
    if optin: args['include_start_diagnostics'] = True
    assert child._local_candidate_owner_entry(**args) == 2
    values = [json.loads(raw) for raw in output.lines]
    status = [row for row in values if row['schema'] == 'wd.mind-local-owner-status.v1']
    assert [row['state'] for row in status] == ['STARTING', 'HOLD', 'STOPPED']
    expected = {'schema':'wd.mind-local-owner-status.v1', 'manifest_sha256':'a'*64,
        'authority':'none', 'caller_authentication':'UNKNOWN', 'source_authentication':'UNKNOWN',
        'index_refresh':'NOT_SCHEDULED'}
    for row in status:
        assert row == dict(expected, state=row['state'])
    for raw in output.lines:
        row = json.loads(raw)
        if row['schema'] == 'wd.mind-local-owner-status.v1':
            assert raw == _v1_wire(row['state'])
    diagnostics = [row for row in values if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v1']
    assert len(diagnostics) == int(optin)
    if optin:
        assert diagnostics[0] == {'schema':'wd.mind-local-owner-start-diagnostic.v1',
            'reason':'HANDLER_BUILD', 'exception_type':'FullRuntimeRefused', 'authority':'none'}
        assert values.index(status[1]) < values.index(diagnostics[0]) < values.index(status[2])
        assert (b'{"authority":"none","exception_type":"FullRuntimeRefused","reason":"HANDLER_BUILD",'
                b'"schema":"wd.mind-local-owner-start-diagnostic.v1"}\n') in output.lines
    for raw in output.lines:
        assert len(raw) <= 1024 and private not in raw


def test_optin_reports_late_cause_after_hold_without_repeating_v1_hold(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=True)
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True) == 2
    rows = [json.loads(raw) for raw in output.lines]
    statuses = [row['state'] for row in rows if row['schema'] == 'wd.mind-local-owner-status.v1']
    assert statuses == ['STARTING', 'HOLD', 'STOPPED']
    held = next(i for i, row in enumerate(rows) if row.get('state') == 'HOLD')
    stopped = next(i for i, row in enumerate(rows) if row.get('state') == 'STOPPED')
    diagnostics = [(i, row) for i, row in enumerate(rows) if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v1']
    assert 1 <= len(diagnostics) <= 3
    assert all(held < i < stopped for i, _ in diagnostics)
    assert any(row['reason'] == 'HANDLER_BUILD' and row['exception_type'] == 'FullRuntimeRefused'
               for _, row in diagnostics)
    assert all(len(raw) <= 1024 for raw in output.lines)


def test_optin_never_emits_unrecognized_diagnostic_values(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=False, malicious=True)
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True) == 2
    assert b'synthetic-private-text' not in b''.join(output.lines)
    rows = [json.loads(raw) for raw in output.lines]
    diagnostics = [row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v1']
    assert all(row == {'schema':'wd.mind-local-owner-start-diagnostic.v1', 'reason':'UNKNOWN',
                       'exception_type':'UNKNOWN', 'authority':'none'} for row in diagnostics)


def test_partial_writes_complete_v1_and_optin_records(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=False, partial=True)
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True) == 2
    assert not output.pending
    rows = [json.loads(raw) for raw in output.lines]
    assert [row['state'] for row in rows if row['schema'] == 'wd.mind-local-owner-status.v1'] == [
        'STARTING', 'HOLD', 'STOPPED']
    assert any(row['schema'] == 'wd.mind-local-owner-start-diagnostic.v1' for row in rows)
    assert all(len(raw) <= 1024 for raw in output.lines)


def test_diagnostic_write_failure_does_not_repeat_successful_owner_close(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=True, diagnostic_write_fails=True)
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=control, output_stream=output, bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True)), daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive()
    assert result == [2] and output.owner.closes == 2
    assert [json.loads(raw)['state'] for raw in output.lines] == ['STARTING', 'HOLD', 'STOPPED']


def test_stalled_diagnostic_write_must_not_block_owner_close(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    control, output, _ = _cli_fixture(monkeypatch, delayed=False, diagnostic_stall=(entered, release))
    result = []
    thread = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=control, output_stream=output, bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True)), daemon=True)
    thread.start()
    writers = []
    try:
        assert entered.wait(3)
        assert output.close_called.wait(1)  # close is independent of a stalled diagnostic sink
        writers = [item for item in threading.enumerate() if item.name == 'mind-owner-start-diagnostic']
        assert len(writers) == 1 and writers[0].is_alive() and not writers[0].daemon
        assert not result  # no false STOPPED/exit while the diagnostic writer is still owned
        assert [json.loads(raw)['state'] for raw in output.lines] == ['STARTING', 'HOLD']
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and result == [2]
    assert all(not writer.is_alive() for writer in writers)


def test_diagnostic_thread_start_failure_does_not_skip_owner_close(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=False)
    original_start = threading.Thread.start
    retained = []
    def synthetic_start(thread):
        if thread.name == 'mind-owner-start-diagnostic':
            retained.append(thread)
            raise RuntimeError('synthetic-secret')
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', synthetic_start)
    result = []
    runner = threading.Thread(target=lambda: result.append(child._local_candidate_owner_entry(
        control_stream=control, output_stream=output, bundle_root='synthetic',
        expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True)), daemon=True)
    runner.start()
    try:
        assert output.close_called.wait(3)
        assert len(retained) == 1 and not result  # uncertain start may still become runnable
        assert [json.loads(raw)['state'] for raw in output.lines] == ['STARTING', 'HOLD']
    finally:
        if retained: original_start(retained[0])  # simulate a late scheduled thread, no forced termination
        runner.join(5)
    assert not runner.is_alive() and result == [2]
    assert output.owner.closes == 1
    assert b'synthetic-secret' not in b''.join(output.lines)


def test_diagnostic_start_exception_after_launch_keeps_thread_owned(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=False)
    original_start = threading.Thread.start
    def synthetic_start(thread):
        result = original_start(thread)
        if thread.name == 'mind-owner-start-diagnostic':
            raise RuntimeError('synthetic-started-thread-error')
        return result
    monkeypatch.setattr(threading.Thread, 'start', synthetic_start)
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True) == 2
    assert output.owner.closes == 1
    assert b'synthetic-started-thread-error' not in b''.join(output.lines)


def test_known_outer_entry_reason_is_not_overwritten_by_unknown_adapter(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=False, entry_after_adapter=True)
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True) == 2
    rows = [json.loads(raw) for raw in output.lines]
    diagnostic = next(row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v1')
    assert diagnostic['reason'] == 'OWNER_ENTRY' and diagnostic['exception_type'] == 'ValueError'
    assert b'synthetic-entry-secret' not in b''.join(output.lines)


def test_postready_service_failure_is_not_labeled_startup_failure(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=False, post_ready=True)
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_start_diagnostics=True) == 2
    rows = [json.loads(raw) for raw in output.lines]
    assert [row['state'] for row in rows] == ['STARTING', 'READY', 'HOLD', 'STOPPED']
    base = {'schema':'wd.mind-local-owner-status.v1', 'manifest_sha256':'a'*64, 'authority':'none',
            'caller_authentication':'UNKNOWN', 'source_authentication':'UNKNOWN', 'index_refresh':'NOT_SCHEDULED'}
    for raw, row in zip(output.lines, rows):
        assert row == dict(base, state=row['state'])
        assert raw == _v1_wire(row['state'])


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell manual helper is Windows-specific')
@pytest.mark.parametrize('shell', ['powershell.exe', 'pwsh.exe'])
@pytest.mark.parametrize('mode', ['legacy', 'legacy_record', 'valid', 'not_enabled', 'before_hold',
                                  'bad_reason', 'bad_type', 'extra_field', 'oversize', 'updated', 'triple'])
def test_manual_parser_requires_optin_prior_hold_and_bounded_enums(shell, mode):
    executable = shutil.which(shell)
    if executable is None: pytest.skip('requested PowerShell version unavailable')
    helper = MANUAL_HELPER
    script = Path(__file__).with_name('test_wd_mind_manual_diagnostic_parser.ps1')
    base = {'schema': 'wd.mind-local-owner-status.v1', 'state': 'HOLD', 'manifest_sha256': 'a'*64,
            'authority':'none', 'caller_authentication':'UNKNOWN', 'source_authentication':'UNKNOWN',
            'index_refresh':'NOT_SCHEDULED'}
    diagnostic = {'schema':'wd.mind-local-owner-start-diagnostic.v1', 'reason':'HANDLER_BUILD',
                  'exception_type':'FullRuntimeRefused', 'authority':'none'}
    if mode == 'bad_reason': diagnostic['reason'] = 'fake-private-content'
    if mode == 'bad_type': diagnostic['exception_type'] = 'fake-private-content'
    if mode == 'extra_field': diagnostic['extra'] = 'fake-private-content'
    if mode == 'oversize': diagnostic['reason'] = 'X'*1025
    records = [base] if mode in ('legacy', 'legacy_record') else ([diagnostic, base] if mode == 'before_hold' else [base, diagnostic])
    if mode in ('updated', 'triple'):
        unknown = dict(diagnostic, reason='UNKNOWN', exception_type='UNKNOWN')
        records = [base, unknown, diagnostic] + ([diagnostic] if mode == 'triple' else [])
    wire = ''.join(json.dumps(row, sort_keys=True, separators=(',', ':'))+'\n' for row in records)
    arguments = json.dumps({'wire': wire, 'optin': mode not in ('legacy', 'legacy_record', 'not_enabled'),
                            'legacyRecord': mode == 'legacy_record'})
    proc = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(script), '-HelperPath', str(helper)], input=arguments,
                          capture_output=True, text=True, check=True)
    result = json.loads(proc.stdout.lstrip('\ufeff'))
    assert 'fake-private-content' not in proc.stdout + proc.stderr
    assert result['status'] == {'schema':'wd.mind-manual-owner-status.v1', 'state':'HOLD',
        'authority':'none', 'caller_authentication':'UNKNOWN', 'index_refresh':'NOT_SCHEDULED'}
    if mode in ('legacy', 'legacy_record'):
        assert not result['invalid'] and result['diagnostic'] is None
    elif mode in ('valid', 'updated'):
        assert not result['invalid'] and result['diagnostic'] == diagnostic
    else:
        assert result['invalid']

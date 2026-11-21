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
    # These diagnostics fixtures have no real source snapshot; separate tests
    # exercise the enabled gate and prewarm path.
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', False)
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


@pytest.mark.parametrize('code,hint', [(20,('arguments',None)), (21,('model_pin',None)), (22,('offline_environment',None)),
    (23,('reader_mutex',None)), (24,('library_import',None)), (25,('model_load',None)), (29,('serving',None)),
    (2,('UNKNOWN',2)), (500,('UNKNOWN',500)), (None,('UNKNOWN',None))])
def test_worker_hint_through_retained_owner_chain_without_process(code, hint):
    from tools import wd_mind_e5_lifecycle as lifecycle
    from tools import wd_mind_e5_rotating_client as rotating
    worker = lifecycle._Owner()  # Its keeper is constructed but never started.
    worker._code = code
    worker._verified_exit_code = code
    rotator = object.__new__(rotating.RotatingGracefulE5Client)
    rotator._lock, rotator._owner, rotator._owner_conflicts = threading.Lock(), worker, []
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._lock, owner._rotation_enabled, owner._owner_conflicts = threading.Lock(), True, []
    owner._client = rotator
    adapter = _local_adapter()
    adapter._owner = owner
    assert adapter.worker_exit_diagnostic() == hint
    rotator._lock.acquire()
    try: assert adapter.worker_exit_diagnostic() == ('UNKNOWN', None)
    finally: rotator._lock.release()
    rotator._owner_conflicts.append(object())
    assert adapter.worker_exit_diagnostic() == ('UNKNOWN', None)


def test_worker_hint_rejects_untrusted_objects_and_text():
    assert child._safe_worker_diagnostic('synthetic-private-text') == ('UNKNOWN', None)
    assert child._safe_worker_diagnostic(None) == ('UNKNOWN', None)
    assert child._safe_worker_diagnostic(['model_load']) == ('UNKNOWN', None)
    assert child._safe_worker_diagnostic(('UNKNOWN', True)) == ('UNKNOWN', None)
    assert child._safe_worker_diagnostic(('UNKNOWN', 2)) == ('UNKNOWN', 2)
    assert child._safe_worker_diagnostic(('UNKNOWN', 20)) == ('UNKNOWN', None)


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


@pytest.mark.parametrize('anchor', [None, '', 'synthetic-invalid-anchor'])
@pytest.mark.parametrize('flag', [None, '--start-diagnostics', '--start-diagnostics-v2'])
def test_invalid_manifest_anchor_emits_only_fixed_optin_diagnostic(monkeypatch, capsys, anchor, flag):
    if anchor is None:
        monkeypatch.delenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', raising=False)
    else:
        monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', anchor)
    monkeypatch.setattr(child, '_guarded_local_entry', lambda *_a, **_k: pytest.fail('owner entry reached'))
    args = ['--mind-local-candidate-owner', '--policy-sha256', 'b'*64,
            '--approval-reference-sha256', 'c'*64]
    if flag:
        args.append(flag)
    assert child._fixed_child_main(args) == 2
    captured = capsys.readouterr()
    assert captured.err == ''
    expected = '{"authority":"none","reason":"IMPORT","schema":"wd.mind-local-owner-preanchor-diagnostic.v1"}\n'
    assert captured.out == (expected if flag else '')


@pytest.mark.parametrize('flag', [None, '--start-diagnostics', '--start-diagnostics-v2'])
@pytest.mark.parametrize('with_path', [False, True])
def test_unverified_well_formed_anchor_never_appears_in_diagnostic(monkeypatch, capsys, flag, with_path):
    unverified = 'd' * 64
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', unverified)
    monkeypatch.setattr(child, '__name__', '__main__')
    def refuse(*_args):
        raise ValueError('synthetic-private-text')
    monkeypatch.setattr(child, '_load_pinned_canonical_child', refuse)
    monkeypatch.setattr(child, '_guarded_local_entry', lambda *_a, **_k: pytest.fail('owner entry reached'))
    args = ['--mind-local-candidate-owner']
    if with_path:
        args += ['--policy-path', 'synthetic-private-path']
    args += ['--policy-sha256', 'b'*64, '--approval-reference-sha256', 'c'*64]
    if flag:
        args.append(flag)
    assert child._fixed_child_main(args) == 2
    captured = capsys.readouterr()
    expected = '{"authority":"none","reason":"IMPORT","schema":"wd.mind-local-owner-preanchor-diagnostic.v1"}\n'
    assert captured.out == (expected if flag else '')
    assert captured.err == ''
    assert unverified not in captured.out and 'synthetic-private' not in captured.out


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


@pytest.mark.parametrize('mode', ['off', 'v1', 'v2'])
def test_cli_source_budget_preflight_reason_is_fixed_and_optin(monkeypatch, mode):
    control, output, private = _cli_fixture(monkeypatch, delayed=False)
    output.owner.start_diagnostic = lambda: {'reason':'SOURCE_BUDGET',
        'exception_type':'UNKNOWN', 'authority':'none'}
    args = dict(control_stream=control, output_stream=output, bundle_root='synthetic',
                expected_manifest_sha256='a'*64, policy_sha256='b'*64, approval_reference_sha256='c'*64,
                include_start_diagnostics=mode == 'v1', include_worker_stage_diagnostics=mode == 'v2')
    assert child._local_candidate_owner_entry(**args) == 2
    rows = [json.loads(raw) for raw in output.lines]
    diagnostics = [row for row in rows if row['schema'].startswith('wd.mind-local-owner-start-diagnostic.')]
    if mode == 'off':
        assert diagnostics == []
    else:
        expected = {'schema':'wd.mind-local-owner-start-diagnostic.' + mode, 'reason':'SOURCE_BUDGET',
                    'exception_type':'UNKNOWN', 'authority':'none'}
        if mode == 'v2': expected.update(worker_exit_code=None, worker_exit_stage_hint='UNKNOWN')
        assert diagnostics == [expected]
        assert any(row.get('state') == 'HOLD' for row in rows[:rows.index(diagnostics[0])])
    assert all(private not in raw for raw in output.lines)


@pytest.mark.parametrize('hint,expected', [(('model_load',None),('model_load',None)),
    (('UNKNOWN',2),('UNKNOWN',2)), ('private-secret',('UNKNOWN',None)), (None,('UNKNOWN',None))])
def test_worker_stage_v2_wire_is_bounded_and_fixed(monkeypatch, hint, expected):
    control, output, private = _cli_fixture(monkeypatch, delayed=False)
    output.owner.worker_exit_diagnostic = lambda: hint
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True) == 2
    rows = [json.loads(raw) for raw in output.lines]
    diagnostic = next(row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v2')
    assert diagnostic == {'schema':'wd.mind-local-owner-start-diagnostic.v2', 'reason':'HANDLER_BUILD',
        'exception_type':'FullRuntimeRefused', 'worker_exit_stage_hint':expected[0],
        'worker_exit_code':expected[1], 'authority':'none'}
    for raw in output.lines:
        assert len(raw) <= 1024 and private not in raw and b'private-secret' not in raw


def test_worker_stage_v2_emits_verified_late_code_after_unknown(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=True)
    unknown_emitted = threading.Event()
    original_write = output.write
    def write(data):
        n = original_write(data)
        if b'worker_exit_code' in bytes(data): unknown_emitted.set()
        return n
    output.write = write
    original_close = output.owner.close
    def close():
        unknown_emitted.wait(1)
        return original_close()
    output.owner.close = close
    output.owner.worker_exit_diagnostic = lambda: (('model_load', None) if output.close_called.is_set()
                                                     else ('UNKNOWN', None))
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True) == 2
    assert unknown_emitted.is_set()
    diagnostics = [json.loads(raw) for raw in output.lines
                   if json.loads(raw)['schema'] == 'wd.mind-local-owner-start-diagnostic.v2']
    assert [d['worker_exit_stage_hint'] for d in diagnostics] == ['UNKNOWN', 'model_load']


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
    assert [row['state'] for row in rows] == ['STARTING', 'HOLD', 'STOPPED']
    base = {'schema':'wd.mind-local-owner-status.v1', 'manifest_sha256':'a'*64, 'authority':'none',
            'caller_authentication':'UNKNOWN', 'source_authentication':'UNKNOWN', 'index_refresh':'NOT_SCHEDULED'}
    for raw, row in zip(output.lines, rows):
        assert row == dict(base, state=row['state'])
        assert raw == _v1_wire(row['state'])


@pytest.mark.parametrize('value', [('serving', None), ('UNKNOWN', 99), ('UNKNOWN', None)])
def test_postready_failure_reports_optin_v2_without_startup_misattribution(monkeypatch, value):
    control, output, private = _cli_fixture(monkeypatch, delayed=False, post_ready=True)
    output.owner.worker_exit_diagnostic = lambda: value
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True) == 2
    rows = [json.loads(raw) for raw in output.lines]
    statuses = [row['state'] for row in rows if row['schema'] == 'wd.mind-local-owner-status.v1']
    assert statuses == ['STARTING', 'HOLD', 'STOPPED']
    diagnostics = [row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v2']
    assert len(diagnostics) == 1
    assert diagnostics[0] == {'schema':'wd.mind-local-owner-start-diagnostic.v2', 'authority':'none',
        'reason':'UNKNOWN', 'exception_type':'UNKNOWN', 'worker_exit_stage_hint':value[0],
        'worker_exit_code':value[1]}
    assert all(private not in raw for raw in output.lines)


def test_postready_v2_reports_late_exit_without_discarding_owner(monkeypatch):
    control, output, _ = _cli_fixture(monkeypatch, delayed=True, post_ready=True)
    first_diag = threading.Event()
    original_write, original_close = output.write, output.owner.close
    def write(data):
        count = original_write(data)
        if b'worker_exit_code' in bytes(data): first_diag.set()
        return count
    def close():
        first_diag.wait(1)
        return original_close()
    output.write, output.owner.close = write, close
    output.owner.worker_exit_diagnostic = lambda: (('serving', None) if output.close_called.is_set()
                                                     else ('UNKNOWN', None))
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True) == 2
    rows = [json.loads(raw) for raw in output.lines]
    diagnostics = [row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v2']
    assert [row['worker_exit_stage_hint'] for row in diagnostics] == ['UNKNOWN', 'serving']
    assert output.owner.closes == 2 and first_diag.is_set()
    assert all(row['reason'] == row['exception_type'] == 'UNKNOWN' for row in diagnostics)


def test_postready_v2_clean_stop_emits_no_failure_diagnostic(monkeypatch):
    from tools import wd_mind_runtime_process as process
    control, output, _ = _cli_fixture(monkeypatch, delayed=False, post_ready=True)
    adapter = _local_adapter()
    adapter._owner, adapter._state = output.owner, 'READY'
    adapter._owner_start_committed = True
    output.owner.start_diagnostic = lambda: {'reason':'NONE', 'exception_type':'NONE', 'authority':'none'}
    output.owner.worker_exit_diagnostic = lambda: ('UNKNOWN', None)
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    monkeypatch.setattr(child._LocalManifestQuery, 'start',
                        lambda self: {'state':'STARTING', 'owned':None, 'authority':'none'})
    original_write = output.write
    ready = threading.Event()
    def write(data):
        written = original_write(data)
        return written
    # The input's frame is consumed before the control thread calls read.
    frame = io.BytesIO(b'MCL1' + struct.pack('>I', len(b'synthetic-policy-never-in-output'))
                       + b'synthetic-policy-never-in-output')
    def read_frame_then_stop(size):
        value = frame.read(size)
        if value: return value
        assert ready.wait(5)
        return b'S'
    control.read, output.write = read_frame_then_stop, write
    class Service:
        def start(self): pass
        def status(self):
            self.status_calls = getattr(self, 'status_calls', 0) + 1
            if self.status_calls == 2: ready.set()  # First RUNNING was already handled internally.
            return {'state':'RUNNING'}
        def close(self, *, timeout): return {'state':'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    evidence = {'clean': False}
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True,
        _cleanup_evidence=evidence) == 0
    rows = [json.loads(raw) for raw in output.lines]
    states = [row['state'] for row in rows]
    assert states[0] == 'STARTING' and states[-1] == 'STOPPED' and 'READY' not in states
    assert all(state == 'HOLD' for state in states[1:-1])
    assert evidence['clean'] is True and output.owner.closes == 1
    assert all(row['schema'] == 'wd.mind-local-owner-status.v1' for row in rows)


@pytest.mark.parametrize('exit_value,is_failure', [
    (('serving', None), True), (('UNKNOWN', 99), True), (('UNKNOWN', 2), True),
    (('UNKNOWN', 0), False), (('UNKNOWN', None), False),
])
def test_postready_v2_detects_worker_exit_during_clean_stop_cleanup(monkeypatch, exit_value, is_failure):
    from tools import wd_mind_runtime_process as process
    control, output, _ = _cli_fixture(monkeypatch, delayed=True, post_ready=True)
    ready, diag = threading.Event(), threading.Event()
    original_write, original_close = output.write, output.owner.close
    frame = io.BytesIO(b'MCL1' + struct.pack('>I', len(b'synthetic-policy-never-in-output'))
                       + b'synthetic-policy-never-in-output')
    def read(size):
        value = frame.read(size)
        if value: return value
        assert ready.wait(5)
        return b'S'
    def write(data):
        written = original_write(data)
        raw = bytes(data)
        if b'worker_exit_stage_hint' in raw: diag.set()
        return written
    def close():
        if output.owner.closes and is_failure:
            diag.wait(1)
        return original_close()
    control.read, output.write, output.owner.close = read, write, close
    output.owner.worker_exit_diagnostic = lambda: (exit_value if output.close_called.is_set()
                                                     else ('UNKNOWN', None))
    class Service:
        def start(self): pass
        def status(self):
            self.status_calls = getattr(self, 'status_calls', 0) + 1
            if self.status_calls == 2: ready.set()  # First RUNNING was already handled internally.
            return {'state':'RUNNING'}
        def close(self, *, timeout): return {'state':'STOPPED'}
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    assert child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True) == (2 if is_failure else 0)
    rows = [json.loads(raw) for raw in output.lines]
    assert [row['state'] for row in rows if row['schema'] == 'wd.mind-local-owner-status.v1'] == [
        'STARTING', 'HOLD', 'STOPPED']
    diagnostics = [row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v2']
    assert len(diagnostics) == int(is_failure)
    if is_failure:
        assert (diagnostics[0]['worker_exit_stage_hint'], diagnostics[0]['worker_exit_code']) == exit_value
    assert output.owner.closes == 2


def test_real_adapter_does_not_lose_exit_hint_when_close_worker_finishes_first(monkeypatch):
    """Synthetic owner only: this does not model actual rotator exit semantics."""
    from tools import wd_mind_runtime_process as process

    control, output, _ = _cli_fixture(monkeypatch, delayed=True, post_ready=True)
    ready, closed_twice = threading.Event(), threading.Event()
    evidence, close_threads, second_close_observations = {'clean': False}, [], []
    adapter = _local_adapter()
    adapter._owner, adapter._state = output.owner, 'READY'
    adapter._owner_start_committed = True
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    monkeypatch.setattr(child._LocalManifestQuery, 'start',
                        lambda self: {'state': 'STARTING', 'owned': None, 'authority': 'none'})
    output.owner.start_diagnostic = lambda: {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
    original_close, original_write = output.owner.close, output.write

    def close():
        close_threads.append(threading.current_thread())
        result = original_close()
        if output.owner.closes == 2:
            second_close_observations.append((evidence['clean'],
                any(json.loads(raw).get('state') == 'STOPPED' for raw in output.lines)))
            closed_twice.set()
        return result

    def write(data):
        result = original_write(data)
        return result

    frame = io.BytesIO(b'MCL1' + struct.pack('>I', len(b'synthetic-policy-never-in-output'))
                       + b'synthetic-policy-never-in-output')

    def read(size):
        value = frame.read(size)
        if value:
            return value
        assert ready.wait(5)
        return b'S'

    class Service:
        def start(self): pass
        def status(self):
            self.status_calls = getattr(self, 'status_calls', 0) + 1
            if self.status_calls == 2: ready.set()  # First RUNNING was already handled internally.
            return {'state':'RUNNING'}
        def close(self, *, timeout):
            assert closed_twice.wait(5)
            # Joining ensures close_adapter_once has published its completion;
            # otherwise the main thread could race the second close's return.
            close_threads[0].join(timeout=5)
            assert not close_threads[0].is_alive()
            return {'state': 'STOPPED'}

    output.owner.close = close
    output.owner.worker_exit_diagnostic = lambda: (('serving', None) if output.close_called.is_set()
                                                     else ('UNKNOWN', None))
    control.read, output.write = read, write
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result = child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True,
        _cleanup_evidence=evidence)
    rows = [json.loads(raw) for raw in output.lines]
    assert closed_twice.is_set() and output.owner.closes == 2
    assert len(set(close_threads)) == 1 and second_close_observations == [(False, False)]
    assert evidence['clean'] is True
    assert result == 2
    assert any(row.get('worker_exit_stage_hint') == 'serving' for row in rows)


@pytest.mark.parametrize('hint,is_failure', [
    (('serving', None), True), (('UNKNOWN', 2), True),
    (('UNKNOWN', 0), False), (('UNKNOWN', None), False),
])
def test_real_adapter_retains_worker_hint_after_close_exception(monkeypatch, hint, is_failure):
    """Use an inert fake owner: an exception followed by success is a test seam."""
    from tools import wd_mind_runtime_process as process

    control, output, _ = _cli_fixture(monkeypatch, delayed=False, post_ready=True)
    ready, close_attempted_twice = threading.Event(), threading.Event()
    evidence, close_threads = {'clean': False}, []
    adapter = _local_adapter()
    adapter._owner, adapter._state = output.owner, 'READY'
    adapter._owner_start_committed = True
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    monkeypatch.setattr(child._LocalManifestQuery, 'start',
                        lambda self: {'state': 'STARTING', 'owned': None, 'authority': 'none'})
    output.owner.start_diagnostic = lambda: {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
    original_write = output.write

    def close():
        close_threads.append(threading.current_thread())
        output.owner.closes += 1
        output.close_called.set()
        if output.owner.closes == 1:
            raise RuntimeError('synthetic-close-error')
        close_attempted_twice.set()
        return {'state': 'STOPPED', 'owned': False}

    def write(data):
        result = original_write(data)
        return result

    frame = io.BytesIO(b'MCL1' + struct.pack('>I', len(b'synthetic-policy-never-in-output'))
                       + b'synthetic-policy-never-in-output')

    def read(size):
        value = frame.read(size)
        if value:
            return value
        assert ready.wait(5)
        return b'S'

    class Service:
        timed_out = False
        def start(self): pass
        def status(self):
            self.status_calls = getattr(self, 'status_calls', 0) + 1
            if self.status_calls == 2: ready.set()  # First RUNNING was already handled internally.
            return {'state':'RUNNING'}
        def close(self, *, timeout):
            if not close_attempted_twice.wait(5):
                self.timed_out = True
                return {'state': 'STOPPED'}
            close_threads[0].join(timeout=5)
            if close_threads[0].is_alive():
                self.timed_out = True
            return {'state': 'STOPPED'}

    service = Service()
    output.owner.close = close
    output.owner.worker_exit_diagnostic = lambda: hint if output.close_called.is_set() else ('UNKNOWN', None)
    control.read, output.write = read, write
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: service)
    result = child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True,
        _cleanup_evidence=evidence)
    rows = [json.loads(raw) for raw in output.lines]
    assert not service.timed_out and close_attempted_twice.is_set() and output.owner.closes == 2
    assert len(set(close_threads)) == 1 and evidence['clean'] is True
    assert result == (2 if is_failure else 0)
    diagnostics = [row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v2']
    if is_failure:
        assert any((row['worker_exit_stage_hint'], row['worker_exit_code']) == hint for row in diagnostics)
    else:
        assert not diagnostics


@pytest.mark.parametrize('hold_write', ['complete', 'raise', 'short', 'partial_then_raise'])
def test_real_adapter_worker_hint_during_hold_publication(monkeypatch, hold_write):
    """Exercise held HOLD writes after a real synthetic service observation.

    READY is intentionally never published; holding a HOLD write here does
    not reproduce the former blocked READY write.
    """
    from tools import wd_mind_runtime_process as process

    control, output, _ = _cli_fixture(monkeypatch, delayed=True, post_ready=True)
    service_observed, hold_write_entered, second_close, owner_close_entered = (
        threading.Event() for _ in range(4))
    evidence, close_threads, observations, timeouts = {'clean': False}, [], [], []
    wire, partial_pending, write_faults = bytearray(), [False], []
    adapter = _local_adapter()
    adapter._owner, adapter._state = output.owner, 'READY'
    adapter._owner_start_committed = True
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kwargs: adapter)
    monkeypatch.setattr(child._LocalManifestQuery, 'start',
                        lambda self: {'state': 'STARTING', 'owned': None, 'authority': 'none'})
    output.owner.start_diagnostic = lambda: {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
    original_close = output.owner.close

    def close():
        close_threads.append(threading.current_thread())
        owner_close_entered.set()
        if not hold_write_entered.wait(5):
            timeouts.append('hold_write')
        result = original_close()
        if output.owner.closes == 2:
            observations.append(('second_close', evidence['clean']))
            second_close.set()
        return result

    def worker_diagnostic():
        if (output.close_called.is_set() and output.owner.closes == 1 and close_threads
                and threading.current_thread() is close_threads[0]):
            observations.append(('hint', output.owner.closes, threading.current_thread()))
            return ('serving', None)
        return ('UNKNOWN', None)

    def write(data):
        raw = bytes(data)
        if partial_pending[0]:
            partial_pending[0] = False
            write_faults.append('partial_then_raise')
            raise OSError('synthetic-hold-write-error')
        if b'"state":"HOLD"' not in raw:
            wire.extend(raw)
            return len(raw)
        hold_write_entered.set()
        if hold_write == 'complete':
            wire.extend(raw)
        elif hold_write == 'partial_then_raise':
            wire.extend(raw[:8])
            partial_pending[0] = True
        if not second_close.wait(5):
            timeouts.append('second_close')
        elif close_threads:
            close_threads[0].join(timeout=5)
            if close_threads[0].is_alive():
                timeouts.append('close_thread')
        if hold_write == 'raise':
            write_faults.append('raise')
            raise OSError('synthetic-hold-write-error')
        if hold_write == 'short':
            write_faults.append('short')
            return 0
        return 8 if hold_write == 'partial_then_raise' else len(raw)

    frame = io.BytesIO(b'MCL1' + struct.pack('>I', len(b'synthetic-policy')) + b'synthetic-policy')

    def read(size):
        value = frame.read(size)
        if value:
            return value
        if not service_observed.wait(5):
            timeouts.append('service_observation')
        return b'S'

    class Service:
        def start(self): pass
        def status(self):
            self.status_calls = getattr(self, 'status_calls', 0) + 1
            if self.status_calls == 2:
                service_observed.set()  # The first RUNNING was already handled internally.
            return {'state': 'RUNNING'}
        def close(self, *, timeout):
            # Ensure the independent close worker owns the adapter gate before
            # the monitor continues to the HOLD write under test. Otherwise
            # the monitor itself can enter the fake owner's blocking close.
            if not owner_close_entered.wait(5):
                timeouts.append('owner_close_entry')
            return {'state': 'STOPPED'}

    output.owner.close = close
    output.owner.worker_exit_diagnostic = worker_diagnostic
    control.read, output.write = read, write
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kwargs: Service())
    result = child._local_candidate_owner_entry(control_stream=control, output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a'*64, policy_sha256='b'*64,
        approval_reference_sha256='c'*64, include_worker_stage_diagnostics=True,
        _cleanup_evidence=evidence)
    rows, malformed = [], []
    for raw in bytes(wire).splitlines():
        try:
            rows.append(json.loads(raw))
        except (ValueError, UnicodeError):
            malformed.append(raw)
    statuses = [row['state'] for row in rows if row['schema'] == 'wd.mind-local-owner-status.v1']
    diagnostics = [row for row in rows if row['schema'] == 'wd.mind-local-owner-start-diagnostic.v2']
    assert all(row.get('schema') in ('wd.mind-local-owner-status.v1',
                                      'wd.mind-local-owner-start-diagnostic.v2') for row in rows)
    assert len(diagnostics) <= 2
    for row in diagnostics:
        assert row == {'schema':'wd.mind-local-owner-start-diagnostic.v2', 'authority':'none',
                       'reason':'UNKNOWN', 'exception_type':'UNKNOWN',
                       'worker_exit_stage_hint':row['worker_exit_stage_hint'], 'worker_exit_code':None}
        assert row['worker_exit_stage_hint'] in ('UNKNOWN', 'serving')
    assert not timeouts and second_close.is_set() and output.owner.closes == 2
    assert len(set(close_threads)) == 1 and ('second_close', False) in observations
    assert any(item[0] == 'hint' and item[1] == 1 and item[2] is close_threads[0]
               for item in observations)
    assert evidence['clean'] is True and result == 2
    assert statuses[0] == 'STARTING'
    assert 'READY' not in statuses
    if hold_write == 'complete':
        assert not write_faults
    else:
        assert write_faults and set(write_faults) == {hold_write}
    if hold_write == 'complete':
        assert statuses[-1] == 'STOPPED'
        assert not malformed
        assert statuses.count('HOLD') == 1
        assert diagnostics and diagnostics[-1]['worker_exit_stage_hint'] == 'serving'
    else:
        if hold_write == 'partial_then_raise':
            # The stream contains an incomplete HOLD prefix followed by the
            # next frame. The child's internal cleanup evidence does not
            # establish STOPPED at the parent when the wire is malformed.
            assert malformed and all(raw.startswith(b'{"author') for raw in malformed)
            assert bytes(wire).endswith(b'"state":"STOPPED"}\n')
            # Internal cleanup succeeded, but a truncated HOLD prefix may
            # corrupt the subsequent STOPPED frame on the parent wire.
            if statuses[-1] != 'STOPPED':
                assert malformed[-1].endswith(b'"state":"STOPPED"}')
            if os.name == 'nt':
                from wd_mind_owned_compat import run_owned
                shell = shutil.which('powershell.exe')
                assert shell is not None
                script = Path(__file__).with_name('test_wd_mind_manual_diagnostic_parser.ps1')
                payload = json.dumps({'wire':bytes(wire).decode('ascii'), 'optin':True, 'legacyRecord':False})
                proc = run_owned([shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                  '-File', str(script), '-HelperPath', str(MANUAL_HELPER)],
                                 input=payload, text=True, encoding='utf-8', timeout=15,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                assert proc.returncode == 0, proc.stderr
                parent = json.loads(proc.stdout.lstrip('\ufeff'))
                assert parent['invalid'] is True and parent['status']['state'] == 'HOLD'
                assert parent['status']['state'] != 'STOPPED'
        else:
            assert statuses[-1] == 'STOPPED'
            assert not malformed
            assert diagnostics and diagnostics[-1]['worker_exit_stage_hint'] == 'serving'
    assert b'synthetic-hold-write-error' not in wire


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

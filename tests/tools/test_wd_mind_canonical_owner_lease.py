"""Synthetic canonical-loader checks; never load a live bundle."""

import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

import pytest

from tools import wd_mind_runtime_child as child
from test_wd_mind_runtime_process import candidate
from test_wd_mind_runtime_child import staged, staged_e5, staged_graceful


def _fixture(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    package = bundle / 'tools-bootstrap' / 'tools'
    package.mkdir(parents=True)
    init = b'# synthetic package\n'
    source = b'def _fixed_child_main(argv):\n    return 37\n'
    (package / '__init__.py').write_bytes(init)
    script = package / 'wd_mind_runtime_child.py'
    script.write_bytes(source)
    files = {'tools-bootstrap/tools/__init__.py': hashlib.sha256(init).hexdigest(),
             'tools-bootstrap/tools/wd_mind_runtime_child.py': hashlib.sha256(source).hexdigest()}
    raw = json.dumps({'schema_version': 1, 'files': files}, sort_keys=True).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    monkeypatch.setattr(child, '__file__', str(script))
    for name in tuple(sys.modules):
        if name == 'tools' or name.startswith('tools.'):
            monkeypatch.delitem(sys.modules, name, raising=False)
    return bundle, hashlib.sha256(raw).hexdigest(), script


def test_canonical_loader_executes_manifest_pinned_bytes(tmp_path, monkeypatch):
    bundle, digest, _ = _fixture(tmp_path, monkeypatch)
    try:
        module = child._load_pinned_canonical_child(bundle, digest)
        assert module is sys.modules['tools.wd_mind_runtime_child']
        assert module._fixed_child_main([]) == 37
        assert sys.modules['tools'].wd_mind_runtime_child is module
    finally:
        sys.modules.pop('tools.wd_mind_runtime_child', None)
        sys.modules.pop('tools', None)


def test_actual_script_dispatches_through_pinned_canonical_module(tmp_path, monkeypatch):
    bundle, _, script = _fixture(tmp_path, monkeypatch)
    # The fixture replaces __file__; use the checked-out source, not live state.
    source = Path(__file__).resolve().parents[2] / 'tools/wd_mind_runtime_child.py'
    script.write_bytes(source.read_bytes())
    package = script.parent / '__init__.py'
    files = {'tools-bootstrap/tools/__init__.py': hashlib.sha256(package.read_bytes()).hexdigest(),
             'tools-bootstrap/tools/wd_mind_runtime_child.py': hashlib.sha256(script.read_bytes()).hexdigest()}
    raw = json.dumps({'schema_version': 1, 'files': files}, sort_keys=True).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(sys, 'argv', [str(script), '--parent-control'])
    try:
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(script), run_name='__main__')
        assert result.value.code == 2  # Existing parent gate refuses before side effects.
        assert sys.modules['tools'].wd_mind_runtime_child is sys.modules['tools.wd_mind_runtime_child']
        assert sys.modules['tools.wd_mind_runtime_child'].__file__ == str(script)
    finally:
        sys.modules.pop('tools.wd_mind_runtime_child', None)
        sys.modules.pop('tools', None)


@pytest.mark.parametrize('fault', ['manifest', 'source', 'package', 'module_collision', 'foreign_submodule', 'reentrant_init'])
def test_canonical_loader_refuses_changed_inputs_before_execution(tmp_path, monkeypatch, fault):
    bundle, digest, script = _fixture(tmp_path, monkeypatch)
    if fault == 'manifest':
        digest = '0' * 64
    elif fault == 'source':
        script.write_bytes(b'raise AssertionError("must not execute")\n')
    elif fault == 'package':
        (script.parent / '__init__.py').write_bytes(b'raise AssertionError("must not execute")\n')
    elif fault == 'module_collision':
        monkeypatch.setitem(sys.modules, 'tools.wd_mind_runtime_child', object())
    elif fault == 'foreign_submodule':
        monkeypatch.setitem(sys.modules, 'tools.foreign_injected', object())
    else:
        package = script.parent / '__init__.py'
        package.write_bytes(b'import tools.wd_mind_runtime_child\n')
        manifest_path = bundle / 'deployment-manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files']['tools-bootstrap/tools/__init__.py'] = hashlib.sha256(package.read_bytes()).hexdigest()
        raw = json.dumps(manifest, sort_keys=True).encode()
        manifest_path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(child.LaunchRefused):
        child._load_pinned_canonical_child(bundle, digest)


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_admission_binds_one_owner_and_factory_and_retains_worker(tmp_path):
    from tools import wd_mind_e5_lifecycle as lifecycle

    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    guard = child._reserve_local_owner(tmp_path)
    try:
        lease = child._LocalAdmission(guard)
        owner, factory = object(), object()
        lease.bind_owner(owner)
        lease.bind_factory(owner, factory)
        with pytest.raises(child.LaunchRefused):
            lease.bind_owner(object())
        with pytest.raises(child.LaunchRefused):
            lease.begin_worker(object(), factory)
        lease.begin_worker(owner, factory)
        assert not lease.quiescent()
        with pytest.raises(child.LaunchRefused):
            lease.begin_worker(owner, factory)
        worker = lifecycle._Owner(require_spawn_attempt=True)
        lease.capture_worker(owner, factory, worker)
        lease.finish_worker_start(owner, factory)
        assert not lease.quiescent()  # An owned worker is never evidence of cleanup.
    finally:
        guard.abandon()
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_admission_with_no_captured_worker_stays_unknown(tmp_path):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    guard = child._reserve_local_owner(tmp_path)
    try:
        lease = child._LocalAdmission(guard)
        owner, factory = object(), object()
        lease.bind_owner(owner)
        lease.bind_factory(owner, factory)
        lease.begin_worker(owner, factory)
        lease.finish_worker_start(owner, factory)
        assert not lease.quiescent()
        with pytest.raises(child.LaunchRefused):
            lease.begin_worker(owner, factory)
    finally:
        guard.abandon()


def test_preflight_refusal_before_resource_reservation_can_release(monkeypatch):
    """The launcher positively knows it never entered the owner/job phase."""
    guard = child._LocalOwnerReservation(Path('synthetic'), Path('marker'), 123, (), ())
    monkeypatch.setattr(guard, '_check', lambda: None)
    monkeypatch.setattr(guard, '_read', lambda: child._OWNER_RESERVATION_MAGIC + b'R')
    released = []
    monkeypatch.setattr(guard, 'release_clean', lambda **kw: released.append(kw))
    lease = child._LocalAdmission(guard, {'clean': True})
    owner, factory = object(), object()
    lease.bind_owner(owner)
    lease.bind_factory(owner, factory)
    lease.begin_worker(owner, factory)
    try:
        with pytest.raises(child.LaunchRefused):
            child._start_e5_worker(bundle_root='synthetic', deployment_manifest_sha256='a' * 64,
                interpreter='synthetic', interpreter_sha256='b' * 64, interpreter_root='synthetic',
                tuple_args={}, timeout=1, cooperative=True,
                _admission=lease, _admission_owner=owner, _admission_factory=factory)
    finally:
        lease.finish_worker_start(owner, factory)
    assert lease.quiescent()
    lease.release_clean()
    assert released == [dict(service_stopped=True, owner_stopped=True,
                             owner_owned=False, control_stopped=True)]


def test_no_resource_proof_after_worker_capture_never_releases(monkeypatch):
    from tools import wd_mind_e5_lifecycle as lifecycle
    guard = child._LocalOwnerReservation(Path('synthetic'), Path('marker'), 123, (), ())
    monkeypatch.setattr(guard, '_check', lambda: None)
    monkeypatch.setattr(guard, '_read', lambda: child._OWNER_RESERVATION_MAGIC + b'R')
    lease = child._LocalAdmission(guard, {'clean': True})
    owner, factory = object(), object()
    lease.bind_owner(owner)
    lease.bind_factory(owner, factory)
    lease.begin_worker(owner, factory)
    lease.capture_worker(owner, factory, lifecycle._Owner(require_spawn_attempt=True))
    with pytest.raises(child.LaunchRefused):
        lease.note_pre_resource_refusal(owner, factory)
    lease.finish_worker_start(owner, factory)
    assert not lease.quiescent()
    with pytest.raises(child.LaunchRefused):
        lease.release_clean()


def test_interrupted_preflight_stays_unknown(monkeypatch):
    guard = child._LocalOwnerReservation(Path('synthetic'), Path('marker'), 123, (), ())
    monkeypatch.setattr(guard, '_check', lambda: None)
    monkeypatch.setattr(guard, '_read', lambda: child._OWNER_RESERVATION_MAGIC + b'R')
    def interrupted(*args): raise KeyboardInterrupt()
    monkeypatch.setattr(child, '_require_safe_writer_scope', interrupted)
    lease = child._LocalAdmission(guard, {'clean': True})
    owner, factory = object(), object()
    lease.bind_owner(owner)
    lease.bind_factory(owner, factory)
    lease.begin_worker(owner, factory)
    try:
        with pytest.raises(KeyboardInterrupt):
            child._start_e5_worker(bundle_root='synthetic', deployment_manifest_sha256='a' * 64,
                interpreter='synthetic', interpreter_sha256='b' * 64, interpreter_root='synthetic',
                tuple_args=dict.fromkeys(child._PUBLIC_KEYS, 'synthetic'), timeout=1, cooperative=True,
                _admission=lease, _admission_owner=owner, _admission_factory=factory)
    finally:
        lease.finish_worker_start(owner, factory)
    assert not lease.quiescent()
    with pytest.raises(child.LaunchRefused):
        lease.release_clean()


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
@pytest.mark.parametrize('failure', ['pre_resource', 'pre_resource_no_proof', 'reserve_attempted'])
def test_rotating_candidate_owner_failure_releases_only_proven_no_resource(
        staged_graceful, tmp_path, monkeypatch, failure):
    """Real owner/adapter/rotator cleanup with synthetic source, no model/process."""
    import io
    import threading
    import time
    from tools import wd_mind_e5_lifecycle as lifecycle
    from tools import wd_mind_e5_rotating_client as rotation
    from tools import wd_mind_e5_snapshot_adapter as adapter_module
    from tools import wd_mind_full_runtime as runtime
    from test_wd_mind_e5_rotating_client import request

    root = tmp_path / 'synthetic-reservation'
    root.mkdir()
    marker = root / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(child, '_AUDIT_ROOT', root)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(runtime, '_prewarm_source_fits', lambda snapshot: None)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *args, **kwargs: b'synthetic-policy')
    monkeypatch.setattr(child._LocalManifestQuery, '_check_policy', lambda self: None)
    snapshot = {'projection_sha256': '2' * 64}
    fleet = json.dumps({'lanes':[{'agent':'codex-lead-1'}],
                        'tools_supervisor':{'agent':'codex-tools-1'}}).encode()
    bundle, manifest = staged_graceful['bundle_root'], staged_graceful['deployment_manifest_sha256']
    binding = runtime._PrivateBinding(snapshot, bundle, manifest, (fleet,), {})
    monkeypatch.setattr(runtime, 'assemble_full_runtime', lambda **kw: binding)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda self: snapshot)
    monkeypatch.setattr(runtime, '_flat_snapshot', lambda *args: snapshot)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_rotation_source_check', lambda self: '2' * 64)
    args = staged_graceful['tuple_args']
    location = {'runtime_root':args['runtime-root'], 'runtime_manifest':args['runtime-manifest'],
        'runtime_manifest_sha256':args['runtime-manifest-sha256'], 'runtime_generation':args['runtime-generation'],
        'runtime_revision':args['runtime-revision'], 'interpreter':str(staged_graceful['interpreter']),
        'interpreter_sha256':staged_graceful['interpreter_sha256'],
        'interpreter_root':str(staged_graceful['interpreter_root'])}
    monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kw: location)
    monkeypatch.setattr(runtime, '_worker_factory_preflight', lambda *args: child)
    monkeypatch.setattr(runtime, '_fixed_worker_audit_lock', lambda: Path(args['lock-file']))
    class Cache:
        def preflight(self): pass
    monkeypatch.setattr(runtime, '_load_pinned_vector_cache', lambda binding: Cache())
    monkeypatch.setattr(adapter_module, '_flat_bundle_bindings', lambda *args: (None, '1' * 64))
    monkeypatch.setattr(adapter_module, '_load_pinned_rotating_module', lambda *args: rotation)
    monkeypatch.setattr(adapter_module, 'create_rotating_flat_adapter', lambda **kw: object())
    observed, hold_reported = threading.Event(), threading.Event()
    def build(self):
        assert type(self._rotator) is rotation.RotatingGracefulE5Client
        return self._rotator.encode_supervised(request(1), call_deadline=time.monotonic() + 5)
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)
    if failure in ('pre_resource', 'pre_resource_no_proof'):
        def refuse_scope(*args): observed.set(); raise ValueError('synthetic pre-resource failure')
        monkeypatch.setattr(child, '_require_safe_writer_scope', refuse_scope)
        if failure == 'pre_resource_no_proof':
            monkeypatch.setattr(child._LocalAdmission, 'note_pre_resource_refusal', lambda *args: None)
    else:
        def refuse_reserve(**kwargs): observed.set(); raise ValueError('synthetic uncertain reserve attempt')
        monkeypatch.setattr(lifecycle, 'reserve', refuse_reserve)
    class Input:
        def read(self, size): hold_reported.wait(8); return b'S'
    class Output(io.BytesIO):
        def write(self, data):
            if b'"state":"HOLD"' in bytes(data): hold_reported.set()
            return super().write(data)
    output = Output()
    result = child._guarded_local_entry(child._local_candidate_owner_entry,
        control_stream=Input(), output_stream=output, bundle_root=str(bundle),
        expected_manifest_sha256=manifest, policy_sha256=hashlib.sha256(b'synthetic-policy').hexdigest(),
        approval_reference_sha256='c' * 64)
    assert observed.is_set(), 'synthetic failure injection was not reached'
    assert result == 2
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + (b'C' if failure == 'pre_resource' else b'R')


@pytest.mark.parametrize('service_fails', [False, True])
def test_owner_tolerates_busy_while_started_service_becomes_ready(monkeypatch, service_fails):
    import io
    import threading
    from tools import wd_mind_full_runtime as runtime
    from tools import wd_mind_runtime_process as process

    permit_service, running_observed, explicit_stop = (threading.Event() for _ in range(3))
    stop_observed, stop_timed_out, start_attempted, failure_injected = (threading.Event() for _ in range(4))
    closes = []
    class Adapter:
        _owner = object()
        calls = 0
        def start(self): return {'state': 'STARTING'}
        def status(self):
            self.calls += 1
            if self.calls == 2:
                permit_service.set()
                return {'state': 'BUSY'}
            return {'state': 'READY', 'owned': True}
        def close(self):
            closes.append('owner')
            return {'state': 'STOPPED', 'owned': False}
    class Service:
        def start(self):
            start_attempted.set()
            assert permit_service.wait(3)
            if service_fails:
                failure_injected.set()
                raise RuntimeError('synthetic service failure')
        def status(self):
            self.calls = getattr(self, 'calls', 0) + 1
            if self.calls == 2:
                # First RUNNING status was already consumed by the owner loop.
                running_observed.set()
                explicit_stop.set()
            return {'state': 'RUNNING'}
        def close(self, timeout):
            closes.append('service')
            return {'state': 'STOPPED'}
    class Control:
        def read(self, size):
            if not explicit_stop.wait(5):
                stop_timed_out.set()
                raise TimeoutError('synthetic STOP not signalled')
            stop_observed.set()
            return b'S'
    class Output(io.BytesIO):
        def write(self, value):
            if service_fails and b'"state":"HOLD"' in bytes(value):
                # Trigger explicit synthetic STOP after service failure is reported.
                explicit_stop.set()
            return super().write(value)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', True)
    monkeypatch.setattr(child, '_read_local_policy_frame', lambda *a, **k: b'synthetic')
    monkeypatch.setattr(child, 'create_local_manifest_query', lambda **kw: Adapter())
    monkeypatch.setattr(process, 'local_candidate_pipe_name', lambda **kw: 'synthetic')
    monkeypatch.setattr(process, 'create_local_candidate_service', lambda **kw: Service())
    output = Output()
    cleanup = {}
    result = child._local_candidate_owner_entry(control_stream=Control(), output_stream=output,
        bundle_root='synthetic', expected_manifest_sha256='a' * 64, policy_sha256='b' * 64,
        approval_reference_sha256='c' * 64, _cleanup_evidence=cleanup)
    assert result == (2 if service_fails else 0)
    assert start_attempted.is_set() and permit_service.is_set()
    assert failure_injected.is_set() is service_fails
    assert running_observed.is_set() is (not service_fails)
    assert stop_observed.is_set() and not stop_timed_out.is_set()
    assert sorted(closes) == ['owner', 'service'] and cleanup.get('clean') is True
    statuses = [json.loads(line)['state'] for line in output.getvalue().splitlines()]
    assert statuses[0] == 'STARTING' and statuses[-1] == 'STOPPED' and 'READY' not in statuses
    if service_fails:
        assert 'HOLD' in statuses


@pytest.mark.parametrize('diagnostic_flag', ['--start-diagnostics', '--start-diagnostics-v2'])
def test_fixed_owner_canonical_import_failure_emits_bounded_diagnostic(monkeypatch, diagnostic_flag):
    import io
    class Output:
        buffer = io.BytesIO()
    out = Output()
    args = ['--mind-local-candidate-owner', '--policy-sha256', 'b' * 64,
            '--approval-reference-sha256', 'c' * 64, diagnostic_flag]
    with monkeypatch.context() as m:
        m.setattr(child, '__name__', '__main__')
        m.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'a' * 64)
        m.setattr(child.sys, 'stdout', out)
        def refused(*args): raise RuntimeError('synthetic-private-detail')
        m.setattr(child, '_load_pinned_canonical_child', refused)
        result = child._fixed_child_main(args)
    assert result == 2
    diagnostic, = map(json.loads, out.buffer.getvalue().splitlines())
    assert diagnostic == {'authority':'none', 'reason':'IMPORT',
                          'schema':'wd.mind-local-owner-preanchor-diagnostic.v1'}
    assert b'a' * 64 not in out.buffer.getvalue()
    assert b'synthetic-private-detail' not in out.buffer.getvalue()


def test_fixed_owner_missing_manifest_never_invents_pinned_status(monkeypatch):
    import io
    class Output:
        buffer = io.BytesIO()
    out = Output()
    args = ['--mind-local-candidate-owner', '--policy-sha256', 'b' * 64,
            '--approval-reference-sha256', 'c' * 64, '--start-diagnostics']
    with monkeypatch.context() as m:
        m.setattr(child, '__name__', '__main__')
        m.delenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', raising=False)
        m.setattr(child.sys, 'stdout', out)
        result = child._fixed_child_main(args)
    assert result == 2
    diagnostic, = map(json.loads, out.buffer.getvalue().splitlines())
    assert diagnostic == {'authority':'none', 'reason':'IMPORT',
                          'schema':'wd.mind-local-owner-preanchor-diagnostic.v1'}


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_guarded_entry_requires_cleanup_and_quiescent_admission(tmp_path, monkeypatch):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(child, '_AUDIT_ROOT', tmp_path)

    def entry(*, _cleanup_evidence, _admission):
        owner, factory = object(), object()
        _admission.bind_owner(owner)
        _admission.bind_factory(owner, factory)
        _admission.begin_worker(owner, factory)
        _cleanup_evidence['clean'] = True
        return 0

    assert child._guarded_local_entry(entry) == 2
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_guarded_entry_releases_only_after_separate_cleanup_evidence(tmp_path, monkeypatch):
    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    monkeypatch.setattr(child, '_AUDIT_ROOT', tmp_path)

    def entry(*, _cleanup_evidence, _admission):
        assert _admission.quiescent()
        _cleanup_evidence['clean'] = True
        return 0

    assert child._guarded_local_entry(entry) == 0
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'C'


def test_direct_local_owner_without_admission_refuses(tmp_path):
    policy = b'synthetic policy'
    with pytest.raises(child.LaunchRefused):
        child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
            expected_manifest_sha256='a' * 64, identifier_policy_bytes=policy,
            expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
            operator_approval_reference_sha256='b' * 64, lane='codex-lead-1')


def test_direct_worker_without_admission_refuses_before_io(monkeypatch):
    monkeypatch.setattr(child, '_require_safe_writer_scope', lambda *a: pytest.fail('unexpected I/O'))
    with pytest.raises(child.LaunchRefused):
        child._start_e5_worker(bundle_root='', deployment_manifest_sha256='', interpreter='',
            interpreter_sha256='', interpreter_root='', tuple_args={}, timeout=1, cooperative=True)


@pytest.mark.skipif(os.name != 'nt', reason='reservation uses Windows byte locks')
def test_nested_factory_passes_same_lease_and_rejects_second_live_worker(tmp_path, monkeypatch):
    from tools import wd_mind_e5_lifecycle as lifecycle
    from tools import wd_mind_full_runtime as runtime

    marker = tmp_path / child._OWNER_RESERVATION_NAME
    marker.write_bytes(child._OWNER_RESERVATION_MAGIC + b'C')
    guard = child._reserve_local_owner(tmp_path)
    try:
        lease = child._LocalAdmission(guard)
        policy = b'synthetic policy'
        owner = child.create_local_manifest_query(enabled=True, bundle_root=str(tmp_path),
            expected_manifest_sha256='a' * 64, identifier_policy_bytes=policy,
            expected_identifier_policy_sha256=hashlib.sha256(policy).hexdigest(),
            operator_approval_reference_sha256='b' * 64, lane='codex-lead-1', _admission=lease)
        current = {'runtime_root':'r', 'runtime_manifest':'m', 'runtime_manifest_sha256':'h',
                   'runtime_generation':'g', 'runtime_revision':'v', 'interpreter':'i',
                   'interpreter_sha256':'s', 'interpreter_root':'p'}
        factory = runtime._ManifestWorkerFactory('bundle', 'manifest', current, Path('lock'), 5, 0,
                                                 'bulk', lease, owner)
        lease.bind_factory(owner, factory)
        monkeypatch.setattr(runtime, 'resolve_manifest_runtime_location', lambda **kwargs: current)
        monkeypatch.setattr(runtime, '_worker_factory_preflight', lambda *args: child)
        worker = lifecycle._Owner(require_spawn_attempt=True)

        def launch(**kwargs):
            assert kwargs['_admission'] is lease and kwargs['_admission_owner'] is owner
            assert kwargs['_admission_factory'] is factory
            lease.assert_worker_start(owner, factory)
            lease.capture_worker(owner, factory, worker)
            return {'status':'HOLD', 'owner':worker}

        monkeypatch.setattr(child, 'start_e5_worker_graceful', launch)
        assert factory()['owner'] is worker
        assert not lease.quiescent()
        with pytest.raises(child.LaunchRefused):
            factory()
    finally:
        guard.abandon()
    assert marker.read_bytes() == child._OWNER_RESERVATION_MAGIC + b'R'

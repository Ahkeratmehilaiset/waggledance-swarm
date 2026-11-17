"""Synthetic canonical-loader checks; never load a live bundle."""

import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

import pytest

from tools import wd_mind_runtime_child as child


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

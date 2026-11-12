"""Synthetic isolated bootstrap; no installed runtime or live bridge."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sandbox.mind.step2 import runtime_builder


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def candidate(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    # CI may launch Python via a symlink. Pin and execute its resolved binary;
    # the bootstrap itself continues to reject symlinked path components.
    interpreter = str(Path(sys.executable).resolve(strict=True))
    bundle = tmp_path / 'bundle'
    code = bundle / 'tools-bootstrap'
    (code / 'tools').mkdir(parents=True)
    (code / 'sandbox' / 'mind' / 'step2').mkdir(parents=True)
    bootstrap = code / 'tools' / 'wd_q4_live_update_bootstrap.py'
    shutil.copyfile(repo / 'tools' / 'wd_q4_live_update_bootstrap.py', bootstrap)
    verifier = code / 'sandbox' / 'mind' / 'step2' / 'runtime_manifest.py'
    shutil.copyfile(repo / 'sandbox' / 'mind' / 'step2' / 'runtime_manifest.py', verifier)
    (code / 'tools' / '__init__.py').write_text('', encoding='ascii')
    update = code / 'tools' / 'wd_q4_live_update_once.py'
    update.write_text("def main(args):\n import synthetic_runtime_dependency\n print(synthetic_runtime_dependency.VALUE)\n if '--host-attempt' in args: print(args[args.index('--host-attempt')+1])\n return 0\n", encoding='ascii')
    recovery = code / 'tools' / 'wd_q4_live_update_recovery.py'
    recovery.write_text("def main(args):\n assert args[0]=='--recover' and '--attempt' in args\n print('fixed-recovery-loaded')\n if '--job-receipt-sha256' in args: print(args[args.index('--job-receipt-sha256')+1], args[args.index('--pointer-sha256')+1])\n return 0\n", encoding='ascii')
    runtime = tmp_path / 'runtime'
    (runtime / 'assets' / 'code').mkdir(parents=True)
    (runtime / 'site').mkdir()
    (runtime / 'model').mkdir()
    (runtime / 'site' / 'synthetic_runtime_dependency.py').write_text("VALUE='pinned-site-loaded'\n", encoding='ascii')
    (runtime / 'model' / 'synthetic.txt').write_text('model', encoding='ascii')
    (runtime / 'assets' / 'code' / 'synthetic.txt').write_text('code', encoding='ascii')
    runtime_manifest = tmp_path / 'runtime-manifest.json'
    built = runtime_builder.build_runtime_manifest(runtime, runtime_manifest, generation='a' * 40, revision='b' * 40)
    fixed = {'interpreter_sha256': sha(Path(interpreter).read_bytes()), 'runtime_root': str(runtime),
        'runtime_manifest_path': str(runtime_manifest), 'runtime_manifest_sha256': built['manifest_sha256'],
        'runtime_generation': 'a' * 40, 'runtime_revision': 'b' * 40, 'verifier_sha256': sha(verifier.read_bytes())}
    fixed_path = tmp_path / 'fixed.json'
    fixed_path.write_bytes(encode(fixed))
    baseline = {'interpreter': interpreter, 'interpreter_sha256': fixed['interpreter_sha256'],
        'runtime_manifest_path': str(runtime_manifest), 'runtime_manifest_sha256': fixed['runtime_manifest_sha256'],
        'fixed_plan_path': str(fixed_path), 'fixed_plan_sha256': sha(fixed_path.read_bytes())}
    base_path = tmp_path / 'descriptor.json'
    base_path.write_bytes(encode(baseline))
    files = {'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py': sha(bootstrap.read_bytes()),
        'tools-bootstrap/tools/__init__.py': sha((code / 'tools' / '__init__.py').read_bytes()),
        'tools-bootstrap/tools/wd_q4_live_update_once.py': sha(update.read_bytes()),
        'tools-bootstrap/tools/wd_q4_live_update_recovery.py': sha(recovery.read_bytes()),
        'tools-bootstrap/sandbox/mind/step2/runtime_manifest.py': sha(verifier.read_bytes())}
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_bytes(encode({'schema_version': 1, 'source_commit': 'c' * 40, 'files': files}))
    manifest_sha = sha(manifest.read_bytes())
    config = tmp_path / 'config.json'
    config.write_bytes(encode({'schema': 'wd.q4-live-update-once.v1', 'baseline_path': str(base_path),
        'baseline_sha256': sha(base_path.read_bytes()), 'manifest_sha256': manifest_sha}))
    return {'bootstrap': bootstrap, 'config': config, 'config_sha': sha(config.read_bytes()), 'interpreter': interpreter,
            'manifest_sha': manifest_sha, 'runtime': runtime, 'bundle': bundle}


def execute(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    return subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']),
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha']], capture_output=True, text=True, timeout=30, env=env)


def test_isolated_process_imports_only_verified_runtime_site(candidate):
    result = execute(candidate)
    assert result.returncode == 0 and result.stdout.strip() == 'pinned-site-loaded'


def test_isolated_bootstrap_forwards_exact_host_attempt(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']),
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha'], '--host-attempt', 'b' * 64],
        capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0 and result.stdout.splitlines() == ['pinned-site-loaded', 'b' * 64]


def test_explicit_manifest_pin_works_without_inherited_wd_environment(candidate):
    env = {key: value for key, value in os.environ.items() if not key.startswith('WD_')}
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']),
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha']], capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0 and result.stdout.strip() == 'pinned-site-loaded'


def test_mismatching_inherited_manifest_still_refuses(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH='0' * 64)
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']),
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha']], capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode != 0 and not result.stdout


def test_modified_site_refuses_before_update_import(candidate):
    (candidate['runtime'] / 'site' / 'synthetic_runtime_dependency.py').write_text("raise RuntimeError('must not import')", encoding='ascii')
    result = execute(candidate)
    assert result.returncode != 0 and not result.stdout


def test_modified_config_refuses_before_update_import(candidate):
    candidate['config'].write_bytes(candidate['config'].read_bytes() + b' ')
    result = execute(candidate)
    assert result.returncode != 0 and not result.stdout


def test_modified_bootstrap_refuses_before_update_import(candidate):
    candidate['bootstrap'].write_bytes(candidate['bootstrap'].read_bytes() + b'\n')
    result = execute(candidate)
    assert result.returncode != 0 and not result.stdout


def test_unlisted_package_file_refuses_before_update_import(candidate):
    (candidate['bundle'] / 'tools-bootstrap' / 'tools' / 'extra.py').write_text('pass', encoding='ascii')
    result = execute(candidate)
    assert result.returncode != 0 and not result.stdout and 'package_membership' in result.stderr


def test_refuses_mixed_namespace_without_emitting_answer(candidate):
    root = candidate['runtime']
    injected = root / 'site' / 'sandbox' / 'mind'
    injected.mkdir(parents=True)
    (injected / 'injected.py').write_text('VALUE=1', encoding='ascii')
    config = json.loads(candidate['config'].read_bytes())
    baseline_path = Path(config['baseline_path'])
    baseline = json.loads(baseline_path.read_bytes())
    fixed_path = Path(baseline['fixed_plan_path'])
    fixed = json.loads(fixed_path.read_bytes())
    manifest_path = Path(fixed['runtime_manifest_path'])
    manifest_path.unlink()
    rebuilt = runtime_builder.build_runtime_manifest(root, manifest_path, generation='a' * 40, revision='b' * 40)
    fixed['runtime_manifest_sha256'] = rebuilt['manifest_sha256']
    fixed_path.write_bytes(encode(fixed))
    baseline.update(runtime_manifest_sha256=rebuilt['manifest_sha256'], fixed_plan_sha256=sha(fixed_path.read_bytes()))
    baseline_path.write_bytes(encode(baseline))
    config['baseline_sha256'] = sha(baseline_path.read_bytes())
    update = candidate['bundle'] / 'tools-bootstrap' / 'tools' / 'wd_q4_live_update_once.py'
    update.write_text("def main(args):\n print('must-not-leak')\n import sandbox.mind.injected\n return 0\n", encoding='ascii')
    manifest = candidate['bundle'] / 'deployment-manifest.json'
    manifest_doc = json.loads(manifest.read_bytes())
    manifest_doc['files']['tools-bootstrap/tools/wd_q4_live_update_once.py'] = sha(update.read_bytes())
    manifest.write_bytes(encode(manifest_doc))
    candidate['manifest_sha'] = sha(manifest.read_bytes())
    config['manifest_sha256'] = candidate['manifest_sha']
    candidate['config'].write_bytes(encode(config))
    candidate['config_sha'] = sha(candidate['config'].read_bytes())
    result = execute(candidate)
    assert result.returncode != 0 and not result.stdout


def test_interpreter_pin_mismatch_refuses(candidate):
    config = json.loads(candidate['config'].read_bytes())
    baseline_path = Path(config['baseline_path'])
    baseline = json.loads(baseline_path.read_bytes())
    baseline['interpreter_sha256'] = '0' * 64
    baseline_path.write_bytes(encode(baseline))
    config['baseline_sha256'] = sha(baseline_path.read_bytes())
    candidate['config'].write_bytes(encode(config))
    candidate['config_sha'] = sha(candidate['config'].read_bytes())
    result = execute(candidate)
    assert result.returncode != 0 and not result.stdout


@pytest.mark.skipif(os.name == 'nt', reason='POSIX interpreter symlink regression')
def test_symlinked_interpreter_path_is_still_refused(tmp_path):
    from tools import wd_q4_live_update_bootstrap as bootstrap

    link = tmp_path / 'synthetic-python-link'
    target = Path(sys.executable).resolve(strict=True)
    bootstrap._checked_path(str(target))
    link.symlink_to(target)
    assert link.is_symlink()
    with pytest.raises(ValueError):
        bootstrap._checked_path(str(link))


def test_no_arbitrary_module_or_command_argument(candidate):
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']), '--module', 'os'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and not result.stdout


def test_fixed_recovery_route_runs_only_with_pinned_attempt_and_progress(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']), '--recover',
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha'], '--attempt', 'a' * 64, '--progress-sha256', 'b' * 64],
        capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0 and result.stdout.strip() == 'fixed-recovery-loaded'


def test_fixed_recovery_route_accepts_pinned_runner_receipt_and_pointer(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']), '--recover',
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha'], '--attempt', 'a' * 64,
        '--runner-receipt-sha256', 'b' * 64, '--pointer-sha256', 'c' * 64],
        capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0 and result.stdout.strip() == 'fixed-recovery-loaded'


def test_fixed_recovery_route_forwards_pinned_job_receipt_and_pointer(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']), '--recover',
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha'], '--attempt', 'a' * 64,
        '--job-receipt-sha256', 'b' * 64, '--pointer-sha256', 'c' * 64],
        capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0 and result.stdout.splitlines() == ['fixed-recovery-loaded', 'b' * 64 + ' ' + 'c' * 64]


@pytest.mark.parametrize('evidence', [
    ['--job-receipt-sha256', '', '--pointer-sha256', 'c' * 64],
    ['--job-receipt-sha256', 'b' * 64],
    ['--progress-sha256', 'b' * 64, '--job-receipt-sha256', 'c' * 64, '--pointer-sha256', 'd' * 64],
    ['--job-receipt-sha256', 'b' * 64, '--pointer-sha256', 'c' * 64, '--module', 'os'],
])
def test_bootstrap_refuses_invalid_or_mixed_job_evidence(candidate, evidence):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']), '--recover',
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha'], '--attempt', 'a' * 64, *evidence],
        capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode != 0 and not result.stdout


def test_recovery_route_rejects_extra_command(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']), '--recover',
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha'], '--attempt', 'a' * 64, '--progress-sha256', 'b' * 64,
        '--module', 'os'], capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode != 0 and not result.stdout


@pytest.mark.skipif(not os.environ.get('WD_Q4_TEST_RUNTIME_ROOT'), reason='optional explicitly pinned audit runtime')
def test_explicit_audit_runtime_loads_actual_pinned_dependencies(candidate):
    """Optional component-only check: no real source, producer or model inference."""
    root = Path(os.environ['WD_Q4_TEST_RUNTIME_ROOT'])
    runtime_manifest = Path(os.environ['WD_Q4_TEST_RUNTIME_MANIFEST'])
    expected = os.environ['WD_Q4_TEST_RUNTIME_MANIFEST_SHA256']
    data = runtime_manifest.read_bytes()
    assert sha(data) == expected
    manifest_doc = json.loads(data)
    config_path = candidate['config']
    config = json.loads(config_path.read_bytes())
    baseline_path = Path(config['baseline_path'])
    baseline = json.loads(baseline_path.read_bytes())
    fixed_path = Path(baseline['fixed_plan_path'])
    fixed = json.loads(fixed_path.read_bytes())
    fixed.update(runtime_root=str(root), runtime_manifest_path=str(runtime_manifest), runtime_manifest_sha256=expected,
                 runtime_generation=manifest_doc['generation'], runtime_revision=manifest_doc['revision'])
    fixed_path.write_bytes(encode(fixed))
    baseline.update(runtime_manifest_path=str(runtime_manifest), runtime_manifest_sha256=expected,
                    fixed_plan_sha256=sha(fixed_path.read_bytes()))
    baseline_path.write_bytes(encode(baseline))
    config['baseline_sha256'] = sha(baseline_path.read_bytes())
    bundle = candidate['bundle']
    update_path = bundle / 'tools-bootstrap' / 'tools' / 'wd_q4_live_update_once.py'
    update_path.write_text("def main(args):\n import os\n os.environ['HF_HUB_OFFLINE']='1'\n os.environ['TRANSFORMERS_OFFLINE']='1'\n import torch, numpy, faiss, sentence_transformers\n print('pinned-dependencies-loaded')\n return 0\n", encoding='ascii')
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/wd_q4_live_update_once.py'] = sha(update_path.read_bytes())
    manifest_path.write_bytes(encode(manifest))
    candidate['manifest_sha'] = sha(manifest_path.read_bytes())
    config['manifest_sha256'] = candidate['manifest_sha']
    config_path.write_bytes(encode(config))
    candidate['config_sha'] = sha(config_path.read_bytes())
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    result = subprocess.run([candidate['interpreter'], '-I', '-S', '-B', str(candidate['bootstrap']),
        '--config', str(config_path), '--sha256', candidate['config_sha'], '--manifest-sha256', candidate['manifest_sha']],
        capture_output=True, text=True, timeout=600, env=env)
    assert result.returncode == 0 and result.stdout.strip() == 'pinned-dependencies-loaded', result.stderr


def _p1_repin(candidate,change=None):
    p=candidate['config'];doc=json.loads(p.read_bytes())
    for key in ('route_path','plan_path','anchor_path','policy_path','public_policy_path'):
        doc[key]=str(p.parent/(key+'.json'))
    doc.update(schema='wd.q4-live-update-once.v2',p1_enabled=True,route_sha256='a'*64)
    if change:doc.update(change)
    p.write_bytes(encode(doc));candidate['config_sha']=sha(p.read_bytes())
    return doc

def _p1_invoke(candidate,mode):
    args=['--config',str(candidate['config']),'--sha256',candidate['config_sha'],
          '--manifest-sha256',candidate['manifest_sha']]
    if mode!='update':
        args=['--recover',*args,'--attempt','a'*64]
        if mode=='progress':args+=['--progress-sha256','b'*64]
        else:args+=['--'+mode+'-receipt-sha256','b'*64,'--pointer-sha256','c'*64]
    return subprocess.run([candidate['interpreter'],'-I','-S','-B',str(candidate['bootstrap']),*args],
        env=dict(os.environ,WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha']),
        capture_output=True,text=True,timeout=30)

def test_legacy_bootstrap_control(candidate):
    result=_p1_invoke(candidate,'update')
    assert result.returncode==0 and 'pinned-site-loaded' in result.stdout

@pytest.mark.parametrize('mode',['update','progress','runner','job'])
def test_v2_bootstrap_exact_opt_in_reaches_only_pinned_module(candidate,mode):
    _p1_repin(candidate)
    result=_p1_invoke(candidate,mode)
    assert result.returncode==0,result.stderr
    assert ('pinned-site-loaded' if mode=='update' else 'fixed-recovery-loaded') in result.stdout

@pytest.mark.parametrize('flag',[False,0,1,'true',None,[]])
@pytest.mark.parametrize('mode',['update','progress'])
def test_v2_bootstrap_refuses_nonliteral_opt_in(candidate,flag,mode):
    _p1_repin(candidate,{'p1_enabled':flag})
    result=_p1_invoke(candidate,mode)
    assert result.returncode!=0 and not result.stdout

@pytest.mark.parametrize('form',['missing','extra','downgrade','duplicate'])
def test_v2_bootstrap_refuses_ambiguous_policy(candidate,form):
    doc=_p1_repin(candidate)
    if form=='missing':doc.pop('p1_enabled')
    if form=='extra':doc['unexpected']='no'
    if form=='downgrade':doc['schema']='wd.q4-live-update-once.v1'
    raw=encode(doc)
    if form=='duplicate':raw=raw[:-1]+b',"p1_enabled":true}'
    candidate['config'].write_bytes(raw);candidate['config_sha']=sha(raw)
    result=_p1_invoke(candidate,'update')
    assert result.returncode!=0 and not result.stdout

def test_v2_bootstrap_keeps_external_config_hash(candidate):
    _p1_repin(candidate);candidate['config'].write_bytes(candidate['config'].read_bytes()+b' ')
    result=_p1_invoke(candidate,'update')
    assert result.returncode!=0 and not result.stdout


@pytest.fixture
def bounded_read_probe(tmp_path, monkeypatch):
    import importlib
    import stat
    from types import SimpleNamespace

    helper = importlib.import_module('tools.wd_q4_live_update_bootstrap')
    path = tmp_path / 'bounded-read.bin'
    state = SimpleNamespace(requests=[], before_size=None, identity_fault=None, after_read=None,
                            path_fault=None, on_read=None, fstat_calls=0, stat_calls=0, fd=None)
    original_open, original_stat, original_lstat = Path.open, Path.stat, Path.lstat
    original_fstat = os.fstat

    def changed(info, **updates):
        values = {name: getattr(info, name) for name in
                  ('st_mode', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns')}
        values['st_file_attributes'] = getattr(info, 'st_file_attributes', 0)
        values.update(updates)
        return SimpleNamespace(**values)

    class RecordingFile:
        def __init__(self, stream):
            self.stream = stream
            state.fd = stream.fileno()

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            state.requests.append(size)
            if state.on_read is not None:
                state.on_read()
            value = self.stream.read(size)
            if state.after_read is not None:
                state.after_read()
            return value

    def opened(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        if self == path and args == ('rb',):
            return RecordingFile(stream)
        return stream

    def stated(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        if self == path and kwargs.get('follow_symlinks', True):
            state.stat_calls += 1
            if state.stat_calls == 2 and state.before_size is not None:
                return changed(info, st_size=state.before_size)
            if state.stat_calls == 3 and state.identity_fault == 'path_after':
                return changed(info, st_ino=info.st_ino ^ 1)
        return info

    def lstated(self, *args, **kwargs):
        info = original_lstat(self, *args, **kwargs)
        if self == path and state.path_fault == 'file_symlink':
            return changed(info, st_mode=stat.S_IFLNK | 0o777)
        if self == path.parent and state.path_fault == 'ancestor_reparse':
            return changed(info, st_file_attributes=0x400)
        return info

    def fstated(fd):
        info = original_fstat(fd)
        if fd == state.fd:
            state.fstat_calls += 1
            if ((state.identity_fault == 'opened' and state.fstat_calls == 1) or
                    (state.identity_fault == 'after' and state.fstat_calls == 2)):
                return changed(info, st_ino=info.st_ino ^ 1)
        return info

    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(Path, 'stat', stated)
    monkeypatch.setattr(Path, 'lstat', lstated)
    monkeypatch.setattr(os, 'fstat', fstated)
    return helper, path, state


@pytest.mark.parametrize('data,limit', [
    (b'', 64), (b'x', 64), (b'abc', 64), (b'x' * 63, 64),
    (b'x' * 64, 64), (b'abc', 65536), (b'abc', 1024 * 1024 * 1024),
])
def test_bounded_read_requests_only_observed_size_plus_sentinel(bounded_read_probe, data, limit):
    helper, path, state = bounded_read_probe
    path.write_bytes(data)
    assert helper._read(path, sha(data), limit) == data
    assert state.requests == [min(len(data), limit) + 1]
    assert state.fstat_calls == 2


@pytest.mark.parametrize('fault', [
    'grow', 'grow_one', 'grow_over_limit', 'shrink', 'same_size_changed', 'wrong_digest',
    'opened_identity', 'after_identity', 'path_after_identity',
    'before_zero_size', 'before_negative_size',
])
def test_bounded_read_retains_changed_file_refusals(bounded_read_probe, fault):
    helper, path, state = bounded_read_probe
    data = b'abc'
    path.write_bytes(data)
    pin = sha(data)
    expected_size = 4
    if fault in ('grow', 'grow_one', 'grow_over_limit', 'shrink', 'same_size_changed'):
        replacement = {'grow': b'abcdef', 'grow_one': b'abcd', 'grow_over_limit': b'x' * 100,
                       'shrink': b'a', 'same_size_changed': b'xyz'}[fault]
        state.on_read = lambda: path.write_bytes(replacement)
    elif fault == 'wrong_digest':
        pin = '0' * 64
    elif fault.endswith('_identity'):
        state.identity_fault = fault.removesuffix('_identity')
    elif fault == 'before_zero_size':
        state.before_size = 0
        expected_size = 1
    elif fault == 'before_negative_size':
        state.before_size = -1
        expected_size = 1
    with pytest.raises(ValueError):
        helper._read(path, pin, 64)
    assert state.requests == [expected_size]


def test_bounded_read_rejects_mtime_change_during_read(bounded_read_probe):
    helper, path, state = bounded_read_probe
    data = b'abc'
    path.write_bytes(data)
    original = path.stat()
    state.stat_calls = 0
    state.on_read = lambda: os.utime(path, ns=(original.st_mtime_ns + 1000000,
                                             original.st_mtime_ns + 1000000))
    with pytest.raises(ValueError):
        helper._read(path, sha(data), 64)
    assert state.requests == [4]
    assert path.read_bytes() == data


@pytest.mark.parametrize('fault', ['over_limit', 'negative_limit', 'directory',
                                 'relative', 'traversal', 'file_symlink', 'ancestor_reparse'])
def test_bounded_read_retains_preopen_refusals(bounded_read_probe, fault):
    helper, path, state = bounded_read_probe
    data, limit = b'abc', 64
    target = path
    if fault == 'over_limit':
        data = b'x' * 65
    if fault == 'negative_limit':
        limit = -1
    if fault == 'directory':
        target = path.parent
    if fault == 'relative':
        target = Path('relative-bounded-read.bin')
    if fault == 'traversal':
        target = path.parent / '..' / path.parent.name / path.name
    if fault in ('file_symlink', 'ancestor_reparse'):
        state.path_fault = fault
    path.write_bytes(data)
    with pytest.raises(ValueError):
        helper._read(target, sha(data), limit)
    assert not state.requests


@pytest.mark.parametrize('pin', [None, '', 'g' * 64, 'A' * 64, '0' * 63, '0' * 65])
def test_bounded_read_retains_hash_syntax_refusal(bounded_read_probe, pin):
    helper, path, state = bounded_read_probe
    path.write_bytes(b'abc')
    with pytest.raises(ValueError):
        helper._read(path, pin, 64)
    assert not state.requests


def test_bounded_read_rejects_postread_growth(bounded_read_probe):
    helper, path, state = bounded_read_probe
    path.write_bytes(b'abc')
    def append_after_read():
        state.after_read = None
        with path.open('ab') as stream:
            stream.write(b'd')
    state.after_read = append_after_read
    with pytest.raises(ValueError):
        helper._read(path, sha(b'abc'), 64)
    assert state.requests == [4]
    with path.open('r', encoding='ascii') as stream:
        assert stream.read() == 'abcd'

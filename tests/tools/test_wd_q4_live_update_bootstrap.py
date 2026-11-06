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
    update.write_text("def main(args):\n import synthetic_runtime_dependency\n print(synthetic_runtime_dependency.VALUE)\n return 0\n", encoding='ascii')
    runtime = tmp_path / 'runtime'
    (runtime / 'assets' / 'code').mkdir(parents=True)
    (runtime / 'site').mkdir()
    (runtime / 'model').mkdir()
    (runtime / 'site' / 'synthetic_runtime_dependency.py').write_text("VALUE='pinned-site-loaded'\n", encoding='ascii')
    (runtime / 'model' / 'synthetic.txt').write_text('model', encoding='ascii')
    (runtime / 'assets' / 'code' / 'synthetic.txt').write_text('code', encoding='ascii')
    runtime_manifest = tmp_path / 'runtime-manifest.json'
    built = runtime_builder.build_runtime_manifest(runtime, runtime_manifest, generation='a' * 40, revision='b' * 40)
    fixed = {'interpreter_sha256': sha(Path(sys.executable).read_bytes()), 'runtime_root': str(runtime),
        'runtime_manifest_path': str(runtime_manifest), 'runtime_manifest_sha256': built['manifest_sha256'],
        'runtime_generation': 'a' * 40, 'runtime_revision': 'b' * 40, 'verifier_sha256': sha(verifier.read_bytes())}
    fixed_path = tmp_path / 'fixed.json'
    fixed_path.write_bytes(encode(fixed))
    baseline = {'interpreter': sys.executable, 'interpreter_sha256': fixed['interpreter_sha256'],
        'runtime_manifest_path': str(runtime_manifest), 'runtime_manifest_sha256': fixed['runtime_manifest_sha256'],
        'fixed_plan_path': str(fixed_path), 'fixed_plan_sha256': sha(fixed_path.read_bytes())}
    base_path = tmp_path / 'descriptor.json'
    base_path.write_bytes(encode(baseline))
    files = {'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py': sha(bootstrap.read_bytes()),
        'tools-bootstrap/tools/__init__.py': sha((code / 'tools' / '__init__.py').read_bytes()),
        'tools-bootstrap/tools/wd_q4_live_update_once.py': sha(update.read_bytes()),
        'tools-bootstrap/sandbox/mind/step2/runtime_manifest.py': sha(verifier.read_bytes())}
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_bytes(encode({'schema_version': 1, 'source_commit': 'c' * 40, 'files': files}))
    manifest_sha = sha(manifest.read_bytes())
    config = tmp_path / 'config.json'
    config.write_bytes(encode({'schema': 'wd.q4-live-update-once.v1', 'baseline_path': str(base_path),
        'baseline_sha256': sha(base_path.read_bytes()), 'manifest_sha256': manifest_sha}))
    return {'bootstrap': bootstrap, 'config': config, 'config_sha': sha(config.read_bytes()),
            'manifest_sha': manifest_sha, 'runtime': runtime, 'bundle': bundle}


def execute(candidate):
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'])
    return subprocess.run([sys.executable, '-I', '-S', '-B', str(candidate['bootstrap']),
        '--config', str(candidate['config']), '--sha256', candidate['config_sha'],
        '--manifest-sha256', candidate['manifest_sha']], capture_output=True, text=True, timeout=30, env=env)


def test_isolated_process_imports_only_verified_runtime_site(candidate):
    result = execute(candidate)
    assert result.returncode == 0 and result.stdout.strip() == 'pinned-site-loaded'


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


def test_no_arbitrary_module_or_command_argument(candidate):
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(candidate['bootstrap']), '--module', 'os'],
                            capture_output=True, text=True, timeout=30)
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
    update_path.write_text("def main(args):\n import torch, numpy, faiss, sentence_transformers\n print('pinned-dependencies-loaded')\n return 0\n", encoding='ascii')
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/wd_q4_live_update_once.py'] = sha(update_path.read_bytes())
    manifest_path.write_bytes(encode(manifest))
    candidate['manifest_sha'] = sha(manifest_path.read_bytes())
    config['manifest_sha256'] = candidate['manifest_sha']
    config_path.write_bytes(encode(config))
    candidate['config_sha'] = sha(config_path.read_bytes())
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=candidate['manifest_sha'], HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1')
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(candidate['bootstrap']),
        '--config', str(config_path), '--sha256', candidate['config_sha'], '--manifest-sha256', candidate['manifest_sha']],
        capture_output=True, text=True, timeout=600, env=env)
    assert result.returncode == 0 and result.stdout.strip() == 'pinned-dependencies-loaded', result.stderr

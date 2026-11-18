"""Synthetic-only tests of the independently pinned operator bootstrap."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / 'tools/wd_mind_admin_bootstrap.py'


def _operator_literal():
    tree = ast.parse(BOOTSTRAP.read_text(encoding='utf-8'))
    matches = [node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'OPERATOR_LITERAL' for t in node.targets)]
    assert len(matches) == 1
    return ast.literal_eval(matches[0])


def _fixture(tmp_path, mode):
    commit = 'a' * 40
    store = tmp_path / 'bundles'
    pointer = tmp_path / 'state.json'
    root = store / commit
    source = BOOTSTRAP.read_text(encoding='utf-8').replace(
        r'C:\Python\wd-reboot-bundles', str(store)).replace(r'C:\Python\WD_REBOOT_STATE_CURRENT.json', str(pointer))
    admin = b'from tools import dependency\ndef main(argv):\n print("SYNTHETIC_ADMIN", dependency.VALUE)\n return 2\n'
    if mode == 'unlisted_import':
        admin = b'from tools import unlisted\ndef main(argv):\n print("ATTACK_MAIN")\n return 0\n'
    if mode == 'unsupported_namespace':
        admin = b'import sandbox\ndef main(argv):\n print("ATTACK_MAIN")\n return 0\n'
    if mode == 'pointer_change_during_import':
        admin = (b'from pathlib import Path\nPath(' + repr(str(pointer)).encode() + b').write_text("{}")\n'
                 b'def main(argv):\n print("ATTACK_MAIN")\n return 0\n')
    sources = {'tools/__init__.py': b'', 'tools/wd_mind_admin_bootstrap.py': source.encode(),
               'tools/wd_mind_owner_reservation_admin.py': admin, 'tools/dependency.py': b'VALUE = 41\n'}
    if mode == 'real_disabled':
        definition = json.loads((ROOT / 'ops/windows/reboot/bridge-code-files.json').read_text())
        sources = {p: (ROOT / p).read_bytes() for p in definition['python_files']
                   if p.endswith('.py') and p.startswith(('tools/', 'waggledance/'))}
        sources['tools/wd_mind_admin_bootstrap.py'] = source.encode()
    pins = {}
    for relative, raw in sources.items():
        path = root / 'tools-bootstrap' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        pins['tools-bootstrap/' + relative] = hashlib.sha256(raw).hexdigest()
    if mode == 'unlisted_import':
        (root / 'tools-bootstrap/tools/unlisted.py').write_text('print("ATTACK_IMPORT")\n')
    if mode == 'missing_dependency_pin':
        pins.pop('tools-bootstrap/tools/dependency.py')
    entrypoints = {'bad': 'tools/wd_mind_owner_reservation_admin.py'} if mode == 'generic_entrypoint' else {}
    definition = json.dumps({'schema': 'wd.bridge-code-package.v1', 'package_relative_root': 'tools-bootstrap',
                             'python_files': list(sources), 'python_entrypoints': entrypoints}).encode()
    (root / 'bridge-code-files.json').write_bytes(definition)
    pins['bridge-code-files.json'] = hashlib.sha256(definition).hexdigest()
    manifest = json.dumps({'schema_version': 1, 'source_commit': commit, 'files': pins}).encode()
    if mode == 'duplicate_manifest':
        manifest = manifest.replace(b'"source_commit":', b'"source_commit":"' + commit.encode() + b'","source_commit":', 1)
    (root / 'deployment-manifest.json').write_bytes(manifest)
    pin = hashlib.sha256(manifest).hexdigest()
    state = {'source_commit': commit, 'final_commit': commit, 'active_bundle': str(root),
             'manifest_sha256': pin, 'final_manifest_sha256': pin}
    if mode == 'wrong_pointer_commit':
        state['final_commit'] = 'b' * 40
    if mode == 'wrong_pointer_bundle':
        state['active_bundle'] = str(store / ('b' * 40))
    if mode == 'wrong_pointer_pin':
        state['manifest_sha256'] = '0' * 64
    pointer_raw = json.dumps(state)
    if mode == 'duplicate_pointer':
        pointer_raw = pointer_raw[:-1] + ',"source_\\u0063ommit":"' + commit + '"}'
    pointer.write_text(pointer_raw)
    if mode == 'tampered_loader':
        (root / 'tools-bootstrap/tools/wd_mind_admin_bootstrap.py').write_text('print("ATTACK_LOADER")')
    if mode == 'tampered_dependency':
        (root / 'tools-bootstrap/tools/dependency.py').write_text('print("ATTACK_DEPENDENCY")')
    if mode == 'missing_loader_pin':
        pins.pop('tools-bootstrap/tools/wd_mind_admin_bootstrap.py')
        manifest = json.dumps({'schema_version': 1, 'source_commit': commit, 'files': pins}).encode()
        (root / 'deployment-manifest.json').write_bytes(manifest)
        pin = hashlib.sha256(manifest).hexdigest()
        state.update(manifest_sha256=pin, final_manifest_sha256=pin)
        pointer.write_text(json.dumps(state))
    literal = _operator_literal().replace(r'C:\Python\wd-reboot-bundles', str(store))
    return root, commit, pin, literal


@pytest.mark.parametrize('mode', ['valid', 'real_disabled', 'tampered_loader', 'tampered_dependency', 'missing_loader_pin',
                                  'missing_dependency_pin', 'duplicate_manifest', 'duplicate_pointer',
                                  'wrong_pointer_commit', 'wrong_pointer_bundle', 'wrong_pointer_pin',
                                  'unlisted_import', 'unsupported_namespace', 'generic_entrypoint', 'pointer_change_during_import'])
def test_reviewed_literal_pins_loader_pointer_and_package(tmp_path, mode):
    root, commit, pin, literal = _fixture(tmp_path, mode)
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', literal, commit, pin, 'reset'],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=25, check=False)
    assert result.returncode == 2
    assert 'ATTACK_' not in result.stdout + result.stderr
    if mode == 'valid':
        assert result.stdout.strip() == 'SYNTHETIC_ADMIN 41'
        assert not result.stderr
    elif mode == 'real_disabled':
        assert not result.stdout
        assert result.stderr.strip() == 'owner_marker_production_route_unavailable'
    else:
        assert not result.stdout
        assert result.stderr.strip() == 'ADMIN_BOOTSTRAP_REFUSED'


def test_direct_filename_execution_refuses(tmp_path):
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(BOOTSTRAP)], cwd=str(tmp_path),
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 2
    assert result.stderr.strip() == 'owner_marker_bootstrap_requires_verified_literal'


def test_wrong_external_manifest_or_missing_isolation_refuses(tmp_path):
    root, commit, pin, literal = _fixture(tmp_path, 'valid')
    for options, tested_pin in ((['-I', '-S', '-B'], '0' * 64), (['-S', '-B'], pin)):
        result = subprocess.run([sys.executable, *options, '-c', literal, commit, tested_pin, 'create'],
                                cwd=str(tmp_path), capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == 2
        assert not result.stdout
        assert result.stderr.strip() == 'ADMIN_BOOTSTRAP_REFUSED'

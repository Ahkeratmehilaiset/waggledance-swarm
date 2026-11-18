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
    admin = (b'from tools import dependency\nfrom pathlib import Path\nimport json\n'
             b'def main(argv):\n'
             b' assert len(argv) == 5 and argv[0] in ("create", "reset")\n'
             b' assert argv[1] == "--source-commit" and argv[2] == "a" * 40\n'
             b' assert argv[3] == "--manifest-sha256" and len(argv[4]) == 64\n'
             b' assert all(c in "0123456789abcdef" for c in argv[4])\n'
             b' assert argv[4] == json.loads(Path(' + repr(str(pointer)).encode() + b').read_text())["manifest_sha256"]\n'
             b' print("SYNTHETIC_ADMIN", dependency.VALUE, argv[0])\n'
             b' return 2\n')
    if mode == 'unlisted_import':
        admin = b'from tools import unlisted\ndef main(argv):\n print("ATTACK_MAIN")\n return 0\n'
    if mode == 'unsupported_namespace':
        admin = b'import sandbox\ndef main(argv):\n print("ATTACK_MAIN")\n return 0\n'
    if mode == 'pointer_change_during_import':
        admin = (b'from pathlib import Path\nPath(' + repr(str(pointer)).encode() + b').write_text("{}")\n'
                 b'def main(argv):\n print("ATTACK_MAIN")\n return 0\n')
    sources = {'tools/__init__.py': b'', 'tools/wd_mind_admin_bootstrap.py': source.encode(),
               'tools/wd_mind_owner_reservation_admin.py': admin, 'tools/dependency.py': b'VALUE = 41\n'}
    if mode in ('real_disabled', 'real_disabled_current_cli', 'real_validate_loader', 'real_exact_e2e'):
        definition = json.loads((ROOT / 'ops/windows/reboot/bridge-code-files.json').read_text())
        sources = {p: (ROOT / p).read_bytes() for p in definition['python_files']
                   if p.endswith('.py') and p.startswith(('tools/', 'waggledance/'))}
        if mode == 'real_disabled_current_cli':
            # Synthetic legacy-argument probe only: the permanent operator
            # interface must refuse a caller-provided bundle root.
            source = source.replace("[operation, '--source-commit', commit, '--manifest-sha256', pin]",
                                    "[operation, '--bundle-root', str(root), '--manifest-sha256', pin]")
        if mode == 'real_validate_loader':
            # Probe the real loader validation under the synthetic bundle only;
            # do not enter production main or replace its disabled gate.
            admin_source = sources['tools/wd_mind_owner_reservation_admin.py']
            prefix, sep, _ = admin_source.partition(b'def main(argv=None):')
            assert sep
            sources['tools/wd_mind_owner_reservation_admin.py'] = (prefix +
                b'def main(argv=None):\n'
                b' _validate_bootstrap_loader(Path(__file__).parents[2])\n'
                b' print("SYNTHETIC_REAL_LOADER_VALIDATED")\n'
                b' return 2\n')
        sources['tools/wd_mind_admin_bootstrap.py'] = (BOOTSTRAP.read_bytes()
            if mode == 'real_exact_e2e' else source.encode())
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
        assert result.stdout.strip() == 'SYNTHETIC_ADMIN 41 reset'
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


@pytest.mark.parametrize('operation', ['create', 'reset'])
def test_literal_passes_exact_pins_and_operation_without_root_or_observer(tmp_path, operation):
    root, commit, pin, literal = _fixture(tmp_path, 'valid')
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', literal, commit, pin, operation],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=25, check=False)
    assert result.returncode == 2
    assert result.stdout.strip() == f'SYNTHETIC_ADMIN 41 {operation}'
    assert not result.stderr


@pytest.mark.parametrize('operation', ['create', 'reset'])
@pytest.mark.parametrize(('mode', 'expected'), [
    ('real_disabled', 'owner_marker_production_route_unavailable'),
    ('real_disabled_current_cli', 'owner_marker_invalid_input'),
])
def test_real_admin_refuses_operator_and_legacy_routes(tmp_path, operation, mode, expected):
    root, commit, pin, literal = _fixture(tmp_path, mode)
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', literal, commit, pin, operation],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=25, check=False)
    assert result.returncode == 2
    assert not result.stdout
    assert result.stderr.strip() == expected


def test_real_loader_validation_accepts_captured_synthetic_admin(tmp_path):
    root, commit, pin, literal = _fixture(tmp_path, 'real_validate_loader')
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', literal, commit, pin, 'reset'],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=25, check=False)
    assert result.returncode == 2
    assert result.stdout.strip() == 'SYNTHETIC_REAL_LOADER_VALIDATED'
    assert not result.stderr


@pytest.mark.parametrize('when', ['before', 'after'])
def test_finder_insert_interruption_removes_registered_finder(tmp_path, when):
    root, commit, pin, _ = _fixture(tmp_path, 'valid')
    script = r'''
import pathlib, sys
source = pathlib.Path(sys.argv[1])
namespace = {'__name__': 'wd_mind_admin_verified_bootstrap', '__file__': str(source), '__package__': None}
exec(compile(source.read_bytes(), str(source), 'exec', dont_inherit=True), namespace)
original = list(sys.meta_path)
class InterruptAfterInsert(list):
    def insert(self, index, item):
        if isinstance(item, namespace['_PinnedFinder']) and sys.argv[4] == 'before':
            raise KeyboardInterrupt('synthetic interruption before registration')
        super().insert(index, item)
        if isinstance(item, namespace['_PinnedFinder']):
            raise KeyboardInterrupt('synthetic interruption after registration')
sys.meta_path = InterruptAfterInsert(original)
try:
    namespace['_run'](sys.argv[2], sys.argv[3], 'reset')
except KeyboardInterrupt:
    pass
else:
    raise AssertionError('interruption did not propagate')
assert len(sys.meta_path) == len(original)
assert all(actual is expected for actual, expected in zip(sys.meta_path, original))
print('SYNTHETIC_FINDER_CLEAN')
'''
    source = root / 'tools-bootstrap/tools/wd_mind_admin_bootstrap.py'
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(source), commit, pin, when],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=25, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'SYNTHETIC_FINDER_CLEAN'


@pytest.mark.parametrize('scenario', ['create', 'reset', 'damaged', 'source_changed',
                                      'pointer_changed', 'binding_changed', 'binding_reset',
                                      'binding_damaged', 'uncertain'])
def test_exact_admin_end_to_end_with_only_synthetic_injections(tmp_path, scenario):
    """Exact loader/admin bytes; synthetic process overrides all live bindings."""
    root, commit, pin, _ = _fixture(tmp_path, 'real_exact_e2e')
    loader = root / 'tools-bootstrap/tools/wd_mind_admin_bootstrap.py'
    admin_source = root / 'tools-bootstrap/tools/wd_mind_owner_reservation_admin.py'
    assert loader.read_bytes() == BOOTSTRAP.read_bytes()
    assert admin_source.read_bytes() == (ROOT / 'tools/wd_mind_owner_reservation_admin.py').read_bytes()
    script = r'''
import pathlib, sys, time
loader, store, pointer, fake_root, commit, pin, scenario = sys.argv[1:]
loader, store, pointer, fake_root = map(pathlib.Path, (loader, store, pointer, fake_root))
fake_root.mkdir()
interpreter_root = fake_root.parent / 'synthetic-interpreter'
interpreter_root.mkdir()
interpreter = interpreter_root / 'python.exe'
interpreter.write_bytes(b'synthetic interpreter')
ns = {'__name__': 'wd_mind_admin_verified_bootstrap', '__file__': str(loader), '__package__': None}
exec(compile(loader.read_bytes(), str(loader), 'exec', dont_inherit=True), ns)
ns['_BUNDLE_STORE'], ns['_CURRENT_POINTER'] = store, pointer
original_exec = ns['_PinnedFinder'].exec_module
holder = {}
def inject(finder, module):
    original_exec(finder, module)
    if module.__name__ != 'tools.wd_mind_owner_reservation_admin': return
    admin = holder['admin'] = module
    assert admin._PRODUCTION_ENTRY_ENABLED is False
    # Every overridden location is inside this synthetic test root. Never use
    # an installed pointer, live audit root, or real process observer.
    admin._BUNDLE_STORE, admin._CURRENT_POINTER = store, pointer
    admin._FIXED_PRODUCTION_ROOT = fake_root
    admin.child._AUDIT_ROOT = fake_root
    from tools import wd_mind_full_runtime as runtime
    runtime.resolve_manifest_runtime_location = lambda **kwargs: {
        'interpreter_status': 'VERIFIED', 'interpreter_root': str(interpreter_root),
        'interpreter': str(interpreter)}
    admin._observe_windows = lambda: (time.time_ns() - 1_000_000_000, ())
    admin._PRODUCTION_ENTRY_ENABLED = True  # synthetic child process only
    if scenario == 'source_changed':
        pathlib.Path(module.__file__).write_bytes(b'changed synthetic admin')
    elif scenario == 'pointer_changed':
        pointer.write_text('{}', encoding='utf-8')
    elif scenario in ('binding_changed', 'binding_reset', 'binding_damaged'):
        method = {'binding_changed': '_atomic_reserved', 'binding_reset': '_release_verified',
                  'binding_damaged': '_repair_marker_locked'}[scenario]
        original = getattr(admin, method)
        def changed(*args, **kwargs):
            pointer.write_text('{}', encoding='utf-8')
            return original(*args, **kwargs)
        setattr(admin, method, changed)
    elif scenario == 'uncertain':
        original = admin._release_verified
        def failed_fsync(*args, **kwargs):
            def fail(fd): raise OSError('synthetic fsync fault')
            admin.os.fsync = fail
            return original(*args, **kwargs)
        admin._release_verified = failed_fsync
ns['_PinnedFinder'].exec_module = inject
marker = fake_root / 'wd-mind-owner.reservation'
# Derive the exact filename and bytes from captured source only, before the
# actual entry, by populating the synthetic root in the injected import hook.
original_inject = ns['_PinnedFinder'].exec_module
def setup(finder, module):
    original_inject(finder, module)
    if module.__name__ != 'tools.wd_mind_owner_reservation_admin': return
    global marker, initial
    marker = fake_root / module.child._OWNER_RESERVATION_NAME
    if scenario in ('reset', 'binding_reset', 'uncertain'):
        initial = module.child._OWNER_RESERVATION_MAGIC + b'R'
    elif scenario in ('damaged', 'binding_damaged'): initial = b'broken synthetic marker'
    else: initial = None
    if initial is not None:
        marker.write_bytes(initial)
        import os
        os.utime(marker, ns=(1_600_000_000_000_000_000,)*2)
ns['_PinnedFinder'].exec_module = setup
operation = 'reset' if scenario in ('reset', 'damaged', 'binding_reset', 'binding_damaged', 'uncertain') else 'create'
try:
    result = ns['_run'](commit, pin, operation)
except ns['BootstrapRefused']:
    result = 2
admin = holder['admin']
if scenario in ('create', 'reset', 'damaged'):
    assert result == 0
    assert marker.read_bytes() == admin.child._OWNER_RESERVATION_MAGIC + b'C'
elif scenario == 'uncertain':
    assert result == 2
    assert marker.read_bytes() == admin.child._OWNER_RESERVATION_MAGIC + b'C'
elif scenario in ('binding_reset', 'binding_damaged'):
    assert result == 2 and marker.read_bytes() == initial
else:
    assert result == 2 and not marker.exists()
print('SYNTHETIC_E2E_' + scenario.upper())
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(loader), str(root.parent),
                             str(tmp_path / 'state.json'), str(tmp_path / 'owner-root'), commit, pin, scenario],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert 'SYNTHETIC_E2E_' + scenario.upper() in result.stdout
    if scenario == 'uncertain':
        assert 'owner_marker_reset_publication_uncertain' in result.stderr


def test_wrong_external_manifest_or_missing_isolation_refuses(tmp_path):
    root, commit, pin, literal = _fixture(tmp_path, 'valid')
    for options, tested_pin in ((['-I', '-S', '-B'], '0' * 64), (['-S', '-B'], pin)):
        result = subprocess.run([sys.executable, *options, '-c', literal, commit, tested_pin, 'create'],
                                cwd=str(tmp_path), capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == 2
        assert not result.stdout
        assert result.stderr.strip() == 'ADMIN_BOOTSTRAP_REFUSED'

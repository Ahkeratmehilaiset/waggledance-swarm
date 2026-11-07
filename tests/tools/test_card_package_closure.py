"""Default-OFF package closure for the synthetic card modules (lead-mind-card-package-closure-fable-20261107-v1).

The card route of ``shared_result_resolver`` is OFF unless the caller passes a pinned, verified route decision
(lead-mind-card-source-binding-fable-20261107-v1), and the resolver and the resolved child import the card module only
inside that decision path. These tests pin the smallest packaging that lets a bundle carry the card modules as code
only: the import closure of ``shared_card_projection`` is in ``python_files``, no card module is an entrypoint, a
manual capability or an import-smoke module, and the staged package imports with the standard library only while the
route stays OFF. Packaging is not wiring and not activation; nothing here reads an installed
bundle, a policy or a runtime.
"""
import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('package_closure_fixture', ROOT / 'tests/tools/test_bridge_code_package_closure.py')
closure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(closure)

CARD_MODULE = 'sandbox/mind/step2/shared_card_projection.py'
CARD_PRODUCER = 'tools/wd_q4_card_generation.py'
CARD_CHECKPOINT = 'sandbox/mind/step2/shared_card_checkpoint.py'
CARD_FILES = (
    'sandbox/mind/step2/public_knowledge_card.py',
    CARD_MODULE,
    'tools/wd_q4_public_card_prefix_verifier.py',
)
ROUTE_FILES = ('sandbox/mind/step2/shared_result_resolver.py', 'tools/wd_q4_resolved_child.py')
# These roots are reached through fixed module names or pinned file paths,
# not necessarily through Python import statements in the launcher.
CALLER_DYNAMIC_BUNDLE_ROOTS = (
    'tools/wd_q4_lane_caller_runtime_bootstrap.py',
    'tools/wd_q4_lane_caller_cli.py',
    'tools/wd_q4_secure_transport.py',
    'tools/wd_q4_staged_fixed_backend.py',
    'sandbox/mind/step2/runtime_bootstrap.py',
    'sandbox/mind/step2/runtime_manifest.py',
    'sandbox/mind/step2/shared_index_checkpoint.py',
    'sandbox/mind/step2/wd_q4_private_read.py',
)
CALLER_RUNTIME_ONLY_HELPERS = (
    'sandbox/mind/step2/q4_controlled_imports.py',
    'sandbox/mind/step2/q4_namespace_origin.py',
    'sandbox/mind/step2/q4_native_additions.py',
)
STAGED_CARD_CLI_FILES = (
    'tools/wd_q4_staged_card_composer_cli.py',
    'tools/wd_q4_real_card_snapshot_cli.py',
)
CARD_PREPARE_FILES = ('tools/wd_q4_public_card_prepare.py', 'tools/wd_q4_real_card_decision_prepare.py',
                      'tools/wd_q4_public_card_publish.py', 'tools/wd_q4_public_card_publish_cli.py',
                      'tools/build_q4_runtime_plan.py', 'tools/wd_q4_staged_runtime_plan.py',
                      'tools/wd_q4_staged_fixed_plan_writer.py', 'tools/wd_q4_external_generation_anchor.py',
                      'tools/wd_q4_bridge_prefix_pin.py', 'tools/wd_q4_real_card_decision_cli.py',
                      'tools/wd_q4_lane_caller_runtime_bootstrap.py')
CARD_POLICY_FILES = tuple('configs/mind/' + name for name in (
    'shared_card_non_review_policy.json', 'shared_card_writer_policy.json',
    'shared_card_vocabulary.json', 'shared_card_paths.json', 'shared_card_public_status_policy.json'))


def _reach(seeds, monkeypatch):
    monkeypatch.setattr(closure, 'INTERNAL_ROOTS', (*closure.INTERNAL_ROOTS, 'core'))
    reached, unresolved = closure._closure(list(seeds))
    assert not unresolved, unresolved
    return set(reached)


def test_every_card_file_is_packaged_once():
    packaged = closure._packaged_python()
    assert [packaged.count(path) for path in CARD_FILES] == [1, 1, 1]


def test_staged_card_cli_files_and_import_closure_are_packaged(monkeypatch):
    packaged = closure._packaged_python()
    assert all(packaged.count(path) == 1 for path in STAGED_CARD_CLI_FILES)
    reached = _reach(STAGED_CARD_CLI_FILES, monkeypatch)
    assert reached <= set(packaged), sorted(reached - set(packaged))


def test_staged_card_cli_entrypoints_are_explicit():
    entries = closure._definition()['python_entrypoints']
    assert entries['q4_card_snapshot_prepare'] == STAGED_CARD_CLI_FILES[1]
    assert entries['q4_card_staged_composer'] == STAGED_CARD_CLI_FILES[0]
    assert entries['q4_card_publish'] == 'tools/wd_q4_public_card_publish_cli.py'
    assert entries['q4_card_runtime_plan'] == 'tools/wd_q4_staged_runtime_plan.py'
    assert entries['q4_card_fixed_plan'] == 'tools/wd_q4_staged_fixed_plan_writer.py'
    assert entries['q4_bridge_prefix_pin'] == 'tools/wd_q4_bridge_prefix_pin.py'
    assert entries['q4_card_decision'] == 'tools/wd_q4_real_card_decision_cli.py'
    assert entries['q4_lane_runtime_caller'] == 'tools/wd_q4_lane_caller_runtime_bootstrap.py'


def test_card_policy_and_prepare_libraries_are_packaged(monkeypatch):
    packaged = closure._packaged_python()
    assert all(packaged.count(path) == 1 for path in CARD_PREPARE_FILES)
    all_files = closure._definition()['python_files']
    assert all(all_files.count(path) == 1 for path in CARD_POLICY_FILES)
    reached = _reach(CARD_PREPARE_FILES, monkeypatch)
    assert reached <= set(packaged), sorted(reached - set(packaged))


def _caller_missing(packaged, reached):
    required = set(CALLER_DYNAMIC_BUNDLE_ROOTS) | reached
    return {path for path in required if packaged.count(path) != 1}


def test_caller_dynamic_bundle_roots_and_closure_are_packaged(monkeypatch):
    reached = _reach((*CARD_PREPARE_FILES, *CALLER_DYNAMIC_BUNDLE_ROOTS), monkeypatch)
    assert not _caller_missing(closure._packaged_python(), reached)


def test_literal_dynamic_loads_in_caller_are_declared():
    # Follow fixed literal imports and fixed paths; variable or constructed
    # imports still require separate review and are not proven complete here.
    bundle_paths, runtime_paths = set(), set()
    for relative in ('tools/wd_q4_lane_caller_runtime_bootstrap.py', 'tools/wd_q4_secure_transport.py',
                     'sandbox/mind/step2/runtime_bootstrap.py', 'sandbox/mind/step2/q4_controlled_imports.py'):
        tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for prefix, target in (('tools-bootstrap/', bundle_paths), ('assets/code/', runtime_paths)):
                    if node.value.startswith(prefix) and node.value.endswith('.py'):
                        target.add(node.value[len(prefix):])
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ('import_module', 'run_module') and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                name = node.args[0].value
                if name.startswith(('tools.', 'sandbox.', 'waggledance.')):
                    bundle_paths.add(name.replace('.', '/') + '.py')
    assert bundle_paths <= set(CALLER_DYNAMIC_BUNDLE_ROOTS), sorted(bundle_paths - set(CALLER_DYNAMIC_BUNDLE_ROOTS))
    known_runtime = set(CALLER_RUNTIME_ONLY_HELPERS) | set(CALLER_DYNAMIC_BUNDLE_ROOTS)
    assert runtime_paths <= known_runtime, sorted(runtime_paths - known_runtime)


@pytest.mark.parametrize('missing', CALLER_DYNAMIC_BUNDLE_ROOTS + ('tools/wd_q4_lane_caller_adapter.py',))
def test_caller_closure_detects_an_omitted_packaged_file(monkeypatch, missing):
    reached = _reach((*CARD_PREPARE_FILES, *CALLER_DYNAMIC_BUNDLE_ROOTS), monkeypatch)
    packaged = closure._packaged_python()
    assert missing in (set(CALLER_DYNAMIC_BUNDLE_ROOTS) | reached) and missing in packaged
    assert missing in _caller_missing([item for item in packaged if item != missing], reached)


@pytest.mark.parametrize('missing', (None, *CALLER_RUNTIME_ONLY_HELPERS))
def test_caller_runtime_planner_includes_path_loaded_helpers(monkeypatch, missing):
    # The three controlled-import helpers live in the runtime, not the bridge
    # bundle. Exercise the actual planner with an isolated synthetic Git tree.
    from tools import build_q4_runtime_plan as planner
    secure = 'tools/wd_q4_secure_transport.py'
    backend = 'tools/wd_q4_staged_fixed_backend.py'
    blobs = {name: ('VALUE = %d\n' % i).encode('ascii') for i, name in
             enumerate((secure, backend, *CALLER_RUNTIME_ONLY_HELPERS))}
    if missing is not None:
        del blobs[missing]
    entries = {name: ('100644', 'blob', name, len(raw)) for name, raw in blobs.items()}
    monkeypatch.setattr(planner, '_listing', lambda *_: entries)
    monkeypatch.setattr(planner, '_blob', lambda _git, _repo, oid, _size, _deadline: blobs[oid])
    args = ('synthetic-git', 'synthetic-repo', 'a' * 40, [secure], [], None, 1024 * 1024)
    backend_pin = hashlib.sha256(blobs[backend]).hexdigest()
    if missing is not None:
        with pytest.raises(planner.RuntimePlanRefused, match='code_closure_incomplete'):
            planner._code_plan(*args, staged_backend=backend_pin)
    else:
        records, _, _, _ = planner._code_plan(*args, staged_backend=backend_pin)
        assert set(CALLER_RUNTIME_ONLY_HELPERS) | {secure, backend} <= set(records)
        assert records[backend]['sha256'] == backend_pin


def test_the_card_module_import_closure_is_packaged(monkeypatch):
    reached = _reach([CARD_MODULE], monkeypatch)
    assert set(CARD_FILES) - {CARD_MODULE} <= reached
    assert reached <= set(closure._packaged_python()), sorted(reached - set(closure._packaged_python()))


def test_synthetic_card_producer_and_its_import_closure_are_packaged(monkeypatch):
    packaged = closure._packaged_python()
    assert packaged.count(CARD_PRODUCER) == 1
    reached = _reach([CARD_PRODUCER], monkeypatch)
    assert reached <= set(packaged), sorted(reached - set(packaged))


def test_synthetic_card_producer_is_not_a_published_entrypoint():
    definition = closure._definition()
    published = (set(definition['python_entrypoints'].values())
                 | set(definition['manual_execution_capability']['entrypoints'].values()))
    assert CARD_PRODUCER not in published


def test_card_checkpoint_and_its_import_closure_are_packaged(monkeypatch):
    packaged = closure._packaged_python()
    assert packaged.count(CARD_CHECKPOINT) == 1
    reached = _reach([CARD_CHECKPOINT], monkeypatch)
    assert reached <= set(packaged), sorted(reached - set(packaged))


def test_the_route_files_reach_card_files_only_as_packaged_code(monkeypatch):
    reached = _reach(ROUTE_FILES, monkeypatch)
    assert reached <= set(closure._packaged_python()), sorted(reached - set(closure._packaged_python()))


def test_card_files_are_code_only_and_never_published():
    definition = closure._definition()
    published = (set(definition['python_entrypoints'].values())
                 | set(definition['manual_execution_capability']['entrypoints'].values()))
    assert not published & set(CARD_FILES)
    dotted = {path[:-3].replace('/', '.') for path in CARD_FILES}
    assert not dotted & set(definition['import_smoke']['package_modules'])


@pytest.fixture
def staged(tmp_path):
    """Only manifest Python files; the child cannot fall back to repository or site imports."""
    root = tmp_path / 'tools-bootstrap'
    root.mkdir()
    for relative in closure._packaged_python():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return root


def test_staged_card_cli_entrypoints_are_off_by_default(staged):
    for module in ('tools.wd_q4_staged_card_composer_cli', 'tools.wd_q4_real_card_snapshot_cli',
                   'tools.wd_q4_public_card_publish_cli', 'tools.wd_q4_staged_runtime_plan',
                   'tools.wd_q4_staged_fixed_plan_writer', 'tools.wd_q4_bridge_prefix_pin',
                   'tools.wd_q4_real_card_decision_cli', 'tools.wd_q4_lane_caller_runtime_bootstrap'):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import " + module + " as cli; raise SystemExit(cli.main([]))"
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(staged)], cwd=staged,
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'status': 'OFF'}


def test_the_staged_package_imports_the_card_modules_with_the_route_off(staged):
    script = (
        "import importlib, json, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1]).resolve()\n"
        "sys.path.insert(0, str(root))\n"
        "resolver = importlib.import_module('sandbox.mind.step2.shared_result_resolver')\n"
        "child = importlib.import_module('tools.wd_q4_resolved_child')\n"
        "before = 'sandbox.mind.step2.shared_card_projection' in sys.modules\n"
        "switch = hasattr(resolver, 'CARD_ROUTE') or hasattr(resolver, 'card_route')\n"
        "import hashlib\n"
        "probe = importlib.import_module('sandbox.mind.step2.shared_card_projection')\n"
        "try:\n"
        "    probe.verify_route_decision(b'{}', hashlib.sha256(b'{}').hexdigest(),\n"
        "                                policy_sha256='0' * 64, roster_sha256='0' * 64)\n"
        "    decision = 'accepted'\n"
        "except probe.SharedCardRefused as refusal:\n"
        "    decision = str(refusal)\n"
        "cards = importlib.import_module('sandbox.mind.step2.shared_card_projection')\n"
        "producer = importlib.import_module('tools.wd_q4_card_generation')\n"
        "checkpoint = importlib.import_module('sandbox.mind.step2.shared_card_checkpoint')\n"
        "internal = {name: str(pathlib.Path(m.__file__).resolve()) for name, m in sys.modules.copy().items()\n"
        "            if name.split('.')[0] in ('tools', 'waggledance', 'sandbox') and getattr(m, '__file__', None)}\n"
        "print(json.dumps(dict(\n"
        "    before=before, switch=switch,\n"
        "    build=cards.build_shared_route_card_memory(None, None, None, None, None, None),\n"
        "    producer=producer.prepare_card_generation(),\n"
        "    checkpoint=checkpoint.validate_card_snapshot(),\n"
        "    resolve=cards.resolve_shared_card_results(), card_file=str(pathlib.Path(cards.__file__).resolve()),\n"
        "    outside=sorted(n for n, p in internal.items() if not pathlib.Path(p).is_relative_to(root)),\n"
        "    faiss='faiss' in sys.modules, numpy='numpy' in sys.modules, decision=decision,\n"
        "    isolated=sys.flags.isolated, no_site=sys.flags.no_site)))\n"
    )
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(staged)], cwd=staged,
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed['isolated'] == observed['no_site'] == 1 and observed['faiss'] is observed['numpy'] is False
    assert observed['decision'] == 'decision_invalid'
    assert observed['before'] is False
    assert observed['switch'] is False
    assert observed['build'] == observed['resolve'] == {'status': 'OFF'}
    assert observed['producer'] == {'status': 'OFF'}
    assert observed['checkpoint'] == {'status': 'OFF'}
    assert Path(observed['card_file']).is_relative_to(staged.resolve())
    assert observed['outside'] == []

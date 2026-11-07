"""Default-OFF package closure for the synthetic card modules (lead-mind-card-package-closure-fable-20261107-v1).

The card route of ``shared_result_resolver`` is OFF unless its switch holds the card module itself, and neither the
resolver nor the resolved child imports that module. These tests pin the smallest packaging that lets a bundle carry
the card modules as code only: the import closure of ``shared_card_projection`` is in ``python_files``, no card module
is an entrypoint, a manual capability or an import-smoke module, and the staged package imports with the standard
library only while the route stays OFF. Packaging is not wiring and not activation; nothing here reads an installed
bundle, a policy or a runtime.
"""
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
CARD_FILES = (
    'sandbox/mind/step2/public_knowledge_card.py',
    CARD_MODULE,
    'tools/wd_q4_public_card_prefix_verifier.py',
)
ROUTE_FILES = ('sandbox/mind/step2/shared_result_resolver.py', 'tools/wd_q4_resolved_child.py')


def _reach(seeds, monkeypatch):
    monkeypatch.setattr(closure, 'INTERNAL_ROOTS', (*closure.INTERNAL_ROOTS, 'core'))
    reached, unresolved = closure._closure(list(seeds))
    assert not unresolved, unresolved
    return set(reached)


def test_every_card_file_is_packaged_once():
    packaged = closure._packaged_python()
    assert [packaged.count(path) for path in CARD_FILES] == [1, 1, 1]


def test_the_card_module_import_closure_is_packaged(monkeypatch):
    reached = _reach([CARD_MODULE], monkeypatch)
    assert set(CARD_FILES) - {CARD_MODULE} <= reached
    assert reached <= set(closure._packaged_python()), sorted(reached - set(closure._packaged_python()))


def test_packaging_is_not_wiring_the_route_files_never_reach_a_card_file(monkeypatch):
    reached = _reach(ROUTE_FILES, monkeypatch)
    assert not reached & set(CARD_FILES), sorted(reached & set(CARD_FILES))


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


def test_the_staged_package_imports_the_card_modules_with_the_route_off(staged):
    script = (
        "import importlib, json, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1]).resolve()\n"
        "sys.path.insert(0, str(root))\n"
        "resolver = importlib.import_module('sandbox.mind.step2.shared_result_resolver')\n"
        "child = importlib.import_module('tools.wd_q4_resolved_child')\n"
        "before = 'sandbox.mind.step2.shared_card_projection' in sys.modules\n"
        "cards = importlib.import_module('sandbox.mind.step2.shared_card_projection')\n"
        "internal = {name: str(pathlib.Path(m.__file__).resolve()) for name, m in sys.modules.copy().items()\n"
        "            if name.split('.')[0] in ('tools', 'waggledance', 'sandbox') and getattr(m, '__file__', None)}\n"
        "print(json.dumps(dict(\n"
        "    before=before, switch=repr(resolver.CARD_ROUTE), route=repr(resolver.card_route()),\n"
        "    build=cards.build_shared_route_card_memory(None, None, None, None, None, None),\n"
        "    resolve=cards.resolve_shared_card_results(), card_file=str(pathlib.Path(cards.__file__).resolve()),\n"
        "    outside=sorted(n for n, p in internal.items() if not pathlib.Path(p).is_relative_to(root)),\n"
        "    faiss='faiss' in sys.modules, isolated=sys.flags.isolated, no_site=sys.flags.no_site)))\n"
    )
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(staged)], cwd=staged,
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed['isolated'] == observed['no_site'] == 1 and observed['faiss'] is False
    assert observed['before'] is False
    assert observed['switch'] == observed['route'] == 'None'
    assert observed['build'] == observed['resolve'] == {'status': 'OFF'}
    assert Path(observed['card_file']).is_relative_to(staged.resolve())
    assert observed['outside'] == []

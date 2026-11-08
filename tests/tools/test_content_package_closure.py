"""Default-OFF package closure for the useful content route modules (lead-q4-useful-work-content-fable-20261107-v1).

The content branch of ``shared_result_resolver`` is OFF unless the caller passes a pinned, verified content route
decision, and the resolver imports ``content_memory`` only inside that branch. These tests pin the smallest packaging
that lets a bundle carry the content modules as code only: they are in ``python_files``, none is an entrypoint, a
manual capability or an import-smoke module, the content modules never import the bridge LLM package (whose provider
clients the bundle does not ship), and importing the resolver does not load a content module. Packaging is not wiring
and not activation; nothing here reads an installed bundle, a policy or a runtime.
"""
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "ops/windows/reboot/bridge-code-files.json"
CONTENT_FILES = (
    "sandbox/mind/step2/content_memory.py",
    "sandbox/mind/step2/shared_content_projection.py",
    "sandbox/mind/step2/shared_doc_projection.py",
)
_spec = importlib.util.spec_from_file_location("package_closure_fixture_c", ROOT / "tests/tools/test_bridge_code_package_closure.py")
closure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(closure)


def definition():
    return json.loads(DEFINITION.read_text(encoding="utf-8"))


def test_the_content_modules_are_packaged_as_code_only():
    data = definition()
    assert set(CONTENT_FILES) <= set(data["python_files"])
    assert closure.CONTENT_PACKAGE_FILES == frozenset(CONTENT_FILES)
    assert not set(CONTENT_FILES) & set(data["python_entrypoints"].values())
    manual = json.dumps(data["manual_execution_capability"])
    smoke = set(data["import_smoke"]["package_modules"])
    for path in CONTENT_FILES:
        dotted = path[:-3].replace("/", ".")
        assert path not in manual and dotted not in manual and dotted not in smoke


def test_no_content_module_imports_the_bridge_llm_package():
    for path in CONTENT_FILES:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names.update(node.module + "." + alias.name for alias in node.names)
        assert not any(name.startswith("waggledance.core.bridge_llm") for name in names), path


def test_the_content_import_closure_is_packaged():
    gaps, unresolved = closure._closure(list(CONTENT_FILES) + ["sandbox/mind/step2/shared_result_resolver.py"])
    packaged = set(definition()["python_files"])
    assert not unresolved
    assert set(gaps) <= packaged


def test_content_plan_writer_and_its_imports_ship_inert():
    writer = "tools/wd_q4_public_content_plan_writer.py"
    data = definition()
    assert data["python_files"].count(writer) == 1
    assert writer not in data["python_entrypoints"].values()
    assert writer not in data["manual_execution_capability"]["entrypoints"].values()
    assert writer[:-3].replace("/", ".") not in data["import_smoke"]["package_modules"]
    gaps, unresolved = closure._closure([writer])
    assert not unresolved
    assert set(gaps) <= set(data["python_files"])


def test_importing_the_resolver_and_the_content_modules_loads_no_bridge_llm_module():
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from sandbox.mind.step2 import shared_result_resolver; "
            "assert 'sandbox.mind.step2.content_memory' not in sys.modules; "
            "from sandbox.mind.step2 import content_memory, shared_content_projection, shared_doc_projection; "
            "assert not [m for m in sys.modules if m.startswith('waggledance.core.bridge_llm')]")
    done = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]

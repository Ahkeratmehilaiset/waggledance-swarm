"""Packaging invariants for ops/windows/reboot/bridge-code-files.json.

The reboot bundle delivers two kinds of file by two different mechanisms, and
the difference is why new work silently fails to ship:

* ``.agent-bridge/bin/*.ps1`` is picked up by DIRECTORY ENUMERATION, so a new
  PowerShell helper ships the moment it is committed.
* ``tools/*.py`` and ``waggledance/**/*.py`` ship only if they appear in the
  explicit ``python_files`` allow-list in ``bridge-code-files.json``.

These tests make the Python side fail loudly instead of quietly.

WHAT THEY DO NOT PROVE. Source presence alone says nothing about installed
imports. The Mind checks below also import staged manifest files without site
packages and exercise the two published CLIs' help paths. They do not replace the
deployment-time ``import_smoke`` against the final installed bundle.

WHY THE PARSING IS SPLIT FROM THE FILESYSTEM. Every helper that interprets
source or installer text takes TEXT, so each fail-open mode below is
reproducible in a unit test rather than only against the live tree. The first
version of this file had four such modes and all four were invisible precisely
because the parsing could not be exercised directly.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_ROOT = REPO_ROOT / "ops" / "windows" / "reboot"
DEFINITION = OPS_ROOT / "bridge-code-files.json"
OBSERVER_INSTALLER = OPS_ROOT / "Install-WdCapacityObserver.ps1"
INTERNAL_ROOTS = ("tools", "waggledance", "sandbox")


# --- manifest ------------------------------------------------------------------


def _definition() -> dict:
    return json.loads(DEFINITION.read_text(encoding="utf-8"))


def _packaged_python() -> list[str]:
    return [entry for entry in _definition()["python_files"] if entry.endswith(".py")]


def test_manual_capability_declares_only_the_two_existing_drivers_not_default_entrypoints():
    definition = _definition()
    capability = definition["manual_execution_capability"]
    assert capability == {
        "schema": "wd.bridge-manual-execution.v1",
        "entrypoints": {
            "merge": "tools/merge_with_bridge_receipt.py",
            "receipt": "tools/write_bridge_consensus_merge_receipt.py",
        },
        "invocation_marker": "--wd-manual-execution",
    }
    paths = set(capability["entrypoints"].values())
    assert paths <= set(definition["python_files"])
    assert not paths & set(definition["python_entrypoints"].values())
    assert "enabled" not in capability  # declaration is never activation


# --- import parsing, on TEXT so every gap below is directly testable -----------


def _package_of(relative: str) -> str:
    """The dotted package a repository file lives in, for relative imports."""
    parts = Path(relative).parts
    if parts[-1] == "__init__.py":
        return ".".join(parts[:-1])
    return ".".join(parts[:-1])


def _internal_imports(source: str, package: str) -> set[str]:
    """Dotted internal modules a source file imports.

    Four things this must get right, each of which it previously did not:

    * RELATIVE imports (``from . import x``) were skipped outright, hiding any
      dependency expressed that way. They are now resolved against ``package``.
    * ``from tools import foo`` recorded only the package ``tools``, never
      ``tools.foo``, so a sibling module imported by that spelling was invisible.
      Both the module and each imported name are now recorded.
    * ``from tools.x import symbol`` must record ``tools.x.symbol`` as well, so
      the caller can tell a submodule from a symbol by whether it resolves.
    * EXTERNAL imports are ignored ON PURPOSE, including optional ones guarded
      by ``try: import foo / except ImportError``. Packaging says nothing about
      third-party availability; ``python_requirements`` and the deployment smoke
      run own that. This is stated because silence here is a policy, not a miss.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in INTERNAL_ROOTS:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".") if package else []
                trimmed = base_parts[: len(base_parts) - (node.level - 1)] \
                    if node.level > 1 else base_parts
                module = ".".join([*trimmed, node.module] if node.module else trimmed)
            else:
                module = node.module or ""
            if not module or module.split(".")[0] not in INTERNAL_ROOTS:
                continue
            found.add(module)
            for alias in node.names:
                if alias.name != "*":
                    found.add(f"{module}.{alias.name}")
    return found


def _module_path(dotted: str) -> str | None:
    base = dotted.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (REPO_ROOT / candidate).is_file():
            return candidate
    return None


def _bound_names(target) -> set[str]:
    """Names an assignment target binds: a Name, or each Name in a tuple/list target (starred too)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_bound_names(element) for element in target.elts))
    return set()  # an attribute or subscript target binds no module-level name


def _names_bound_in(source: str) -> set[str] | None:
    """Names module TEXT binds at top level, or None if a star-import hides them.

    Gap 5 (Lead 4fd61020): only a bare Name target counted, so a tuple assignment such as
    ``ADMIT, REFUSE, BLOCKED = ...`` bound nothing and the route's real constants were reported
    as nonexistent. Tuple, list and starred targets now bind; chained ``A = B = 0`` already did.
    Names bound only inside a top-level if/try/with block are still NOT counted (unchanged).
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names |= _bound_names(target)
        elif isinstance(node, ast.AnnAssign):
            names |= _bound_names(node.target)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None          # cannot know; caller stays conservative
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _top_level_names(relative: str) -> set[str] | None:
    """Names a module binds at top level, or None if a star-import hides them."""
    return _names_bound_in((REPO_ROOT / relative).read_text(encoding="utf-8"))


def _classify(dotted: str) -> tuple[str, str | None]:
    """('module', path) | ('symbol', parent path) | ('unresolved', None).

    An unresolved internal name used to be skipped in silence, which is the
    fail-open shape: a genuinely missing module looked exactly like a symbol.

    Checking only that the PARENT exists is not enough either, and that was the
    second version of the same bug: `tools.no_such_module` has a real parent
    package, so it passed as a symbol. A name counts as a symbol only when the
    parent module actually BINDS it. If the parent hides its namespace behind a
    star-import we cannot know, so we stay conservative and call it a symbol
    rather than manufacture a failure.
    """
    path = _module_path(dotted)
    if path is not None:
        return "module", path
    # The admitted shadow files use real PEP 420 packages, not generated __init__ files.
    # A real namespace parent never makes a missing child module into a symbol.
    if dotted.split(".")[0] == "sandbox" and (REPO_ROOT / dotted.replace(".", "/")).is_dir():
        return "namespace", None
    if "." in dotted:
        parent_dotted, leaf = dotted.rsplit(".", 1)
        parent = _module_path(parent_dotted)
        if parent is not None:
            bound = _top_level_names(parent)
            if bound is None or leaf in bound:
                return "symbol", parent
    return "unresolved", None


def _closure(seeds: list[str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Transitive internal imports of `seeds`. Returns (reached, unresolved)."""
    packaged = set(seeds)
    pending = list(seeds)
    seen = set(seeds)
    reached: dict[str, set[str]] = {}
    unresolved: dict[str, set[str]] = {}
    while pending:
        relative = pending.pop()
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for dotted in sorted(_internal_imports(source, _package_of(relative))):
            kind, target = _classify(dotted)
            if kind == "unresolved":
                unresolved.setdefault(dotted, set()).add(relative)
                continue
            if kind in ("symbol", "namespace"):
                continue
            if target in packaged:
                continue
            reached.setdefault(target, set()).add(relative)
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return reached, unresolved


# --- manifest tests -------------------------------------------------------------


def test_every_packaged_python_file_exists():
    missing = [entry for entry in _packaged_python()
               if not (REPO_ROOT / entry).is_file()]
    assert not missing, f"listed for packaging but absent from the repo: {missing}"


def test_source_package_initializers_are_not_silently_replaced_by_namespaces():
    packaged = set(_packaged_python())
    initializers = {
        (parent / "__init__.py").as_posix()
        for relative in packaged
        for parent in Path(relative).parents
        if parent != Path(".") and (REPO_ROOT / parent / "__init__.py").is_file()
    }
    assert initializers <= packaged, sorted(initializers - packaged)


def test_the_packaged_python_set_is_import_closed():
    """Nothing packaged may import an unpackaged internal module.

    A module that ships without the module it imports raises ImportError on the
    target machine, where nobody is watching, and the failure looks like the
    caller's fault.
    """
    gaps, _ = _closure(_packaged_python())
    assert not gaps, (
        "packaged modules import these unpackaged modules: "
        + "; ".join(f"{target} <- {sorted(importers)}"
                    for target, importers in sorted(gaps.items())))


def test_no_packaged_module_imports_a_nonexistent_internal_module():
    """Fail loudly on an internal name that resolves to nothing at all."""
    _, unresolved = _closure(_packaged_python())
    assert not unresolved, (
        "packaged modules import internal names that do not exist: "
        + "; ".join(f"{name} <- {sorted(importers)}"
                    for name, importers in sorted(unresolved.items())))


def test_the_import_smoke_list_only_names_packaged_modules():
    packaged = set(_packaged_python())
    unpackaged = [dotted for dotted in _definition()["import_smoke"]["package_modules"]
                  if _module_path(dotted) not in packaged]
    assert not unpackaged, f"import_smoke names unpackaged modules: {unpackaged}"


def test_every_entrypoint_is_packaged():
    packaged = set(_packaged_python())
    missing = {name: path
               for name, path in _definition()["python_entrypoints"].items()
               if path not in packaged}
    assert not missing, f"entrypoints that would not be delivered: {missing}"


def test_the_shipped_bridge_component_doctor_is_packaged():
    definition = _definition()
    assert "tools/wd_bridge_doctor.py" in definition["python_files"]
    assert definition["python_entrypoints"].get("bridge_doctor") == "tools/wd_bridge_doctor.py"


def test_f19_routing_and_shadow_learning_family_is_packaged_and_smoke_checked():
    """Prepared F19/F21/F26 code must ship, without implying an active caller."""
    modules = {
        "tools.wd_task_router",
        "tools.wd_routing_weights",
        "tools.wd_profile_qualification",
        "tools.wd_routing_capacity",
        "tools.wd_composer_select",
        "tools.wd_capacity_pacing",
    }
    definition = _definition()
    paths = {module.replace(".", "/") + ".py" for module in modules}
    assert paths <= set(definition["python_files"])
    assert modules <= set(definition["import_smoke"]["package_modules"])


RCO_CHECKERS = (
    "tools/check_bridge_changes_requested.py",
    "tools/check_rco_pass_present.py",
)
# The merge/gate executors and their verdict modules: shipped and smoke-checked, never published.
GATE_EXECUTORS = (
    "tools/idle_consensus_auto_merge.py",
    "tools/merge_with_bridge_receipt.py",
    "tools/write_bridge_consensus_merge_receipt.py",
    "tools/bridge_rule12_review_eligibility.py",
    "tools/bridge_v2_identity_registry.py",
    "tools/rule12_grok_ledger_adapter.py",
)


def _published_gate_paths(definition: dict) -> set[str]:
    return set(RCO_CHECKERS + GATE_EXECUTORS) & set(definition["python_entrypoints"].values())


MIND_MODULES = (
    "tools.wd_bridge_mind",
    "tools.wd_bridge_mind_lane_read",
    "tools.bridge_mind_workspace",
    "tools.wd_bridge_mind_delta_push",
    "tools.wd_bridge_mind_rebuild",
    "tools.wd_pr_missing_slot_board",
)
MIND_CLI_MODULES = (
    "tools.bridge_mind_workspace",
    "tools.wd_bridge_mind_delta_push",
)


def _assert_mind_delivery(definition: dict) -> None:
    paths = {module.replace(".", "/") + ".py" for module in MIND_MODULES}
    missing_files = paths - set(definition["python_files"])
    assert not missing_files, f"mind_file_drop: {sorted(missing_files)}"
    missing_smoke = set(MIND_MODULES) - set(definition["import_smoke"]["package_modules"])
    assert not missing_smoke, f"mind_smoke_drop: {sorted(missing_smoke)}"
    published = set(definition["python_entrypoints"].values())
    cli_paths = {module.replace(".", "/") + ".py" for module in MIND_CLI_MODULES}
    assert cli_paths <= published, f"mind_entrypoint_drop: {sorted(cli_paths - published)}"
    assert "tools/wd_pr_missing_slot_board.py" not in published, (
        "board_default_gate_entrypoint: board's default gate route must remain unpublished"
    )
    assert not (paths - cli_paths) & published, "Mind libraries must not masquerade as CLI entrypoints"
    assert not set(GATE_EXECUTORS) & published, "Mind delivery must not publish gate executors"
    assert definition["manual_execution_capability"] == {
        "schema": "wd.bridge-manual-execution.v1",
        "entrypoints": {
            "merge": "tools/merge_with_bridge_receipt.py",
            "receipt": "tools/write_bridge_consensus_merge_receipt.py",
        },
        "invocation_marker": "--wd-manual-execution",
    }, "manual execution remains a capability declaration, without activation"


def test_mind_family_is_packaged_smoke_checked_and_real_clis_are_published():
    """RCO2 closure finding: dormant source must reach the bundle before it can be used."""
    _assert_mind_delivery(_definition())


@pytest.mark.parametrize("mutation,module", [
    *(('mind_file_drop', module) for module in MIND_MODULES),
    *(('mind_smoke_drop', module) for module in MIND_MODULES),
    *(('mind_entrypoint_drop', module) for module in MIND_CLI_MODULES),
    ('board_default_gate_entrypoint', 'tools.wd_pr_missing_slot_board'),
])
def test_mind_delivery_contract_rejects_named_mutants(mutation, module):
    """The unchanged real manifest must pass before any single-field omission is tested."""
    definition = _definition()
    _assert_mind_delivery(definition)
    mutant = deepcopy(definition)
    path = module.replace(".", "/") + ".py"
    if mutation == "mind_file_drop":
        mutant["python_files"].remove(path)
    elif mutation == "mind_smoke_drop":
        mutant["import_smoke"]["package_modules"].remove(module)
    elif mutation == "mind_entrypoint_drop":
        mutant["python_entrypoints"] = {
            name: relative for name, relative in mutant["python_entrypoints"].items()
            if relative != path
        }
    else:
        mutant["python_entrypoints"]["mind_missing_slot_board"] = path
    with pytest.raises(AssertionError, match=mutation):
        _assert_mind_delivery(mutant)
    _assert_mind_delivery(definition)  # Mutating the copy did not repair or alter the control.


@pytest.fixture
def staged_mind_source(tmp_path):
    """Stage only manifest Python files; the child cannot fall back to repository or site imports."""
    definition = _definition()
    _assert_mind_delivery(definition)
    staged = tmp_path / "tools-bootstrap"
    staged.mkdir()
    for relative in definition["python_files"]:
        if relative.endswith(".py"):
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative, destination)
    return staged


def test_staged_mind_modules_import_with_stdlib_only(staged_mind_source):
    script = (
        "import importlib, json, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1]).resolve()\n"
        "sys.path.insert(0, str(root))\n"
        "names = json.loads(sys.argv[2])\n"
        "loaded = {name: str(pathlib.Path(importlib.import_module(name).__file__).resolve()) for name in names}\n"
        "internal = {name: str(pathlib.Path(module.__file__).resolve()) for name, module in sys.modules.copy().items() "
        "if name.split('.')[0] in ('tools', 'waggledance') and getattr(module, '__file__', None)}\n"
        "assert all(pathlib.Path(path).is_relative_to(root) for path in internal.values()), internal\n"
        "print(json.dumps(dict(loaded=loaded, isolated=sys.flags.isolated, no_site=sys.flags.no_site)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(staged_mind_source), json.dumps(MIND_MODULES)],
        cwd=staged_mind_source, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["isolated"] == observed["no_site"] == 1
    assert set(observed["loaded"]) == set(MIND_MODULES)
    assert all(Path(path).is_relative_to(staged_mind_source) for path in observed["loaded"].values())


@pytest.mark.parametrize("module", MIND_CLI_MODULES)
def test_staged_mind_cli_help_is_available_without_site_packages(staged_mind_source, module):
    script = (
        "import pathlib, runpy, sys\n"
        "sys.path.insert(0, str(pathlib.Path(sys.argv[1]).resolve()))\n"
        "name = sys.argv[2]\n"
        "sys.argv = [name, '--help']\n"
        "runpy.run_module(name, run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(staged_mind_source), module],
        cwd=staged_mind_source, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
    assert "--help" in result.stdout
    assert "Traceback" not in result.stderr


def test_consensus_gate_fixes_ship_and_are_smoke_checked_without_activation():
    """A new bundle must deliver its reviewed gate fixes, not just a new pointer.

    The only published gate paths are the two diagnostic RCO checkers (C6, three-party
    route consensus 6108CB61); every merge/gate executor stays unpublished."""
    modules = {
        "tools.check_bridge_changes_requested",
        "tools.check_rco_pass_present",
        "tools.idle_consensus_auto_merge",
        "tools.bridge_v2_identity_registry",
        "tools.bridge_rule12_review_eligibility",
        "tools.merge_with_bridge_receipt",
        "tools.write_bridge_consensus_merge_receipt",
    }
    definition = _definition()
    paths = {module.replace(".", "/") + ".py" for module in modules}
    assert paths <= set(definition["python_files"])
    assert modules <= set(definition["import_smoke"]["package_modules"])
    adapter = "tools/rule12_grok_ledger_adapter.py"
    assert adapter in definition["python_files"]
    # The already-smoked idle gate imports the adapter transitively; no new entrypoint.
    reached, _ = _closure(["tools/idle_consensus_auto_merge.py"])
    assert adapter in reached
    assert _published_gate_paths(definition) == set(RCO_CHECKERS)
    assert definition["python_entrypoints"].get("rco_changes_requested") == RCO_CHECKERS[0]
    assert definition["python_entrypoints"].get("rco_pass_present") == RCO_CHECKERS[1]


@pytest.mark.parametrize("mutation", ["adapter_file_drop", "adapter_transitive_smoke_closure_drop"])
def test_dropping_rule12_grok_adapter_breaks_gate_delivery_checks(monkeypatch, mutation):
    """c0093: the real delivery check kills adapter drops, with a clean control."""
    test_consensus_gate_fixes_ship_and_are_smoke_checked_without_activation()
    adapter = "tools/rule12_grok_ledger_adapter.py"
    if mutation == "adapter_file_drop":
        definition = _definition()
        definition["python_files"].remove(adapter)
        monkeypatch.setitem(globals(), "_definition", lambda: definition)
    else:
        original_closure = _closure

        def without_adapter(seeds):
            reached, unresolved = original_closure(seeds)
            return {path: importers for path, importers in reached.items()
                    if path != adapter}, unresolved

        monkeypatch.setitem(globals(), "_closure", without_adapter)
    with pytest.raises(AssertionError):
        test_consensus_gate_fixes_ship_and_are_smoke_checked_without_activation()


@pytest.mark.parametrize("executor", GATE_EXECUTORS)
def test_publishing_any_gate_executor_breaks_the_no_activation_check(executor):
    """Negative control: a third published gate module is caught, under any name."""
    definition = _definition()
    assert _published_gate_paths(definition) == set(RCO_CHECKERS)
    definition["python_entrypoints"]["innocent_looking_tool"] = executor
    assert _published_gate_paths(definition) != set(RCO_CHECKERS)


def test_the_rco_checkers_ship_with_their_existing_internal_closure():
    """Both Rule 9a checkers are entrypoints, so their whole internal import
    closure must be packaged too. The seeds are the checkers alone, so this
    fails on the checkers' own gap even before the global closure test does."""
    packaged = set(_packaged_python())
    for checker in RCO_CHECKERS:
        assert checker in packaged, checker
    reached, unresolved = _closure(list(RCO_CHECKERS))
    assert not unresolved, sorted(unresolved)
    missing = {target: sorted(importers)
               for target, importers in reached.items() if target not in packaged}
    assert not missing, f"checker closure not packaged: {missing}"


def test_consensus_gate_data_and_existing_jsonschema_dependency_are_shipped():
    definition = _definition()
    required_data = {
        "docs/architecture/IDLE_AUTONOMY_CHARTER.md",
        "schemas/v3_13_0/idle_protocol.v1.json",
        "schemas/v3_13_0/magma_receipt.v1.json",
        "schemas/v3_13_0/evaluation_result.v0.json",
        "schemas/v3_13_0/evaluation_result.v1.json",
        "schemas/v3_13_0/policy_surface.v0.json",
    }
    assert required_data <= set(definition["python_files"])
    assert all((REPO_ROOT / relative).is_file() for relative in required_data)
    assert {"jsonschema", "attrs", "jsonschema-specifications", "referencing", "rpds-py"} <= {
        requirement["name"] for requirement in definition["python_requirements"]
    }
    for relative in required_data:
        if relative.endswith(".json"):
            schema = json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))
            # These schemas use local references only; no undeclared remote data dependency.
            def refs(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "$ref":
                            yield item
                        yield from refs(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from refs(item)
            assert all(ref.startswith("#") for ref in refs(schema)), relative


def test_passive_participants_and_dashboard_ship_without_new_entrypoints():
    """F5/F6 are optional caller-fed libraries, not newly enabled collectors."""
    modules = {"tools.bridge_lock_participants", "tools.bridge_v2_dashboard"}
    definition = _definition()
    paths = {module.replace(".", "/") + ".py" for module in modules}
    assert paths <= set(definition["python_files"])
    assert modules <= set(definition["import_smoke"]["package_modules"])
    assert not paths & set(definition["python_entrypoints"].values())


def test_advisory_input_assembler_and_reader_ship_without_new_entrypoints():
    modules = {"tools.wd_routing_inputs", "tools.wd_routing_reader"}
    definition = _definition()
    paths = {module.replace(".", "/") + ".py" for module in modules}
    assert paths <= set(definition["python_files"])
    assert modules <= set(definition["import_smoke"]["package_modules"])
    assert not paths & set(definition["python_entrypoints"].values())


def test_queue_snapshot_w3_load_and_python_root_mutex_ship_without_new_entrypoints():
    """Passive shipping only: it is not global-idle or exclusive-dispatch proof."""
    modules = {
        "tools.bridge_v2_queue_snapshot",
        "tools.wd_routing_load",
        "tools.bridge_v2_queue_ports_windows",
        "tools.bridge_named_mutex",
        "tools.work_queue",
        "tools.work_queue_sweep_stale",
    }
    definition = _definition()
    paths = {module.replace(".", "/") + ".py" for module in modules}
    assert paths <= set(definition["python_files"])
    assert modules <= set(definition["import_smoke"]["package_modules"])
    assert not paths & set(definition["python_entrypoints"].values())


# --- the parser's own fail-open modes, reproduced -------------------------------


def test_relative_imports_are_resolved_not_ignored():
    """Gap 1. Skipping `node.level` hid every dependency written this way."""
    source = "from . import sibling\nfrom .deeper import thing\n"
    found = _internal_imports(source, "tools")
    assert "tools.sibling" in found
    assert "tools.deeper" in found and "tools.deeper.thing" in found


def test_a_parent_relative_import_walks_up_one_package():
    source = "from .. import shared\n"
    assert "waggledance.shared" in _internal_imports(source, "waggledance.core")


def test_from_package_import_module_records_the_module():
    """Gap 3. `from tools import foo` recorded only `tools`, never `tools.foo`."""
    found = _internal_imports("from tools import bridge_next_action\n", "tools")
    assert "tools.bridge_next_action" in found, found


def test_from_module_import_symbol_records_both_forms():
    found = _internal_imports(
        "from tools.bridge_next_action import thing\n", "tools")
    assert "tools.bridge_next_action" in found
    assert "tools.bridge_next_action.thing" in found


def test_external_imports_are_ignored_including_optional_ones():
    """Stated as policy, not left as an accident of the internal-root filter."""
    source = (
        "import json\n"
        "try:\n"
        "    import pydantic\n"
        "except ImportError:\n"
        "    pydantic = None\n"
        "from collections import deque\n"
    )
    assert _internal_imports(source, "tools") == set()


def test_a_star_import_does_not_invent_a_submodule():
    assert _internal_imports("from tools import *\n", "tools") == {"tools"}


def test_an_unresolvable_internal_name_is_classified_unresolved():
    """Gap 2. A missing module used to look exactly like a symbol."""
    assert _classify("tools.no_such_module_anywhere")[0] == "unresolved"
    assert _classify("tools.bridge_next_action")[0] == "module"
    assert _classify("tools.bridge_next_action.build_parser")[0] == "symbol"


def test_tuple_list_starred_and_chained_targets_are_bound_names():
    """Gap 5 (Lead 4fd61020), on TEXT: a tuple target bound nothing, so real constants looked missing."""
    source = ("ADMIT, REFUSE, BLOCKED = 'admit', 'refuse', 'blocked_unknown'\n"
              "HEX32, HEX40, HEX64 = (1, 2, 3)\n"
              "[FIRST, *REST] = [1, 2, 3]\n"
              "A = B = 0\n"
              "LIMIT: int = 5\n"
              "holder.attribute = 1\n")
    names = _names_bound_in(source)
    assert {"ADMIT", "REFUSE", "BLOCKED", "HEX32", "HEX40", "HEX64", "FIRST", "REST", "A", "B", "LIMIT"} <= names
    assert not {"attribute", "holder", "MISSING"} & names  # nothing unbound is invented
    assert _names_bound_in("from tools import *\n") is None  # a star-import still stays conservative


def test_real_route_constants_resolve_and_a_truly_missing_name_stays_unresolved():
    """The failing case on the real packaged route: its tuple-assigned constants are symbols, while a
    name it never binds still refuses as unresolved (no allowlist, no skip)."""
    for leaf in ("ADMIT", "REFUSE", "BLOCKED", "HEX40", "HEX64"):
        assert _classify(f"tools.bridge_v2_grok_route.{leaf}") == ("symbol", "tools/bridge_v2_grok_route.py")
    assert _classify("tools.bridge_v2_grok_route.NO_SUCH_CONSTANT_ANYWHERE") == ("unresolved", None)

# --- the second delivery surface, parsed from the actual array ------------------
#
# The observer installer carries its own delivery list, independently of the
# reboot bundle's allowlist. Some capacity modules are shipped by both surfaces;
# this section checks the observer's actual array rather than inferring delivery.

_FILES_ARRAY = re.compile(r"\$files\s*=\s*@\((?P<body>.*?)\)", re.DOTALL)
_QUOTED = re.compile(r"'([^']*)'")


def _payload_from_installer_text(text: str) -> set[str]:
    """Parse the real `$files = @(...)` array only.

    Gap 4. Scanning the whole file counted a COMMENT mentioning a module as
    delivery, and this installer really does name two modules in comments above
    the array, so removing either from the array would not have been noticed.
    """
    match = _FILES_ARRAY.search(text)
    if match is None:
        return set()
    body = "\n".join(line.split("#", 1)[0] for line in match.group("body").splitlines())
    return {entry.replace("\\", "/") for entry in _QUOTED.findall(body)
            if entry.endswith(".py")}


def _observer_payload() -> set[str]:
    return _payload_from_installer_text(
        OBSERVER_INSTALLER.read_text(encoding="utf-8"))


def test_a_commented_out_module_does_not_count_as_delivered():
    """Gap 4, reproduced on text shaped like the real installer."""
    text = (
        "# bridge_capacity_attribution.py is imported by the collector\n"
        "$files = @('tools\\bridge_capacity_advisor.py',\n"
        "           # 'tools\\bridge_capacity_recovery.py',\n"
        "           'tools\\bridge_capacity_collector.py')\n"
    )
    payload = _payload_from_installer_text(text)
    assert payload == {"tools/bridge_capacity_advisor.py",
                       "tools/bridge_capacity_collector.py"}, payload
    assert "tools/bridge_capacity_attribution.py" not in payload, \
        "a comment above the array was counted as delivery"
    assert "tools/bridge_capacity_recovery.py" not in payload, \
        "a commented-out array entry was counted as delivery"


def test_the_observer_installer_still_carries_the_capacity_modules():
    """Non-vacuity guard: an empty payload would make the delivery test hollow."""
    payload = _observer_payload()
    assert len(payload) >= 5, f"observer payload parsed as {sorted(payload)}"
    assert "tools/bridge_capacity_collector.py" in payload


@pytest.mark.parametrize("module", sorted(_observer_payload()))
def test_observer_payload_modules_exist(module):
    assert (REPO_ROOT / module).is_file(), f"observer ships a missing file: {module}"


# --- cross-surface delivery ------------------------------------------------------


_MODULE_REFERENCE = re.compile(r"tools/[A-Za-z0-9_]+\.py")


def _ops_referenced_modules() -> dict[str, set[str]]:
    """Every tools/*.py an ops launcher names, and which scripts name it."""
    referenced: dict[str, set[str]] = {}
    for script in sorted(OPS_ROOT.glob("*.ps1")):
        if script.name == OBSERVER_INSTALLER.name:
            continue                  # its array IS a delivery manifest, not a call
        text = script.read_text(encoding="utf-8", errors="replace")
        for candidate in _MODULE_REFERENCE.findall(text.replace("\\", "/")):
            referenced.setdefault(candidate, set()).add(script.name)
    return referenced


def test_the_ops_reference_scan_is_not_vacuous():
    referenced = _ops_referenced_modules()
    assert len(referenced) >= 5, f"ops reference scan found only {sorted(referenced)}"
    assert "tools/bridge_next_action.py" in referenced


def test_every_module_an_ops_script_invokes_is_delivered_by_some_installer():
    delivered = set(_packaged_python()) | _observer_payload()
    undelivered = {module: sorted(scripts)
                   for module, scripts in _ops_referenced_modules().items()
                   if module not in delivered and (REPO_ROOT / module).is_file()}
    assert not undelivered, (
        "ops scripts invoke modules no installer delivers: " + repr(undelivered))


# Bounded shadow release: delivery declarations are not runtime authorization.
SHADOW_RELEASE_MODULES = (
    "tools.wd_bridge_mind_shadow",
    "sandbox.mind.mind_capture_producer",
    "sandbox.mind.mind_chain_driver",
    "sandbox.mind.join_turn",
    "sandbox.mind.join_card",
    "sandbox.mind.join_card_delivery",
    "sandbox.mind.step1.lane_view",
    "sandbox.mind.step1.lane_view_frozen",
    "sandbox.mind.capture.capacity_lens",
    "sandbox.mind.capture.overlap_lens",
)
SHADOW_RELEASE_CLI = {
    "mind_shadow_producer": "sandbox/mind/mind_capture_producer.py",
    "mind_join_turn": "sandbox/mind/join_turn.py",
}

# Read-only Q4 source may be delivered as code only. Its numerical runtime and
# query entrypoint are not authorized by this source-file declaration.
Q4_READONLY_FILES = frozenset({
    "sandbox/mind/step2/cell.py", "sandbox/mind/step2/corpus.py",
    "sandbox/mind/step2/encode.py", "sandbox/mind/step2/memory.py",
    "sandbox/mind/step2/q4.py", "sandbox/mind/step2/reader_slot.py", "sandbox/mind/step2/runtime_manifest.py",
    "sandbox/mind/step2/wd_q4_private_read.py",
})


def _assert_shadow_release_delivery(definition):
    _assert_mind_delivery(definition)  # Existing gates and manual capability stay unchanged.
    required = {name.replace(".", "/") + ".py" for name in SHADOW_RELEASE_MODULES}
    assert required <= set(definition["python_files"]), "shadow_file_drop"
    assert set(SHADOW_RELEASE_MODULES) <= set(
        definition["import_smoke"]["package_modules"]), "shadow_smoke_drop"
    for name, path in SHADOW_RELEASE_CLI.items():
        assert definition["python_entrypoints"].get(name) == path, "shadow_entrypoint_drop"
    published = set(definition["python_entrypoints"].values())
    assert not (required - set(SHADOW_RELEASE_CLI.values())) & published, "shadow_library_published"
    sandbox_files = {path for path in definition["python_files"] if path.startswith("sandbox/")}
    # Q4 files are delivery-only too: the existing CLI and smoke boundaries remain unchanged.
    assert Q4_READONLY_FILES <= sandbox_files, "q4_file_drop"
    assert sandbox_files <= required | set(M0_MEASUREMENT_TOOL_FILES) | Q4_READONLY_FILES, "deferred_sandbox_file_published"
    sandbox_entrypoints = {path for path in published if path.startswith("sandbox/")}
    assert sandbox_entrypoints <= set(SHADOW_RELEASE_CLI.values()), "deferred_shadow_entrypoint"
    sandbox_smoke = {name for name in definition["import_smoke"]["package_modules"]
                     if name.startswith("sandbox.")}
    assert sandbox_smoke <= set(SHADOW_RELEASE_MODULES), "deferred_shadow_smoke"
    assert "enabled" not in definition["manual_execution_capability"]


@pytest.mark.parametrize("mutation", [
    None, "shadow_file_drop", "shadow_smoke_drop", "shadow_entrypoint_drop",
    "shadow_library_published", "deferred_sandbox_file_published",
    "deferred_shadow_entrypoint", "deferred_shadow_smoke", "q4_file_drop", "q4_adapter_drop", "q4_slot_drop",
    "q4_runtime_manifest_drop",
])
def test_shadow_release_delivery_contract_and_named_mutants(mutation):
    definition = _definition()
    _assert_shadow_release_delivery(definition)
    if mutation is None:
        return
    mutant = deepcopy(definition)
    if mutation == "shadow_file_drop":
        mutant["python_files"].remove("sandbox/mind/mind_capture_producer.py")
    elif mutation == "shadow_smoke_drop":
        mutant["import_smoke"]["package_modules"].remove("sandbox.mind.join_card")
    elif mutation == "shadow_entrypoint_drop":
        mutant["python_entrypoints"].pop("mind_join_turn")
    elif mutation == "shadow_library_published":
        mutant["python_entrypoints"]["unreviewed_driver_cli"] = "sandbox/mind/mind_chain_driver.py"
    elif mutation == "q4_file_drop":
        mutant["python_files"].remove("sandbox/mind/step2/q4.py")
    elif mutation == "q4_adapter_drop":
        mutant["python_files"].remove("sandbox/mind/step2/wd_q4_private_read.py")
    elif mutation == "q4_slot_drop":
        mutant["python_files"].remove("sandbox/mind/step2/reader_slot.py")
    elif mutation == "q4_runtime_manifest_drop":
        mutant["python_files"].remove("sandbox/mind/step2/runtime_manifest.py")
    elif mutation == "deferred_shadow_entrypoint":
        mutant["python_entrypoints"]["unlisted_cli"] = "sandbox/mind/deferred_runner.py"
    elif mutation == "deferred_shadow_smoke":
        mutant["import_smoke"]["package_modules"].append("sandbox.mind.deferred_memory")
    else:
        mutant["python_files"].append("sandbox/mind/step1/run_parity_1b.py")
    with pytest.raises(AssertionError, match="q4_file_drop" if mutation in {
            "q4_adapter_drop", "q4_slot_drop", "q4_runtime_manifest_drop"} else mutation):
        _assert_shadow_release_delivery(mutant)
    _assert_shadow_release_delivery(definition)


def test_shadow_namespace_imports_are_traced_not_silently_ignored():
    found = _internal_imports("from sandbox.mind import join_turn\n", "tools")
    assert {"sandbox.mind", "sandbox.mind.join_turn"} <= found


def test_shadow_namespace_does_not_hide_a_missing_module(tmp_path, monkeypatch):
    (tmp_path / "sandbox" / "mind").mkdir(parents=True)
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
    assert _classify("sandbox.mind") == ("namespace", None)
    assert _classify("sandbox.mind.no_such_module") == ("unresolved", None)


@pytest.mark.parametrize("cli_module", [None, "sandbox.mind.mind_capture_producer", "sandbox.mind.join_turn"])
def test_staged_shadow_import_and_help_refuse_gate_modules(staged_mind_source, cli_module):
    """Run only after final-source admission: isolated staged bytes, no gate execution.

    This checks import/help paths, not a producer cycle, native deployment or
    arbitrary dynamic imports. Merely shipping a gate file is not importing it.
    """
    denied = {path[:-3].replace("/", ".") for path in (*GATE_EXECUTORS, *RCO_CHECKERS)} | {
        "tools.bridge_event_taxonomy",
        "tools.check_proven_safe_autosign_class",
        "tools.check_standing_consensus_sign_class",
        "tools.classify_bridge_ci_scope",
        "waggledance.core.idle_consensus_charter",
        "waggledance.core.magma.consensus_receipt",
    }
    script = (
        "import importlib, importlib.abc, json, pathlib, runpy, sys\n"
        "root = pathlib.Path(sys.argv[1]).resolve()\n"
        "denied = set(json.loads(sys.argv[2]))\n"
        "names = json.loads(sys.argv[3])\n"
        "cli = json.loads(sys.argv[4])\n"
        "class NoGate(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self, fullname, path=None, target=None):\n"
        "  if any(fullname == name or fullname.startswith(name + '.') for name in denied):\n"
        "   raise RuntimeError('shadow_gate_import_refused:' + fullname)\n"
        "guard = NoGate()\n"
        "sys.meta_path.insert(0, guard)\n"
        "assert sys.meta_path[0] is guard, 'shadow_gate_guard_control_failed'\n"
        "for candidate in sorted(denied):\n"
        " try:\n"
        "  guard.find_spec(candidate)\n"
        " except RuntimeError as error:\n"
        "  assert str(error) == 'shadow_gate_import_refused:' + candidate, 'shadow_gate_guard_control_failed'\n"
        " else:\n"
        "  raise AssertionError('shadow_gate_guard_control_failed')\n"
        "sys.path.insert(0, str(root))\n"
        "def source_path_owned(value):\n"
        " assert pathlib.Path(value).is_absolute(), 'shadow_source_origin_not_absolute'\n"
        " assert pathlib.Path(value).resolve().is_relative_to(root), 'shadow_source_origin_outside_staged_root'\n"
        "if cli is not None:\n"
        " sys.argv = [cli, '--help']\n"
        " try:\n"
        "  runpy.run_module(cli, run_name='__main__')\n"
        " except SystemExit as error:\n"
        "  if error.code not in (None, 0):\n"
        "   raise\n"
        "else:\n"
        " loaded = {name: str(pathlib.Path(importlib.import_module(name).__file__).resolve()) for name in names}\n"
        " assert all(pathlib.Path(path).is_relative_to(root) for path in loaded.values())\n"
        "for name, module in list(sys.modules.items()):\n"
        " if name.partition('.')[0] in {'tools', 'waggledance', 'sandbox', 'step1', 'join_card', 'join_card_delivery', 'lane_view', 'lane_view_frozen'}:\n"
        "  origin = getattr(module, '__file__', None)\n"
        "  if origin is not None:\n"
        "   source_path_owned(origin)\n"
        "  else:\n"
        "   locations = list(getattr(module, '__path__', ()))\n"
        "   assert locations, 'shadow_source_origin_missing'\n"
        "   for location in locations:\n"
        "    source_path_owned(location)\n"
        "if cli is None:\n"
        " print(json.dumps(dict(loaded=loaded, isolated=sys.flags.isolated, no_site=sys.flags.no_site)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(staged_mind_source),
         json.dumps(sorted(denied)), json.dumps(SHADOW_RELEASE_MODULES), json.dumps(cli_module)],
        cwd=staged_mind_source, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    if cli_module is None:
        observed = json.loads(result.stdout)
        assert observed["isolated"] == observed["no_site"] == 1
        assert set(observed["loaded"]) == set(SHADOW_RELEASE_MODULES)
    else:
        assert "usage:" in result.stdout.lower() and "--help" in result.stdout


@pytest.mark.parametrize("removed", [
    None, "tools.check_proven_safe_autosign_class",
    "waggledance.core.magma.consensus_receipt",
    "tools.classify_bridge_ci_scope",
])
def test_shadow_guard_denies_core_governance_and_named_mutants(removed):
    """Inspect the actual child caller deny set without importing any gate."""
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_staged_shadow_import_and_help_refuse_gate_modules")
    assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "denied"
                              for target in node.targets))
    denied = eval(compile(ast.Expression(assignment.value), "<actual-shadow-deny-set>", "eval"),
                  {"GATE_EXECUTORS": GATE_EXECUTORS, "RCO_CHECKERS": RCO_CHECKERS})
    required = {
        "tools.check_proven_safe_autosign_class",
        "waggledance.core.magma.consensus_receipt",
        "tools.merge_with_bridge_receipt",
        "tools.bridge_v2_identity_registry",
        "tools.classify_bridge_ci_scope",
    }
    assert required <= denied, "shadow_governance_guard_incomplete"
    if removed is not None:
        mutant = denied - {removed}
        with pytest.raises(AssertionError, match="shadow_governance_guard_incomplete"):
            assert required <= mutant, "shadow_governance_guard_incomplete"


def test_shadow_finder_positive_control_kills_noop_mutant(tmp_path):
    """Exercise the ACTUAL child code, with empty imports and a nonexistent toy gate.

    No product/gate source is imported. The empty import list alone must not
    allow a disabled finder to masquerade as a checked denial mechanism.
    """
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_staged_shadow_import_and_help_refuse_gate_modules")
    assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "script"
                              for target in node.targets))
    script = ast.literal_eval(assignment.value)
    anchor = "raise RuntimeError('shadow_gate_import_refused:' + fullname)"
    assert script.count(anchor) == 1
    commands = [sys.executable, "-I", "-S", "-B", "-c"]
    arguments = [str(tmp_path), json.dumps(["nonexistent_synthetic_gate"]), "[]", "null"]
    control = subprocess.run(commands + [script] + arguments, cwd=tmp_path,
                             capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert control.returncode == 0, control.stderr
    assert json.loads(control.stdout)["loaded"] == {}
    mutant = subprocess.run(commands + [script.replace(anchor, "return None")] + arguments,
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert mutant.returncode != 0, "MUTANT_NOOP_GATE_FINDER survived"
    assert "shadow_gate_guard_control_failed" in mutant.stderr


@pytest.mark.parametrize("name", [
    "tools.synthetic_unlisted", "waggledance.synthetic_unlisted",
    "sandbox.synthetic_unlisted", "step1.synthetic_unlisted", "join_card",
    "lane_view", "lane_view_frozen",
])
@pytest.mark.parametrize("namespace", [False, True])
def test_shadow_source_origin_covers_unlisted_internal_modules_and_namespaces(tmp_path, name, namespace):
    """REGRESSION_TWIN_UNLISTED_MODULE_ORIGIN uses the actual staged child.

    Only synthetic sys.modules entries are injected, never foreign Source or
    a product import. An in-root entry controls an identical out-of-root one.
    """
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_staged_shadow_import_and_help_refuse_gate_modules")
    assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "script"
                              for target in node.targets))
    script = ast.literal_eval(assignment.value)
    injection_anchor = " loaded = {name: str(pathlib.Path(importlib.import_module(name).__file__).resolve()) for name in names}"
    assert script.count(injection_anchor) == 1
    commands = [sys.executable, "-I", "-S", "-B", "-c"]
    arguments = [str(tmp_path), "[]", "[]", "null"]

    def run(candidate, foreign):
        origin = str((tmp_path.parent if foreign else tmp_path) / "synthetic.py")
        injected = (
            " import types\n"
            f" synthetic = types.ModuleType({name!r})\n"
            f" synthetic.__file__ = {None if namespace else origin!r}\n"
            + (f" synthetic.__path__ = {[str(Path(origin).parent)]!r}\n" if namespace else "")
            + f" sys.modules[{name!r}] = synthetic\n"
        )
        return subprocess.run(
            commands + [candidate.replace(injection_anchor, injected + injection_anchor)] + arguments,
            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )

    control = run(script, False)
    assert control.returncode == 0, control.stderr
    refused = run(script, True)
    assert refused.returncode != 0, "REGRESSION_TWIN_UNLISTED_MODULE_ORIGIN accepted a foreign origin"
    assert "shadow_source_origin_outside_staged_root" in refused.stderr
    guard_anchor = (
        " assert pathlib.Path(value).resolve().is_relative_to(root), "
        "'shadow_source_origin_outside_staged_root'"
    )
    assert script.count(guard_anchor) == 1
    mutant = run(script.replace(guard_anchor, " pass  # MUTANT_NOOP_SOURCE_ORIGIN"), True)
    assert mutant.returncode == 0, mutant.stderr


@pytest.mark.parametrize("origin", ["synthetic.py", ".", ""])
@pytest.mark.parametrize("namespace", [False, True])
def test_shadow_source_origin_refuses_relative_file_and_namespace_origins(tmp_path, origin, namespace):
    """A relative metadata path must not be credited to the staged cwd."""
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_staged_shadow_import_and_help_refuse_gate_modules")
    assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "script"
                              for target in node.targets))
    script = ast.literal_eval(assignment.value)
    anchor = " loaded = {name: str(pathlib.Path(importlib.import_module(name).__file__).resolve()) for name in names}"
    assert script.count(anchor) == 1
    injected = (
        " import types\n"
        " synthetic = types.ModuleType('sandbox.synthetic_relative')\n"
        f" synthetic.__file__ = {None if namespace else origin!r}\n"
        + (f" synthetic.__path__ = {[origin]!r}\n" if namespace else "")
        + " sys.modules['sandbox.synthetic_relative'] = synthetic\n"
    )
    command = [sys.executable, "-I", "-S", "-B", "-c"]
    arguments = [str(tmp_path), "[]", "[]", "null"]
    candidate = script.replace(anchor, injected + anchor)
    refused = subprocess.run(command + [candidate] + arguments, cwd=tmp_path,
                             capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert refused.returncode != 0, "RELATIVE_SOURCE_ORIGIN was credited to cwd"
    assert "shadow_source_origin_not_absolute" in refused.stderr
    guard = " assert pathlib.Path(value).is_absolute(), 'shadow_source_origin_not_absolute'"
    assert candidate.count(guard) == 1
    mutant = subprocess.run(command + [candidate.replace(guard, " pass  # MUTANT_RELATIVE_SOURCE_ORIGIN")] + arguments,
                            cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert mutant.returncode == 0, mutant.stderr


M0_MEASUREMENT_TOOL_FILES = (
    "sandbox/mind/measurement_tools/m0_lite_hash_judge.py",
    "sandbox/mind/measurement_tools/m0_lite_refusal_recorder.py",
)


def _assert_m0_measurement_tool_delivery(definition, relative):
    assert definition["python_files"].count(relative) == 1, "m0_frozen_file_not_packaged_once"
    assert relative not in definition["python_entrypoints"].values(), "m0_entrypoint_not_authorized"
    dotted = relative[:-3].replace("/", ".")
    assert dotted not in definition["import_smoke"]["package_modules"], "m0_smoke_launch_not_authorized"
    assert relative not in definition["manual_execution_capability"]["entrypoints"].values(), (
        "m0_manual_execution_not_authorized"
    )


@pytest.mark.parametrize("relative", M0_MEASUREMENT_TOOL_FILES)
def test_m0_frozen_tools_are_packaged_without_new_launch_routes(relative):
    _assert_m0_measurement_tool_delivery(_definition(), relative)


@pytest.mark.parametrize("relative", M0_MEASUREMENT_TOOL_FILES)
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "entrypoint", "smoke", "manual"])
def test_m0_file_only_delivery_contract_refuses_each_boundary_mutant(relative, mutation):
    definition = _definition()
    # A synthetic success twin is useful on both the pre-fix and post-fix tree.
    # It does not change any Source, build a bundle, or imply an installed file.
    definition["python_files"] = [
        item for item in definition["python_files"] if item != relative
    ] + [relative]
    _assert_m0_measurement_tool_delivery(definition, relative)
    mutant = deepcopy(definition)
    if mutation == "missing":
        mutant["python_files"].remove(relative)
    elif mutation == "duplicate":
        mutant["python_files"].append(relative)
    elif mutation == "entrypoint":
        mutant["python_entrypoints"]["unapproved_m0"] = relative
    elif mutation == "smoke":
        mutant["import_smoke"]["package_modules"].append(relative[:-3].replace("/", "."))
    else:
        mutant["manual_execution_capability"]["entrypoints"]["unapproved_m0"] = relative
    with pytest.raises(AssertionError):
        _assert_m0_measurement_tool_delivery(mutant, relative)
    _assert_m0_measurement_tool_delivery(definition, relative)

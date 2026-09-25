"""Packaging invariants for ops/windows/reboot/bridge-code-files.json.

The reboot bundle delivers two kinds of file by two different mechanisms, and
the difference is why new work silently fails to ship:

* ``.agent-bridge/bin/*.ps1`` is picked up by DIRECTORY ENUMERATION, so a new
  PowerShell helper ships the moment it is committed.
* ``tools/*.py`` and ``waggledance/**/*.py`` ship only if they appear in the
  explicit ``python_files`` allow-list in ``bridge-code-files.json``.

These tests make the Python side fail loudly instead of quietly.

WHAT THEY DO NOT PROVE. That a path exists in the repository says nothing about
whether the module imports once installed under the bundle's isolated
PYTHONPATH. Only the deployment-time ``import_smoke`` run proves that. These
tests check the manifest against the source tree and nothing further.

WHY THE PARSING IS SPLIT FROM THE FILESYSTEM. Every helper that interprets
source or installer text takes TEXT, so each fail-open mode below is
reproducible in a unit test rather than only against the live tree. The first
version of this file had four such modes and all four were invisible precisely
because the parsing could not be exercised directly.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_ROOT = REPO_ROOT / "ops" / "windows" / "reboot"
DEFINITION = OPS_ROOT / "bridge-code-files.json"
OBSERVER_INSTALLER = OPS_ROOT / "Install-WdCapacityObserver.ps1"
INTERNAL_ROOTS = ("tools", "waggledance")


# --- manifest ------------------------------------------------------------------


def _definition() -> dict:
    return json.loads(DEFINITION.read_text(encoding="utf-8"))


def _packaged_python() -> list[str]:
    return [entry for entry in _definition()["python_files"] if entry.endswith(".py")]


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


def _top_level_names(relative: str) -> set[str] | None:
    """Names a module binds at top level, or None if a star-import hides them."""
    tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None          # cannot know; caller stays conservative
                names.add(alias.asname or alias.name.split(".")[0])
    return names


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
            if kind == "symbol":
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


# --- the second delivery surface, parsed from the actual array ------------------
#
# The capacity modules are NOT in the reboot bundle. Install-WdCapacityObserver.ps1
# carries its own list. That is by design; this section keeps the design true.

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

"""Packaging invariants for ops/windows/reboot/bridge-code-files.json.

The reboot bundle delivers two kinds of file by two different mechanisms, and
the difference is the reason new work has silently failed to ship:

* ``.agent-bridge/bin/*.ps1`` is picked up by DIRECTORY ENUMERATION, so a new
  PowerShell helper ships the moment it is committed.
* ``tools/*.py`` and ``waggledance/**/*.py`` ship only if they appear in the
  explicit ``python_files`` allow-list in ``bridge-code-files.json``.

So every new PowerShell helper has shipped without anyone thinking about it,
and every new Python module has not shipped without anyone noticing. These
tests make the Python side fail loudly instead of quietly.

They deliberately assert only what the repository can know. Whether a dormant
library *should* be delivered is a release decision; whether a delivered module
can actually import is not.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFINITION = REPO_ROOT / "ops" / "windows" / "reboot" / "bridge-code-files.json"
INTERNAL_ROOTS = ("tools", "waggledance")


def _definition() -> dict:
    return json.loads(DEFINITION.read_text(encoding="utf-8"))


def _packaged_python() -> list[str]:
    return [entry for entry in _definition()["python_files"] if entry.endswith(".py")]


def _internal_imports(relative: str) -> set[str]:
    """Dotted internal modules imported by one repository file."""
    tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in INTERNAL_ROOTS:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue                       # relative import, same package
            if node.module and node.module.split(".")[0] in INTERNAL_ROOTS:
                found.add(node.module)
    return found


def _module_path(dotted: str) -> str | None:
    """The repository path a dotted internal module resolves to, if any."""
    base = dotted.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (REPO_ROOT / candidate).is_file():
            return candidate
    return None


def test_every_packaged_python_file_exists():
    """A stale or mistyped entry would fail at deploy time, not here."""
    missing = [entry for entry in _packaged_python()
               if not (REPO_ROOT / entry).is_file()]
    assert not missing, f"listed for packaging but absent from the repo: {missing}"


def test_the_packaged_python_set_is_import_closed():
    """Nothing packaged may import something unpackaged.

    This is the invariant a manual audit keeps having to rediscover. A module
    that ships without the module it imports raises ImportError on the target
    machine, where nobody is watching, and the failure looks like the caller's
    fault. Checked transitively, because a two-step chain hides just as well.
    """
    packaged = set(_packaged_python())
    pending = list(packaged)
    seen = set(packaged)
    gaps: dict[str, set[str]] = {}
    while pending:
        relative = pending.pop()
        for dotted in sorted(_internal_imports(relative)):
            target = _module_path(dotted)
            if target is None or target in packaged:
                continue
            gaps.setdefault(target, set()).add(relative)
            if target not in seen:
                seen.add(target)
                pending.append(target)
    assert not gaps, (
        "packaged modules import these unpackaged modules: "
        + "; ".join(f"{target} <- {sorted(importers)}"
                    for target, importers in sorted(gaps.items())))


def test_the_import_smoke_list_only_names_packaged_modules():
    """A smoke check on an unpackaged module would fail on the target machine."""
    packaged = set(_packaged_python())
    unpackaged = []
    for dotted in _definition()["import_smoke"]["package_modules"]:
        target = _module_path(dotted)
        if target is None or target not in packaged:
            unpackaged.append(dotted)
    assert not unpackaged, f"import_smoke names unpackaged modules: {unpackaged}"


def test_every_entrypoint_is_packaged():
    packaged = set(_packaged_python())
    missing = {name: path
               for name, path in _definition()["python_entrypoints"].items()
               if path not in packaged}
    assert not missing, f"entrypoints that would not be delivered: {missing}"


# --- the second delivery surface ----------------------------------------------
#
# The capacity modules are NOT in the reboot bundle. They are installed by
# Install-WdCapacityObserver.ps1, which carries its own file list. That is by
# design, and this test exists so the design stays true: a module invoked by an
# ops script must be delivered by SOME installer, not merely exist in the repo.

OPS_ROOT = REPO_ROOT / "ops" / "windows" / "reboot"
OBSERVER_INSTALLER = OPS_ROOT / "Install-WdCapacityObserver.ps1"


#: Matched directly rather than by splitting on quotes. Quote-parity parsing
#: broke on a single apostrophe elsewhere in the file and silently produced an
#: EMPTY payload, which would have made the delivery test pass against nothing.
#: The non-vacuity guard below exists because that is exactly what happened.
#:
#: Separators are normalised BEFORE matching, so the pattern needs no backslash
#: at all. PowerShell writes tools\name.py while the manifest writes tools/name.py,
#: and a pattern carrying an escaped separator is one shell hop away from
#: silently matching nothing.
_MODULE_REFERENCE = re.compile(r"tools/[A-Za-z0-9_]+\.py")


def _module_references(text: str) -> set[str]:
    return set(_MODULE_REFERENCE.findall(text.replace("\\", "/")))


def _observer_payload() -> set[str]:
    return _module_references(OBSERVER_INSTALLER.read_text(encoding="utf-8"))


def _ops_referenced_modules() -> dict[str, set[str]]:
    """Every tools/*.py an ops launcher names, and which scripts name it."""
    referenced: dict[str, set[str]] = {}
    for script in sorted(OPS_ROOT.glob("*.ps1")):
        if script.name == OBSERVER_INSTALLER.name:
            continue                  # its list IS a delivery manifest, not a call
        text = script.read_text(encoding="utf-8", errors="replace")
        for candidate in _module_references(text):
            referenced.setdefault(candidate, set()).add(script.name)
    return referenced


def test_every_module_an_ops_script_invokes_is_delivered_by_some_installer():
    delivered = set(_packaged_python()) | _observer_payload()
    referenced = _ops_referenced_modules()
    undelivered = {module: sorted(scripts)
                   for module, scripts in referenced.items()
                   if module not in delivered and (REPO_ROOT / module).is_file()}
    assert not undelivered, (
        "ops scripts invoke modules no installer delivers: " + repr(undelivered))


def test_the_ops_reference_scan_is_not_vacuous():
    """Guards the delivery test above against parsing nothing.

    A delivery test whose input set is empty passes for the worst reason
    available, and the observer-payload parser did exactly that on its first
    run, so this pins both ends of the comparison.
    """
    referenced = _ops_referenced_modules()
    assert len(referenced) >= 5, f"ops reference scan found only {sorted(referenced)}"
    assert "tools/bridge_next_action.py" in referenced,         "the routing entrypoint must be discoverable, or the scan is broken"


def test_the_observer_installer_still_carries_the_capacity_modules():
    """Non-vacuity guard for the test above.

    If the observer payload were ever parsed as empty, the closure test would
    pass by accident while the capacity surface shipped nothing.
    """
    payload = _observer_payload()
    assert len(payload) >= 5, f"observer payload parsed as {sorted(payload)}"
    assert "tools/bridge_capacity_collector.py" in payload


@pytest.mark.parametrize("module", sorted(_observer_payload()))
def test_observer_payload_modules_exist(module):
    assert (REPO_ROOT / module).is_file(), f"observer ships a missing file: {module}"

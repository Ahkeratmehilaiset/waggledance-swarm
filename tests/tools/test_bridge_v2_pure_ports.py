"""Bridge v2 pure ports: present, standard-library only, and dormant (Lead 0578016d).

Authored under the 2026-09-29 operator no-runs directive: NOT executed by the author.
The four tools-owned ports are exact source copies of 08825c1b, except the registry port's default root
(parents[1] in tools/, Tools c013708e). The kernel fixture's behaviour parity SKIPS when waggledance.core
cannot be imported; these pins never skip.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTS = ("bridge_v2_request_contract", "bridge_v2_identity_registry", "bridge_v2_log_reader", "bridge_v2_workflow")
STDLIB = {"__future__", "copy", "ctypes", "dataclasses", "datetime", "enum", "json", "math", "msvcrt", "os",
          "pathlib", "re", "typing", "uuid"}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


@pytest.mark.parametrize("name", PORTS)
def test_each_port_imports_only_the_standard_library_or_the_request_contract_port(name):
    imported = _imports(ROOT / "tools" / f"{name}.py")   # a missing port fails here, never skips
    assert imported, name
    for module in imported:
        assert module.split(".")[0] in STDLIB or module == "tools.bridge_v2_request_contract", (name, module)


def test_no_runtime_module_names_a_port():
    # Dormant: nothing under tools/ or waggledance/ except the ports themselves names a port.
    paths = [*(ROOT / "tools").rglob("*.py"), *(ROOT / "waggledance").rglob("*.py")]
    for path in paths:
        if path.parent == ROOT / "tools" and path.stem in PORTS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in PORTS:
            assert name not in text, (path.relative_to(ROOT).as_posix(), name)
    # The twin: the same scan finds the one real reference, so it is not vacuous.
    assert "bridge_v2_request_contract" in (ROOT / "tools" / "bridge_v2_workflow.py").read_text(encoding="utf-8")


def test_the_workflow_port_reuses_the_request_contract_port_objects():
    workflow = importlib.import_module("tools.bridge_v2_workflow")
    contract = importlib.import_module("tools.bridge_v2_request_contract")
    assert workflow.reply_matches_request is contract.reply_matches_request
    assert workflow.timestamp is contract.timestamp


# --- Tools c013708e: the relocated registry port reads ITS OWN repo's configs file ---------------------

REGISTRY_FILE = ROOT / "configs" / "bridge_identity_registry.json"


def test_the_registry_default_is_this_repos_configs_file_never_its_parent():
    """The port lives in tools/, so parents[1] of it is this repo; the core copy's parents[2] would name the
    repo's PARENT here. The default is never the working directory or an installed pointer."""
    registry = importlib.import_module("tools.bridge_v2_identity_registry")
    assert registry.DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH == REGISTRY_FILE
    assert registry.DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH != ROOT.parent / "configs" / "bridge_identity_registry.json"
    assert REGISTRY_FILE.is_file()                                    # the twin: this repo's registry is there
    assert "codex-lead-1" in registry.load_bridge_identity_registry()   # and the default load finds it


@pytest.mark.parametrize("content, outcome", [
    ('{"identities": {"claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103"}}', "valid"),
    (None, "missing"),
    ("not json", "malformed"),
])
def test_the_default_load_is_isolated_valid_missing_or_malformed(tmp_path, monkeypatch, content, outcome):
    """The default is read at call time, so pointing it at a tmp file isolates the default path itself."""
    registry = importlib.import_module("tools.bridge_v2_identity_registry")
    default = tmp_path / "configs" / "bridge_identity_registry.json"
    if content is not None:
        default.parent.mkdir()
        default.write_text(content, encoding="utf-8")
    monkeypatch.setattr(registry, "DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH", default)
    if outcome == "valid":
        assert registry.load_bridge_identity_registry() == {"claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103"}
    elif outcome == "missing":
        with pytest.raises(ValueError, match="not found"):
            registry.load_bridge_identity_registry()
        assert registry.load_bridge_identity_registry(allow_missing=True) == {}
    else:
        with pytest.raises(ValueError, match="invalid JSON"):
            registry.load_bridge_identity_registry()

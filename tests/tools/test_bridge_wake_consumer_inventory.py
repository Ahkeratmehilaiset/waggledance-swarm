"""Repository-wide static wake-consumer inventory, not behavioral acceptance.

Conservative: named PS references (including indirect helpers) and Python
imports/literal module references require an explicit inventory entry. This
does not prove absence of computed names, reflection or external consumers.
Each listed consumer still requires its own real execution acceptance.
"""
from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXTENSIONS = {".ps1", ".psm1", ".psd1", ".ps1xml", ".py", ".pyi",
              ".cmd", ".bat", ".sh", ".yaml", ".yml", ".json"}
MEASURED_FILTERS = {
    ".agent-bridge/bin/Watch-Bridge.ps1",
    ".agent-bridge/bin/Monitor-AgentBridge.ps1",
}
UNMEASURED = {"ops/windows/reboot/Get-WdSwarmParallelStatus.ps1"}
# This is an internal predicate caller, not a measured delivery consumer.
INTERNAL_IMPLEMENTATIONS = {".agent-bridge/bin/BridgeEventClassifier.ps1"}
PS_NAMES = (
    "Test-BridgeWakeEligible", "Get-BridgeWakeClass", "Test-IsTargeted",
    "Test-SubstantiveMonitorEvent",
    "Test-BridgeInformationalNoticeSuppressible",
)
PY_REFERENCE = re.compile(r"(?<![a-z0-9_])bridge_wake_class(?![a-z0-9_])", re.I)
PS_REFERENCE = re.compile(
    r"(?<![a-z0-9_-])(?:" + "|".join(map(re.escape, PS_NAMES))
    + r")(?![a-z0-9_-])", re.IGNORECASE,
)
PS_DECLARATION = re.compile(
    r"(?im)^\s*function\s+(?:(?:global|script|local):)?(?:"
    + "|".join(map(re.escape, PS_NAMES)) + r")(?![a-z0-9_-])",
)


def python_reference(text: str) -> bool:
    """Recognize imports, aliases and literal dynamic imports, not execution."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any("bridge_wake_class" in a.name.split(".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if ("bridge_wake_class" in (node.module or "").split(".")
                    or any(a.name == "bridge_wake_class" for a in node.names)):
                return True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in {"bridge_wake_class", "tools.bridge_wake_class"}:
                return True
    return False


def discover(sources: dict[str, str]) -> set[str]:
    consumers = set()
    for name, text in sources.items():
        path = PurePosixPath(name)
        if path.parts[0] in {"tests", "docs"} or path.suffix.lower() not in EXTENSIONS:
            continue
        without_declarations = PS_DECLARATION.sub("", text)
        # Ignore full-line comments; other references are conservative hits.
        scan = "\n".join(line for line in without_declarations.splitlines()
                         if not line.lstrip().startswith("#"))
        if PS_REFERENCE.search(scan):
            consumers.add(name)
        elif name != "tools/bridge_wake_class.py":
            if PY_REFERENCE.search(scan):
                consumers.add(name)
            elif path.suffix.lower() in {".py", ".pyi"}:
                try:
                    if python_reference(text):
                        consumers.add(name)
                except SyntaxError as exc:
                    raise AssertionError(f"Cannot inventory invalid Python: {name}: {exc.msg}") from exc
    return consumers


def assert_inventory(sources: dict[str, str]) -> None:
    assert discover(sources) == MEASURED_FILTERS | UNMEASURED | INTERNAL_IMPLEMENTATIONS


def read_source(path: Path) -> str:
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        text = raw.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"Cannot inventory source encoding: {path}") from exc
    if "\x00" in text:
        raise AssertionError(f"Cannot inventory NUL-containing source: {path}")
    return text


def repository_sources() -> dict[str, str]:
    # Include newly added/untracked source too: an unstaged consumer must fail.
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"], check=True, stdout=subprocess.PIPE,
    ).stdout
    paths = {p.decode("utf-8", errors="strict") for p in raw.split(b"\0") if p}
    return {
        p: read_source(ROOT / p)
        for p in sorted(paths)
        if PurePosixPath(p).parts[0] not in {"tests", "docs"}
        and PurePosixPath(p).suffix.lower() in EXTENSIONS
    }


def test_repository_inventory_has_no_unreviewed_consumer():
    assert_inventory(repository_sources())


@pytest.mark.parametrize("directory", ["ops", "tools", "waggledance", "scripts", "new_root"])
@pytest.mark.parametrize("call", PS_NAMES)
def test_new_ps_consumer_in_any_directory_refuses(directory, call):
    baseline = {p: "Test-BridgeWakeEligible $ev"
                for p in MEASURED_FILTERS | UNMEASURED | INTERNAL_IMPLEMENTATIONS}
    assert_inventory(baseline)
    name = f"{directory}/new_consumer.ps1"
    baseline[name] = f"if ({call} $ev) {{ 'wake' }}"
    assert name in discover(baseline)
    with pytest.raises(AssertionError):
        assert_inventory(baseline)


@pytest.mark.parametrize("source", [
    "import tools.bridge_wake_class as wc",
    "import bridge_wake_class",
    "from tools.bridge_wake_class import classify as route",
    "from tools import bridge_wake_class as wc",
    "from .bridge_wake_class import classify",
    "from . import bridge_wake_class",
    "import importlib\nwc = importlib.import_module('tools.bridge_wake_class')",
    "wc = __import__('bridge_wake_class')",
    "script = 'Test-BridgeWakeEligible $ev'",
])
def test_python_and_embedded_ps_consumers_are_visible(source):
    assert discover({"tools/new_consumer.py": source}) == {"tools/new_consumer.py"}


def test_case_and_indirect_references_are_conservative():
    assert discover({"scripts/x.PS1": "& 'get-bridgewakeclass' -Event $e"}) == {"scripts/x.PS1"}


def test_declarations_do_not_hide_same_line_calls():
    assert discover({"ops/x.ps1": "function Test-IsTargeted { Get-BridgeWakeClass $e }"}) == {"ops/x.ps1"}
    assert discover({"ops/x.ps1": "function Test-IsTargeted { return $false }"}) == set()


def test_tests_and_non_source_are_excluded_not_production_fixtures():
    assert discover({"tests/fixtures/x.ps1": "Get-BridgeWakeClass $e",
                     "docs/example.md": "Get-BridgeWakeClass $e"}) == set()
    assert discover({"tools/fixtures/x.ps1": "Get-BridgeWakeClass $e"}) == {"tools/fixtures/x.ps1"}


def test_unmeasured_is_not_misrepresented_as_measured():
    assert MEASURED_FILTERS.isdisjoint(UNMEASURED)
    assert MEASURED_FILTERS.isdisjoint(INTERNAL_IMPLEMENTATIONS)
    assert UNMEASURED == {"ops/windows/reboot/Get-WdSwarmParallelStatus.ps1"}


@pytest.mark.parametrize("extension", sorted(EXTENSIONS))
@pytest.mark.parametrize("source", [
    "python -m tools.bridge_wake_class",
    "runpy.run_path('tools/bridge_wake_class.py')",
    "importlib.util.spec_from_file_location('wc', 'tools/bridge_wake_class.py')",
    "tools.bridge_wake_class.classify(event, target)",
    "importlib.import_module('.bridge_wake_class', 'tools')",
    "Join-Path $root 'tools/bridge_wake_class.py'",
    "Test-BridgeInformationalNoticeSuppressible $event",
])
def test_literal_cross_language_consumers(extension, source):
    name = "new_root/caller" + extension
    assert discover({name: source}) == {name}


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be"])
def test_bom_encoded_sources_are_scanned(tmp_path, encoding):
    path = tmp_path / "caller.ps1"
    raw = "Test-BridgeWakeEligible $e".encode(encoding)
    if encoding == "utf-16-be":
        raw = b"\xfe\xff" + raw
    path.write_bytes(raw)
    assert discover({"ops/caller.ps1": read_source(path)}) == {"ops/caller.ps1"}


def test_unreadable_sources_fail_closed_with_path(tmp_path):
    path = tmp_path / "bad.ps1"
    path.write_bytes(b"\x80")
    with pytest.raises(AssertionError, match="bad.ps1"):
        read_source(path)
    with pytest.raises(AssertionError, match="ops/bad.py"):
        discover({"ops/bad.py": "def broken("})


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32", "utf-32-be"])
def test_nul_encoded_sources_cannot_silently_hide_calls(tmp_path, encoding):
    path = tmp_path / "hidden-caller.ps1"
    path.write_bytes("Test-BridgeWakeEligible $e".encode(encoding))
    with pytest.raises(AssertionError, match="hidden-caller.ps1"):
        read_source(path)


def test_utf8_with_literal_nul_is_rejected(tmp_path):
    path = tmp_path / "nul.ps1"
    path.write_bytes(b"Test-Bridge\x00WakeEligible $e")
    with pytest.raises(AssertionError, match="nul.ps1"):
        read_source(path)


def test_module_self_reference_is_not_external_consumer():
    assert discover({"tools/bridge_wake_class.py": '"tools/bridge_wake_class.py"'}) == set()
    assert discover({"tools/bridge_wake_class.py": "Test-BridgeWakeEligible $e"}) == {"tools/bridge_wake_class.py"}

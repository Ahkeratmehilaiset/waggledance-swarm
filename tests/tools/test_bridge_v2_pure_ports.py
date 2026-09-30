"""Bridge v2 pure ports: present, standard-library only, and dormant (Lead 0578016d).

Authored under the 2026-09-29 operator no-runs directive: NOT executed by the author.
The four tools-owned ports are exact source copies of 08825c1b, except the registry port's default root
(parents[1] in tools/, Tools c013708e) and the log reader's pointer-width CloseHandle signature (fable-5 e004
review N1). The kernel fixture's behaviour parity SKIPS when waggledance.core cannot be imported; these pins
never skip.
"""
from __future__ import annotations

import ast
import ctypes
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

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


# --- fable-5 e004 review N1: a still-owned handle closes through a private, pointer-width signature --------
# Synthetic and OS-free: fake ctypes/wintypes/msvcrt/os objects, and a Path that is never opened.

WIDE_HANDLE = (1 << 40) | 0x1A4               # above every C int, still a valid pointer-width HANDLE value
BARE_CALL_INTS = range(-(1 << 31), 1 << 32)   # what a bare Windows ctypes call converts (C long, else unsigned)
WINTYPES = SimpleNamespace(**{t: f"wintypes.{t}" for t in ("LPCWSTR", "DWORD", "LPVOID", "HANDLE", "BOOL")})


class _Function:
    """A fake ctypes function object: settable argtypes/restype, each call recorded with its signature."""

    def __init__(self, result):
        self.argtypes = self.restype = None
        self.result, self.calls = result, []

    def __call__(self, *args):
        self.calls.append((args, self.argtypes, self.restype))
        if self.argtypes is None and any(isinstance(arg, int) and arg not in BARE_CALL_INTS for arg in args):
            raise ctypes.ArgumentError("argument 1: OverflowError: int too long to convert")   # as a bare call
        return self.result


class _Kernel32:
    """One PRIVATE WinDLL instance: its function objects belong to it alone, never to a shared cache."""

    def __init__(self, closed):
        self.CreateFileW, self.CloseHandle = _Function(WIDE_HANDLE), _Function(closed)


def _nt_fakes(monkeypatch, primary, closed=1):
    reader = importlib.import_module("tools.bridge_v2_log_reader")
    loaded, handed, opened = [], [], []

    def win_dll(name, use_last_error=False):
        loaded.append((name, use_last_error, _Kernel32(closed)))
        return loaded[-1][2]

    def open_osfhandle(handle, flags):
        handed.append((handle, flags))
        if primary is not None:
            raise primary
        return 7

    def fdopen(descriptor, mode, buffering=-1):
        opened.append((descriptor, mode, buffering))
        return "stream"

    # No windll attribute: a shared-cache regression fails with AttributeError instead of passing.
    fake_ctypes = SimpleNamespace(WinDLL=win_dll, c_void_p=ctypes.c_void_p, WinError=OSError,
                                  get_last_error=lambda: 0)
    monkeypatch.setattr(reader, "ctypes", fake_ctypes)
    monkeypatch.setattr(reader, "wintypes", WINTYPES, raising=False)   # the module has no wintypes off Windows
    monkeypatch.setattr(reader, "os", SimpleNamespace(name="nt", O_RDONLY=0, fdopen=fdopen))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=open_osfhandle))
    return reader, loaded, handed, opened


@pytest.mark.parametrize("closed", [1, 0], ids=["closed", "close_failed"])
@pytest.mark.parametrize("make_primary", [lambda: OSError(9, "Bad file descriptor"), KeyboardInterrupt],
                         ids=["oserror", "interrupt"])
def test_a_failed_handoff_closes_the_wide_handle_and_reraises_the_primary(monkeypatch, make_primary, closed):
    primary = make_primary()
    reader, loaded, handed, opened = _nt_fakes(monkeypatch, primary, closed)
    with pytest.raises(type(primary)) as raised:
        reader._open_log(Path("events.jsonl"))
    assert raised.value is primary and primary.__context__ is None   # never replaced, never chained
    assert handed == [(WIDE_HANDLE, 0)] and opened == []
    assert [(name, flag) for name, flag, _ in loaded] == [("kernel32", True), ("kernel32", True)]
    creator, closer = (kernel32 for _, _, kernel32 in loaded)
    assert creator is not closer and creator.CloseHandle.calls == [] and closer.CreateFileW.calls == []
    # Once, with the full wide value, under the pointer-width signature (a bare call raises ArgumentError).
    assert closer.CloseHandle.calls == [((WIDE_HANDLE,), (WINTYPES.HANDLE,), WINTYPES.BOOL)]


def test_a_successful_handoff_moves_the_wide_handle_to_the_descriptor(monkeypatch):
    reader, loaded, handed, opened = _nt_fakes(monkeypatch, None)
    assert reader._open_log(Path("events.jsonl")) == "stream"
    assert handed == [(WIDE_HANDLE, 0)] and opened == [(7, "rb", 0)]
    assert len(loaded) == 1 and loaded[0][2].CloseHandle.calls == []   # the descriptor owns it now
    (args, _, restype), = loaded[0][2].CreateFileW.calls
    assert args[0] == str(Path("events.jsonl")) and restype == WINTYPES.HANDLE

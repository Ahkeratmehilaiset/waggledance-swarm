"""Bridge v2 log reader: ownership of the Windows handle and the CRT descriptor in _open_log (RCO1 096e L1/L2).

Authored under the operator no-runs directive: NOT executed by the author. Synthetic and OS-free: fake ctypes,
wintypes, msvcrt and os objects and a Path that is never opened. RCO2's pure-ports fixture is not touched.
"""
from __future__ import annotations

import ctypes
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HANDLE = (1 << 40) | 0x2B8                       # a wide, still valid pointer-width HANDLE value
DESCRIPTOR = 7
WINTYPES = SimpleNamespace(**{t: f"wintypes.{t}" for t in ("LPCWSTR", "DWORD", "LPVOID", "HANDLE", "BOOL")})


class _Function:
    """A fake ctypes function object: settable argtypes/restype, every call recorded."""

    def __init__(self, result):
        self.argtypes = self.restype = None
        self.result, self.calls = result, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def _fakes(monkeypatch, *, handoff=None, fdopen=None, close=None, close_handle=1, load=None):
    """handoff, fdopen, close: an exception instance raised there (None succeeds). close_handle: the BOOL that
    CloseHandle returns. load: an exception instance raised by the SECOND (cleanup) WinDLL load."""
    reader = importlib.import_module("tools.bridge_v2_log_reader")
    log = {"loads": 0, "handed": [], "opened": [], "closed": []}
    creator, closer = _Function(HANDLE), _Function(close_handle)

    def win_dll(name, use_last_error=False):
        log["loads"] += 1
        if log["loads"] == 1:
            return SimpleNamespace(CreateFileW=creator)
        if load is not None:
            raise load
        return SimpleNamespace(CloseHandle=closer)

    def open_osfhandle(handle, flags):
        log["handed"].append(handle)
        if handoff is not None:
            raise handoff
        return DESCRIPTOR

    def fake_fdopen(descriptor, mode, buffering=-1):
        log["opened"].append(descriptor)
        if fdopen is not None:
            raise fdopen
        return "stream"

    def fake_close(descriptor):
        log["closed"].append(descriptor)
        if close is not None:
            raise close

    monkeypatch.setattr(reader, "ctypes", SimpleNamespace(WinDLL=win_dll, c_void_p=ctypes.c_void_p,
                                                           WinError=lambda code=0: OSError(code, "win32 error"),
                                                           get_last_error=lambda: 6))
    monkeypatch.setattr(reader, "wintypes", WINTYPES, raising=False)   # the module has no wintypes off Windows
    monkeypatch.setattr(reader, "os", SimpleNamespace(name="nt", O_RDONLY=0, fdopen=fake_fdopen, close=fake_close))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=open_osfhandle))
    return reader, log, closer


def test_a_failed_fdopen_closes_the_descriptor_once_and_reraises_the_primary(monkeypatch):
    primary = OSError(22, "fdopen failed")
    reader, log, closer = _fakes(monkeypatch, fdopen=primary)
    with pytest.raises(OSError) as raised:
        reader._open_log(Path("events.jsonl"))
    assert raised.value is primary and getattr(primary, "__notes__", []) == []   # a clean cleanup adds no note
    assert log["closed"] == [DESCRIPTOR]                 # was never closed: the descriptor (and its handle) leaked
    assert closer.calls == [] and log["loads"] == 1      # the descriptor owns the handle: never a double close


def test_a_failing_descriptor_close_never_replaces_the_primary_and_is_recorded(monkeypatch):
    primary = OSError(22, "fdopen failed")
    reader, log, _ = _fakes(monkeypatch, fdopen=primary, close=OSError(9, "bad descriptor"))
    with pytest.raises(OSError) as raised:
        reader._open_log(Path("events.jsonl"))
    assert raised.value is primary and primary.__notes__ == ["os.close cleanup failed: OSError"]
    assert log["closed"] == [DESCRIPTOR]


def test_a_clean_handle_cleanup_after_a_failed_handoff_adds_no_note(monkeypatch):
    primary = OSError(24, "too many descriptors")
    reader, log, closer = _fakes(monkeypatch, handoff=primary)
    with pytest.raises(OSError) as raised:
        reader._open_log(Path("events.jsonl"))
    assert raised.value is primary and getattr(primary, "__notes__", []) == []
    assert closer.calls == [(HANDLE,)] and closer.argtypes == (WINTYPES.HANDLE,) and closer.restype == WINTYPES.BOOL
    assert log["opened"] == [] and log["closed"] == [] and log["loads"] == 2


@pytest.mark.parametrize("load,result", [(None, 0), (OSError(126, "module load failed"), 1)],
                         ids=["close_returns_false", "cleanup_load_fails"])
def test_a_failing_handle_cleanup_never_replaces_the_handoff_primary(monkeypatch, load, result):
    primary = OSError(24, "too many descriptors")
    reader, log, closer = _fakes(monkeypatch, handoff=primary, close_handle=result, load=load)
    with pytest.raises(OSError) as raised:
        reader._open_log(Path("events.jsonl"))
    assert raised.value is primary                       # was the load error, or a silently ignored FALSE close
    assert primary.__notes__ == ["CloseHandle cleanup failed: OSError"]   # recorded, never hidden as success
    assert closer.calls == ([] if load is not None else [(HANDLE,)]) and log["opened"] == []


def test_a_base_exception_from_cleanup_propagates_with_the_primary_as_context(monkeypatch):
    primary, interrupt = OSError(22, "fdopen failed"), KeyboardInterrupt()
    reader, log, _ = _fakes(monkeypatch, fdopen=primary, close=interrupt)
    with pytest.raises(KeyboardInterrupt) as raised:
        reader._open_log(Path("events.jsonl"))
    assert raised.value is interrupt and interrupt.__context__ is primary   # the documented policy: never swallowed
    assert log["closed"] == [DESCRIPTOR]


def test_a_successful_open_keeps_both_resources_and_closes_nothing(monkeypatch):
    reader, log, closer = _fakes(monkeypatch)
    assert reader._open_log(Path("events.jsonl")) == "stream"
    assert log["handed"] == [HANDLE] and log["opened"] == [DESCRIPTOR]
    assert log["closed"] == [] and closer.calls == [] and log["loads"] == 1
